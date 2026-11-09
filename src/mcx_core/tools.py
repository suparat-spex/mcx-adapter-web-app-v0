"""
tools.py - MCP tools: definitions, input schemas, curated queries, the answer envelope. No Qt.

Two kinds - never one tool per tag, never free SQL or free protocol commands:
  generic   browse_model, list_assets, read_tag, query_history - over the ISA-95 model of every
            tag source (built in, same for every adapter)
  curated   one per business question (get_oee, get_energy_kwh ...), built from a SQL view / table
            / sheet with typed parameters: filters (column op value), an optional group_by enum
            and aggregated measures

Every result uses one envelope (`envelope`), so any LLM can read and cite it:
  {tool, rows, units, context {isa95, tz}, quality, source {adapter, object, rows_read}, as_of,
   truncated, note?}
"""
from __future__ import annotations

import datetime as dt

from mcx_core.adapters.base import Query
from mcx_core.config import Measure, ToolDef, ToolParam
from mcx_core.safety import SafetyError, check_range, coerce, parse_time

AGGS = ("avg", "min", "max", "sum", "count")
INTERVALS = {"raw": 0, "1m": 60, "5m": 300, "15m": 900, "1h": 3600, "1d": 86400}

GENERIC = {
    "browse_model": dict(
        title="Browse the plant model", risk="R0",
        description="List the children of an ISA-95 path (site / area / line / machine) and the tags "
                    "at that level, with units and limits. Start with no path to see the sites. Use it to "
                    "find exact tag paths before read_tag or query_history. Does not return values.",
        params=[ToolParam("path", "string", "ISA-95 path, e.g. Bangkok/Assembly/L2 (empty = top)")]),
    "list_assets": dict(
        title="List plant assets", risk="R0",
        description="List the machines / units under a scope with how many tags each has. Use it to answer "
                    "'which machines are there' questions. Not for values.",
        params=[ToolParam("scope", "string", "ISA-95 path to list under (empty = all)")]),
    "read_tag": dict(
        title="Read current values", risk="R1",
        description="Read the current value of a tag, or of every tag of a machine, by ISA-95 path. Each value "
                    "has its unit, timestamp and quality (good / uncertain / bad). For trends over time use "
                    "query_history instead.",
        params=[ToolParam("path", "string", "Tag path (Bangkok/Assembly/L2/Press1/temperature) or a machine "
                                            "path for all its tags", required=True)]),
    "query_history": dict(
        title="Tag history", risk="R1",
        description="Values of one tag over a time range, aggregated on the server (avg / min / max per "
                    "interval). Times are ISO 8601 in plant time. The range is limited (max days per call); "
                    "for long ranges use a larger interval.",
        params=[ToolParam("path", "string", "Tag path", required=True),
                ToolParam("from", "datetime", "Start, ISO 8601 (plant time if no offset)", required=True),
                ToolParam("to", "datetime", "End, ISO 8601", required=True),
                ToolParam("agg", "enum", "Aggregate per interval", values=["avg", "min", "max", "raw"],
                          default="avg"),
                ToolParam("interval", "enum", "Interval", values=list(INTERVALS), default="15m")]),
}


def generic_tool_def(name: str, profiles: list) -> ToolDef:
    g = GENERIC[name]
    t = ToolDef(name=name, kind="generic", title=g["title"], description=g["description"], risk=g["risk"],
                params=[ToolParam(**vars(p)) for p in g["params"]], profiles=list(profiles))
    return t


# ----------------------------------------------------------------------- schemas
def param_schema(p: ToolParam) -> dict:
    s: dict = {"description": p.description} if p.description else {}
    if p.type == "enum" or p.group:
        s.update(type="string", enum=list(p.values or p.group.keys()))
    elif p.type in ("date", "datetime"):
        s.update(type="string", format="date" if p.type == "date" else "date-time")
    elif p.type in ("number", "integer", "boolean"):
        s["type"] = p.type
    else:
        s["type"] = "string"
        if p.values:
            s["enum"] = list(p.values)
    if p.default is not None:
        s["default"] = p.default
    return s


def input_schema(t: ToolDef) -> dict:
    return {"type": "object", "properties": {p.name: param_schema(p) for p in t.params},
            "required": [p.name for p in t.params if p.required], "additionalProperties": False}


def signature(t: ToolDef) -> str:
    """get_oee(line, date_from, date_to, group_by="shift") - for previews."""
    parts = []
    for p in t.params:
        parts.append(f'{p.name}="{p.default}"' if p.default is not None and not p.required else p.name)
    return f"{t.name}({', '.join(parts)})"


def full_description(t: ToolDef, limits, tz: str) -> str:
    """The description the AI sees: the author's text + units, limits and the answer format."""
    d = t.description.strip()
    units = ", ".join(f"{m.out} [{m.unit}]" for m in t.measures if m.unit)
    extra = []
    if units:
        extra.append(f"Units: {units}.")
    if t.kind == "curated" and t.date_column:
        extra.append(f"Max {limits.max_days} days per call.")
    extra.append(f"Times in plant time ({tz}). Read-only; answers carry unit, time, ISA-95 path and source, "
                 f"max {limits.max_rows} rows (truncated: true when capped).")
    if t.risk == "R2":
        extra.append("SENSITIVE: may contain personal or cost data.")
    return (d + "\n\n" + " ".join(extra)).strip()


