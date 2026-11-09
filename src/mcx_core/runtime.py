"""
runtime.py - the adapter service: sources, ISA-95 model, tools, safety, audit. No Qt.

Used the same way headless (`python -m mcx_core serve`) and inside MCX Studio. Thread rules: source
I/O runs in worker threads (a health / poll loop per source, a pool for tool calls with a timeout);
callers on other threads (MCP server, Studio) only read the state and call `call_tool` / `publish`.
Studio polls `rev` (bumped on every change) instead of receiving callbacks.

    rt = Runtime(Project.load("plant.mcx"), "plant.mcx"); rt.start()
    env, err = rt.call_tool("get_oee", {...}, client="cli", profile="eng")
"""
from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from mcx_core import paths
from mcx_core.adapters import AdapterError, Health, Reading, family, make_adapter, type_label
from mcx_core.audit import AuditLog
from mcx_core.config import Project
from mcx_core.model import Model, in_scope
from mcx_core.safety import RateLimiter, SafetyError, check_range, parse_time, risk_level, visible
from mcx_core import tools as tl

log = logging.getLogger(__name__)
QUALITY_ORDER = {"good": 0, "uncertain": 1, "bad": 2}


@dataclass
class ClientInfo:
    id: str
    name: str = ""
    via: str = ""                   # HTTP / stdio / Studio
    profile: str = ""
    auth: str = ""                  # API key / local / none
    calls: int = 0
    last: float = 0.0
    sessions: set = field(default_factory=set)


