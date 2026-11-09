"""
safety.py - MCX Adapter is safe on its own: it never relies on the customer's gateway. No Qt.

  * Risk classes R0-R5 per tool, sent to clients as standard MCP annotations; v1 serves R0-R3.
    R2 (sensitive read) stays off until an admin enables it per tool; R4 / R5 never publish.
  * Allow-list: only exposed, not-blocked objects; new objects found by discovery start hidden.
  * Columns marked personal data are never selected or returned.
  * Limits: rows per answer, days per history / curated call, timeout, calls per minute per client.
  * No free SQL, SCPI or protocol writes: tools only take typed parameters (checked here).
"""
from __future__ import annotations

import datetime as dt
import threading
import time
from collections import deque

RISK_INFO = {
    "R0": ("metadata", "Lists assets, tags, units"),
    "R1": ("read", "Values, history, KPIs"),
    "R2": ("sensitive read", "Operator comments, personal data, costs"),
    "R3": ("compute", "ML / analytics on read data"),
    "R4": ("setpoint write", "Change a recipe value or setpoint - not in v1"),
    "R5": ("OT control", "Start / stop, mode change - not in v1"),
}
V1_MAX_RISK = "R3"


class SafetyError(Exception):
    """A call or a publish refused by a rule. `code` goes into the audit log."""

    def __init__(self, message: str, code: str = "blocked"):
        super().__init__(message)
        self.code = code


def risk_level(r: str) -> int:
    try:
        return int(str(r).upper().lstrip("R"))
    except ValueError:
        return 9


def annotations(tool) -> dict:
    """MCP tool annotations for a risk class (the gateway can apply its own policy on top)."""
    lvl = risk_level(tool.risk)
    a = {"title": tool.title or tool.name, "readOnlyHint": lvl <= 3, "openWorldHint": False}
    if lvl <= 3:
        a["idempotentHint"] = True
    if lvl == 4:
        a["destructiveHint"] = False
    if lvl >= 5:
        a["destructiveHint"] = True
    return a


def check_publishable(tool, project):
    """Refuse a publish that breaks a v1 rule."""
    lvl = risk_level(tool.risk)
    if lvl > risk_level(V1_MAX_RISK):
        raise SafetyError(f"{tool.name}: {tool.risk} tools (writes / control) are not available in v1", "risk")
    if lvl > risk_level(project.licence.max_risk):
        raise SafetyError(f"{tool.name}: {tool.risk} needs a higher licence edition "
                          f"({project.licence.edition} allows up to {project.licence.max_risk})", "licence")
    if tool.kind == "curated":
        src = project.source(tool.source)
        if src is None:
            raise SafetyError(f"{tool.name}: source {tool.source!r} not found", "config")
        obj = src.object(tool.object)
        if obj is None or not obj.exposed or obj.blocked:
            raise SafetyError(f"{tool.name}: object {tool.object!r} is not on the allow-list", "allow_list")
        pii = {c.lower() for c in obj.pii_columns()}
        used = {p.column.lower() for p in tool.params if p.column}
        used |= {m.column.lower() for m in tool.measures} | {c.lower() for c in tool.columns}
        for p in tool.params:
            for cols in (p.group or {}).values():
                used |= {c.lower() for c in cols}
        bad = used & pii
        if bad:
            raise SafetyError(f"{tool.name}: uses personal-data column(s) {', '.join(sorted(bad))}", "pii")


def visible(tool, profile, licence) -> bool:
    """Is this (published) tool listed for this profile?"""
    lvl = risk_level(tool.risk)
    if not tool.enabled or lvl > risk_level(V1_MAX_RISK) or lvl > risk_level(licence.max_risk):
        return False
    if lvl == 2 and not tool.sensitive_ok:
        return False
    if profile is not None:
        if profile.id not in tool.profiles and "*" not in tool.profiles:
            return False
        if lvl > risk_level(profile.max_risk):
            return False
    return True


# ----------------------------------------------------------------------- argument checks
def parse_time(v, name: str, tz=None) -> dt.datetime:
    """ISO 8601 date or date-time; naive values are plant local time (tz)."""
    if isinstance(v, (int, float)):
        return dt.datetime.fromtimestamp(v, tz or dt.timezone.utc)
    s = str(v).strip()
    try:
        if len(s) == 10:
            d = dt.datetime.fromisoformat(s)
        else:
            d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        raise SafetyError(f"{name}: not an ISO 8601 date / time: {s!r}", "bad_argument") from None
    if d.tzinfo is None and tz is not None:
        d = d.replace(tzinfo=tz)
    return d


def check_range(t0: dt.datetime, t1: dt.datetime, max_days: int, inclusive_day: bool = False):
    if t1 < t0:
        raise SafetyError("the end is before the start", "bad_argument")
    span = (t1 - t0).total_seconds() / 86400 - (1 if inclusive_day else 0)
    if span > max_days + 1e-9:
        raise SafetyError(f"date range {span:.0f} days is over the limit of {max_days} days - "
                          "ask for a shorter range or an aggregate", "range")


def coerce(param, value):
    """Typed parameter -> Python value (anything else is refused, so no free text reaches a source
    except as a bound parameter)."""
    t = param.type
    if value is None:
        return None
    try:
        if t == "integer":
            if isinstance(value, bool):
                raise ValueError
            return int(value)
        if t == "number":
            if isinstance(value, bool):
                raise ValueError
            return float(value)
        if t == "boolean":
            if isinstance(value, bool):
                return value
            raise ValueError
        if t == "enum":
            if value not in param.values:
                raise SafetyError(f"{param.name} must be one of {', '.join(map(str, param.values))}",
                                  "bad_argument")
            return value
        if t in ("date", "datetime"):
            parse_time(value, param.name)
            return str(value)
        if t == "string":
            s = str(value)
            if len(s) > 200:
                raise SafetyError(f"{param.name}: text too long (max 200)", "bad_argument")
            if param.values and s not in param.values:
                raise SafetyError(f"{param.name} must be one of {', '.join(map(str, param.values))}",
                                  "bad_argument")
            return s
    except SafetyError:
        raise
    except (TypeError, ValueError):
        raise SafetyError(f"{param.name}: expected {t}, got {value!r}", "bad_argument") from None
    return value


# ----------------------------------------------------------------------- rate limit
class RateLimiter:
    """Sliding one-minute window per client."""

    def __init__(self):
        self._calls: dict[str, deque] = {}
        self._lock = threading.Lock()

    def hit(self, client: str, per_min: int, now: float | None = None):
        now = time.monotonic() if now is None else now
        with self._lock:
            q = self._calls.setdefault(client, deque())
            while q and now - q[0] > 60:
                q.popleft()
            if per_min and len(q) >= per_min:
                raise SafetyError(f"rate limit: {per_min} calls per minute - wait "
                                  f"{60 - (now - q[0]):.0f} s", "rate")
            q.append(now)