# ----------------------------------------------------------------------- arguments
def check_args(t: ToolDef, args: dict) -> dict:
    """Typed, known arguments only; defaults filled in."""
    args = dict(args or {})
    known = {p.name: p for p in t.params}
    extra = set(args) - set(known)
    if extra:
        raise SafetyError(f"unknown argument(s): {', '.join(sorted(extra))}", "bad_argument")
    out = {}
    for p in t.params:
        v = args.get(p.name, p.default)
        if v in (None, "") and p.required:
            raise SafetyError(f"{p.name} is required", "bad_argument")
        if v in (None, ""):
            continue
        if p.group:
            if v not in p.group:
                raise SafetyError(f"{p.name} must be one of {', '.join(p.group)}", "bad_argument")
            out[p.name] = v
        else:
            out[p.name] = coerce(p, v)
    return out


# ----------------------------------------------------------------------- curated
def curated_query(t: ToolDef, args: dict, obj, limits, tz) -> Query:
    q = Query(limit=limits.max_rows)
    lo = hi = None
    for p in t.params:
        v = args.get(p.name)
        if v is None or p.group:
            continue
        if not p.column:
            continue
        q.where.append((p.column, p.op, v))
        if p.type in ("date", "datetime") and (not t.date_column or p.column == t.date_column):
            if p.op in (">=", ">"):
                lo = (p, v)
            elif p.op in ("<=", "<"):
                hi = (p, v)
    if t.date_column:
        if lo is None or hi is None:
            raise SafetyError(f"a date range on {t.date_column} is required", "range")
        check_range(parse_time(lo[1], lo[0].name, tz), parse_time(hi[1], hi[0].name, tz), limits.max_days,
                    inclusive_day=hi[0].type == "date")
    group: list[str] = []
    for p in t.params:
        if p.group and args.get(p.name):
            group += [c for c in p.group[args[p.name]] if c not in group]
    if t.measures:
        q.select = [(g, None, g) for g in group] + [(m.column, None if m.agg == "none" else m.agg, m.out)
                                                    for m in t.measures]
        q.group = group if any(m.agg != "none" for m in t.measures) else []
        q.order = ([(t.order_by.lstrip("-"), t.order_by.startswith("-"))] if t.order_by
                   else [(g, False) for g in group])
    else:
        q.select = [(c, None, c) for c in t.columns]
        if t.order_by:
            desc = t.order_by.startswith("-")
            q.order = [(t.order_by.lstrip("-"), desc)]
    return q


def rows_as_dicts(cols, rows, digits: int = 4) -> list[dict]:
    out = []
    for r in rows:
        d = {}
        for c, v in zip(cols, r):
            if isinstance(v, float):
                v = round(v, digits)
            elif isinstance(v, (dt.date, dt.datetime)):
                v = v.isoformat()
            elif isinstance(v, bytes):
                v = None
            d[c] = v
        out.append(d)
    return out


def units_of(t: ToolDef, obj=None) -> dict:
    return {m.out: m.unit for m in t.measures if m.unit}


# ----------------------------------------------------------------------- envelope
def envelope(tool: str, rows: list, *, isa95: str = "", tz: str = "UTC", quality: str = "good",
             adapter: str = "", obj: str = "", rows_read: int | None = None, truncated: bool = False,
             units: dict | None = None, tzinfo=None, note: str = "") -> dict:
    now = dt.datetime.now(tzinfo or dt.timezone.utc).replace(microsecond=0)
    e = {"tool": tool, "rows": rows, "units": units or {}, "context": {"isa95": isa95, "tz": tz},
         "quality": quality, "source": {"adapter": adapter, "object": obj,
                                        "rows_read": len(rows) if rows_read is None else rows_read},
         "as_of": now.isoformat(), "truncated": truncated}
    if note:
        e["note"] = note
    return e


TRUNCATED_NOTE = ("Result capped at {n} rows. Narrow the query: a shorter date range, a filter (line, "
                  "machine) or a coarser group_by.")


def new_curated(name: str, source: str, obj, profiles: list) -> ToolDef:
    """A first draft from a discovered object: date range + one filter + numeric measures."""
    cols = [c for c in obj.columns if not c.pii]
    date_col = next((c.name for c in cols if c.type in ("date", "datetime")), "")
    numeric = [c for c in cols if c.type in ("number", "integer") and not c.name.lower().endswith("id")]
    text = [c for c in cols if c.type == "string"]
    params = []
    if text:
        params.append(ToolParam(text[0].name, "string", f"Filter by {text[0].name}", column=text[0].name))
    if date_col:
        params += [ToolParam("date_from", "date", "First day (plant time)", True, date_col, ">="),
                   ToolParam("date_to", "date", "Last day (plant time)", True, date_col, "<=")]
    if text[:3]:
        params.append(ToolParam("group_by", "enum", "Group the result by", default=text[0].name,
                                group={c.name: [c.name] for c in text[:3]}))
    measures = [Measure(c.name, "avg") for c in numeric[:4]] if text else []
    return ToolDef(name=name, kind="curated", title=name.replace("_", " ").capitalize(),
                   description=f"Data from {obj.name}.", source=source, object=obj.name,
                   risk=obj.risk if obj.risk in ("R1", "R2") else "R1", params=params, measures=measures,
                   columns=[] if measures else [c.name for c in cols][:12], date_column=date_col,
                   profiles=list(profiles), order_by="" if measures else (f"-{date_col}" if date_col else ""))