class Runtime:
    def __init__(self, project: Project, path: str | None = None, user: str = ""):
        self.project, self.path, self.user = project, path, user
        self.adapters: dict = {}
        self.health: dict[str, Health] = {}
        self.discovered: dict[str, str] = {}              # sid -> summary of the last discovery
        self.model = Model.build(project)
        self.rate = RateLimiter()
        self.audit = AuditLog(paths.audit_dir(path),
                              (project.audit.syslog_host, project.audit.syslog_port) if project.audit.syslog_host else None)
        self.clients: dict[str, ClientInfo] = {}
        self.calls_today = self.blocked_today = 0
        self._day = time.strftime("%Y-%m-%d")
        self._count_today()
        self.rev = 0                                     # bumped on every change (Studio polls it)
        self.list_listeners: list = []                   # fn() on tools/list_changed (MCP sessions)
        self._cache: dict[tuple, tuple[float, Reading]] = {}
        self._ring: dict[tuple, deque] = {}
        self._lock = threading.RLock()
        self._pool = cf.ThreadPoolExecutor(max_workers=8, thread_name_prefix="mcx-call")
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.started = 0.0

    # ================================================================ lifecycle
    def start(self):
        self._stop.clear()
        self.started = time.time()
        for s in self.project.sources:
            if s.enabled:
                self.start_source(s.id)

    def stop(self):
        self._stop.set()
        for t in self._threads:
            t.join(timeout=3)
        self._threads.clear()
        for a in list(self.adapters.values()):
            try:
                a.close()
            except Exception:
                pass
        self.adapters.clear()

    def start_source(self, sid: str):
        cfg = self.project.source(sid)
        if cfg is None:
            return
        old = self.adapters.pop(sid, None)
        if old:
            old.close()
        try:
            a = make_adapter(cfg, self.project.limits)
        except AdapterError as e:
            self.health[sid] = Health("error", str(e), None, time.time())
            return
        self.adapters[sid] = a
        self.health[sid] = Health("connecting", "", None, time.time())
        t = threading.Thread(target=self._source_loop, args=(sid, a), name=f"src-{sid}", daemon=True)
        self._threads.append(t)
        t.start()
        self._bump()

    def stop_source(self, sid: str):
        a = self.adapters.pop(sid, None)
        if a:
            a.close()
        self.health[sid] = Health("stopped")
        self._bump()

    def _source_loop(self, sid, a):
        """Health every 15 s; tag sources also poll their exposed items into the history ring."""
        poll = float(a.cfg.settings.get("poll_s", 10) or 10)
        next_health = 0.0
        while not self._stop.is_set() and self.adapters.get(sid) is a:
            now = time.time()
            if now >= next_health:
                h = a.health()
                prev = self.health.get(sid)
                self.health[sid] = h
                if prev is None or prev.state != h.state or prev.message != h.message:
                    self._bump()
                next_health = now + (15 if h.state == "online" else 5)
            if family(a.cfg) == "tags" and self.health[sid].state == "online":
                items = [i for i in a.cfg.items if i.exposed]
                if items:
                    try:
                        self._store(sid, items, a.read(items[:500]))
                    except Exception as e:
                        log.debug("poll %s: %s", sid, e)
            self._stop.wait(min(poll, max(0.5, next_health - time.time())))

    def _store(self, sid, items, readings):
        with self._lock:
            for it, r in zip(items, readings):
                k = (sid, it.address)
                self._cache[k] = (time.time(), r)
                if r.quality != "bad" and isinstance(r.value, (int, float)):
                    self._ring.setdefault(k, deque(maxlen=20000)).append((r.t or time.time(), float(r.value)))

    def reload(self, project: Project):
        """Hot reload: new configuration, same audit log and counters."""
        self.stop()
        self.project = project
        self.model = Model.build(project)
        self.start()
        self.notify_tools_changed()

    def _bump(self):
        self.rev += 1

    def notify_tools_changed(self):
        self.model = Model.build(self.project)
        self._bump()
        for fn in list(self.list_listeners):
            try:
                fn()
            except Exception:
                pass

    # ================================================================ discovery
    def discover(self, sid: str):
        """Connect + discover one source (blocking - call it off the GUI thread)."""
        cfg = self.project.source(sid)
        a = self.adapters.get(sid) or make_adapter(cfg, self.project.limits)
        try:
            res = a.discover()
        finally:
            if sid not in self.adapters:
                a.close()
        return res

    def apply_discovery(self, sid: str, res):
        """Merge a discovery: known objects / items keep their settings, new ones start hidden."""
        cfg = self.project.source(sid)
        old = {o.name.lower(): o for o in cfg.objects}
        merged = []
        for o in res.objects:
            prev = old.get(o.name.lower())
            if prev:
                pii = {c.name.lower() for c in prev.columns if c.pii}
                for c in o.columns:
                    c.pii = c.pii or c.name.lower() in pii
                o.exposed, o.blocked, o.risk, o.note = prev.exposed, prev.blocked, prev.risk, prev.note
            merged.append(o)
        cfg.objects = merged
        if res.items:
            known = {i.address: i for i in cfg.items}
            items = []
            for it in res.items:
                prev = known.get(it.address)
                if prev:
                    items.append(prev)
                else:
                    it.exposed = family(cfg) != "tags" or bool(it.path)
                    items.append(it)
            cfg.items = items
        cfg.discovered = time.strftime("%Y-%m-%d %H:%M")
        self.discovered[sid] = res.summary
        self.model = Model.build(self.project)
        self._bump()

    # ================================================================ tools
    def live_tools(self, profile: str | None = None) -> list:
        prof = self.project.profile(profile) if profile else None
        out = []
        for t in self.project.tools:
            lt = t.live()
            if lt is not None and visible(lt, prof, self.project.licence):
                out.append(lt)
        return out

    def mcp_tools(self, profile: str | None = None) -> list[dict]:
        from mcx_core.safety import annotations
        tz = self.project.timezone
        return [{"name": t.name, "title": t.title or t.name,
                 "description": tl.full_description(t, self.project.limits, tz),
                 "inputSchema": tl.input_schema(t), "annotations": annotations(t)}
                for t in self.live_tools(profile)]

    def publish(self, names=None, note: str = "", errors: list | None = None) -> list[str]:
        """Publish drafts; tools that break a rule are held back into `errors` (name, reason)."""
        errors = [] if errors is None else errors
        done = self.project.publish(names, self.user, note, errors)
        if done:
            self.audit.write(client=self.user or "studio", via="studio", tool="publish", args={"tools": done},
                             rows=len(done), ms=0, result="ok")
            self.notify_tools_changed()
        return done

    @property
    def tz(self):
        try:
            return ZoneInfo(self.project.timezone)
        except Exception:
            return dt.timezone.utc

    def call_tool(self, name: str, args: dict, client: str = "local", profile: str | None = None,
                  via: str = "", draft: bool = False) -> tuple[dict, str]:
        """Run one tool. Returns (envelope, error text or ""). Every call is audited.
        draft=True runs the current draft (Studio test calls) instead of the published version."""
        self._roll_day()
        t0 = time.perf_counter()
        result, err, rows = "ok", "", 0
        env: dict = {}
        try:
            self.rate.hit(client, self.project.limits.rate_per_min)
            tool = self._resolve(name, profile, draft)
            args = tl.check_args(tool, args)
            fut = self._pool.submit(self._run, tool, args, profile)
            try:
                env = fut.result(timeout=self.project.limits.timeout_s + 1)
            except cf.TimeoutError:
                raise SafetyError(f"timeout after {self.project.limits.timeout_s:g} s", "timeout") from None
            rows = len(env.get("rows", []))
            if env.get("truncated"):
                result = "truncated"
        except SafetyError as e:
            result, err = "blocked", str(e)
            self.blocked_today += 1
            env = {"tool": name, "error": err, "code": e.code}
        except Exception as e:
            result, err = "error", str(e) or type(e).__name__
            env = {"tool": name, "error": err}
        ms = round((time.perf_counter() - t0) * 1000, 1)
        self.calls_today += 1
        c = self.clients.get(client)
        if c:
            c.calls += 1
            c.last = time.time()
        if self.project.audit.enabled:
            rec = dict(client=client, profile=profile or "", via=via, tool=name, args=args, rows=rows, ms=ms,
                       result=result)
            if err:
                rec["error"] = err[:300]
            if draft:
                rec["test"] = True
            self.audit.write(**rec)
        self._bump()
        return env, err

    def _resolve(self, name, profile, draft):
        t = self.project.tool(name)
        if t is None:
            raise SafetyError(f"unknown tool {name!r}", "unknown_tool")
        if draft:
            if risk_level(t.risk) > 3:
                raise SafetyError(f"{t.risk} tools are not available in v1", "risk")
            return t
        lt = t.live()
        prof = self.project.profile(profile) if profile else None
        if lt is None or not visible(lt, prof, self.project.licence):
            raise SafetyError(f"tool {name!r} is not published for this client", "not_listed")
        return lt

    def _run(self, tool, args, profile) -> dict:
        if tool.kind == "generic":
            return self._generic(tool, args, self.project.profile(profile) if profile else None)
        return self._curated(tool, args)

    # ---- curated
    def _curated(self, tool, args) -> dict:
        src = self.project.source(tool.source)
        if src is None or not src.enabled:
            raise AdapterError(f"source {tool.source!r} is not available")
        obj = src.object(tool.object)
        if obj is None or not obj.exposed or obj.blocked:
            raise SafetyError(f"{tool.object} is not on the allow-list", "allow_list")
        a = self.adapters.get(src.id) or make_adapter(src, self.project.limits)
        lim = self.project.limits
        q = tl.curated_query(tool, args, obj, lim, self.tz)
        cols, rows = a.select(obj, q, lim.timeout_s)
        pii = {c.lower() for c in obj.pii_columns()}
        keep = [i for i, c in enumerate(cols) if c.lower() not in pii]
        cols, rows = [cols[i] for i in keep], [[r[i] for i in keep] for r in rows]
        truncated = len(rows) > lim.max_rows
        rows = rows[:lim.max_rows]
        ctx = tool.isa95 or src.isa95
        if args.get("line") and ctx:
            ctx = f"{ctx}/{args['line']}"
        return tl.envelope(tool.name, tl.rows_as_dicts(cols, rows), isa95=ctx, tz=self.project.timezone,
                           adapter=f"{type_label(src).lower().replace(' ', '')}.{src.id}", obj=obj.name,
                           rows_read=len(rows), truncated=truncated, units=tl.units_of(tool), tzinfo=self.tz,
                           note=tl.TRUNCATED_NOTE.format(n=lim.max_rows) if truncated else "")

    # ---- generic
    def _scope_ok(self, path, prof):
        return in_scope(path, prof.scope if prof else "")

    def _generic(self, tool, args, prof) -> dict:
        lim, tz = self.project.limits, self.tz
        env = dict(tz=self.project.timezone, tzinfo=tz, adapter="model")
        if tool.name == "browse_model":
            path = (args.get("path") or "").strip("/")
            node = self.model.node(path)
            if node is None:
                raise SafetyError(f"no such path {path!r} - call browse_model without a path to start",
                                  "bad_argument")
            rows = [{"name": c.name, "path": c.path, "kind": "node", "level": c.level,
                     "children": len(c.children), "tags": len(c.items)}
                    for c in node.children.values() if self._scope_ok(c.path, prof) or
                    in_scope(prof.scope if prof else "", c.path)]
            if self._scope_ok(path, prof):
                rows += [{"name": it.name, "path": it.full_path, "kind": "tag", "unit": it.unit,
                          "lo": it.lo, "hi": it.hi, "description": it.description,
                          "source": sid} for sid, it in node.items]
            return tl.envelope(tool.name, rows[:lim.max_rows], isa95=path, obj="isa95",
                               truncated=len(rows) > lim.max_rows, **env)
        if tool.name == "list_assets":
            scope = (args.get("scope") or (prof.scope if prof else "")).strip("/")
            if prof and not self._scope_ok(scope, prof):
                raise SafetyError(f"{scope} is outside this profile's scope ({prof.scope})", "scope")
            rows = [{"path": n.path, "name": n.name, "level": n.level, "tags": len(n.items),
                     "sources": sorted({sid for sid, _ in n.items})} for n in self.model.equipment(scope)]
            return tl.envelope(tool.name, rows[:lim.max_rows], isa95=scope, obj="isa95",
                               truncated=len(rows) > lim.max_rows, **env)
        path = (args.get("path") or "").strip("/")
        if not self._scope_ok(path, prof):
            raise SafetyError(f"{path} is outside this profile's scope", "scope")
        if tool.name == "read_tag":
            hit = self.model.item(path)
            if hit:
                targets = [hit]
            else:
                node = self.model.node(path)
                if node is None or not node.items:
                    raise SafetyError(f"no tag or machine at {path!r} - use browse_model to find paths",
                                      "bad_argument")
                targets = list(node.items)
            rows = self._read(targets)
            q = max((QUALITY_ORDER.get(r["quality"], 2) for r in rows), default=0)
            srcs = sorted({sid for sid, _ in targets})
            return tl.envelope(tool.name, rows, isa95=path, quality=list(QUALITY_ORDER)[q],
                               units={r["name"]: r["unit"] for r in rows if r.get("unit")},
                               obj=",".join(srcs), **{**env, "adapter": ",".join(
                                   f"{type_label(self.project.source(s)).lower().replace(' ', '')}.{s}"
                                   for s in srcs)})
        if tool.name == "query_history":
            hit = self.model.item(path)
            if not hit:
                raise SafetyError(f"no tag at {path!r} (query_history needs a tag path)", "bad_argument")
            sid, it = hit
            a0, a1 = parse_time(args["from"], "from", tz), parse_time(args["to"], "to", tz)
            check_range(a0, a1, lim.max_days)
            rows, quality = self._history(sid, it, a0.timestamp(), a1.timestamp(), args.get("agg", "avg"),
                                          INTERVAL(args.get("interval", "15m")))
            truncated = len(rows) > lim.max_rows
            src = self.project.source(sid)
            return tl.envelope(tool.name, rows[:lim.max_rows], isa95=it.path, quality=quality,
                               units={"value": it.unit} if it.unit else {}, obj=it.address,
                               truncated=truncated,
                               note=tl.TRUNCATED_NOTE.format(n=lim.max_rows) if truncated else "",
                               **{**env, "adapter": f"{type_label(src).lower().replace(' ', '')}.{sid}"})
        raise SafetyError(f"unknown generic tool {tool.name}", "unknown_tool")

    def _read(self, targets) -> list[dict]:
        by_src: dict[str, list] = {}
        for sid, it in targets:
            by_src.setdefault(sid, []).append(it)
        rows = []
        tz = self.tz
        for sid, items in by_src.items():
            a = self.adapters.get(sid)
            fresh, need = {}, []
            with self._lock:
                for it in items:
                    c = self._cache.get((sid, it.address))
                    if c and time.time() - c[0] <= self.project.limits.cache_s:
                        fresh[it.address] = c[1]
                    else:
                        need.append(it)
            if need:
                if a is None:
                    for it in need:
                        fresh[it.address] = Reading(None, "bad", 0, "source stopped")
                else:
                    try:
                        got = a.read(need)
                        self._store(sid, need, got)
                        for it, r in zip(need, got):
                            fresh[it.address] = r
                    except Exception as e:
                        for it in need:
                            fresh[it.address] = Reading(None, "bad", 0, str(e)[:120])
            for it in items:
                r = fresh[it.address]
                row = {"path": it.full_path, "name": it.name, "value": r.value, "unit": it.unit,
                       "quality": r.quality,
                       "time": dt.datetime.fromtimestamp(r.t, tz).replace(microsecond=0).isoformat() if r.t else None}
                if it.lo is not None or it.hi is not None:
                    row["limits"] = [it.lo, it.hi]
                if r.error:
                    row["error"] = r.error
                rows.append(row)
        return rows

    def _history(self, sid, item, t0, t1, agg, step):
        a = self.adapters.get(sid)
        pts = None
        if a is not None:
            try:
                pts = a.history(item, t0, t1, 20000)
            except Exception:
                pts = None
        quality = "good"
        if pts is None:
            with self._lock:
                pts = [(t, v) for t, v in self._ring.get((sid, item.address), ()) if t0 <= t <= t1]
            quality = "uncertain" if pts else "bad"     # local cache only (since the adapter started)
        tz = self.tz
        iso = lambda t: dt.datetime.fromtimestamp(t, tz).replace(microsecond=0).isoformat()   # noqa: E731
        if agg == "raw" or not step:
            return [{"time": iso(t), "value": v} for t, v in pts], quality
        buckets: dict[int, list] = {}
        for t, v in pts:
            if isinstance(v, (int, float)):
                buckets.setdefault(int((t - t0) // step), []).append(v)
        fn = {"avg": lambda x: sum(x) / len(x), "min": min, "max": max}[agg]
        rows = [{"time": iso(t0 + k * step), "value": round(fn(v), 4), "n": len(v)}
                for k, v in sorted(buckets.items())]
        return rows, quality

    # ================================================================ clients / stats
    def track_client(self, cid: str, name: str = "", via: str = "", profile: str = "", auth: str = ""):
        c = self.clients.get(cid)
        if c is None:
            c = self.clients[cid] = ClientInfo(cid)
        c.name = name or c.name or cid
        c.via, c.profile, c.auth = via or c.via, profile or c.profile, auth or c.auth
        c.last = time.time()
        self._bump()
        return c

    def _count_today(self):
        for r in self.audit.today():
            if r.get("tool") != "publish" and r.get("kind") != "admin":
                self.calls_today += 1
                self.blocked_today += r.get("result") == "blocked"

    def _roll_day(self):
        d = time.strftime("%Y-%m-%d")
        if d != self._day:
            self._day, self.calls_today, self.blocked_today = d, 0, 0
            for c in self.clients.values():
                c.calls = 0

    def sources_online(self) -> tuple[int, int]:
        en = [s for s in self.project.sources if s.enabled]
        return sum(self.health.get(s.id, Health()).state == "online" for s in en), len(en)

    def summary(self) -> dict:
        on, n = self.sources_online()
        pub = [t for t in self.project.tools if t.published is not None]
        return {"sources_online": on, "sources": n, "tools": len(pub),
                "generic": sum(t.kind == "generic" for t in pub), "curated": sum(t.kind == "curated" for t in pub),
                "profiles": len(self.project.profiles), "calls_today": self.calls_today,
                "blocked_today": self.blocked_today, "clients": len(self.clients)}


def INTERVAL(key: str) -> int:
    return tl.INTERVALS.get(key, 900)
