"""
app.py - the MCX Adapter web console (FastAPI + Jinja2, server-rendered, no CDN - works offline).

One process: the adapter runtime (mcx_core), the MCP endpoint (Streamable HTTP, its own port) and this
console. Every change made here is applied to the running adapter, saved to the project file at once
and written to the audit log (kind "admin") with the user's name.

Security: local accounts (users.py), session cookie HttpOnly + SameSite=Strict, a CSRF token in every
form / fetch, role checked on every request (viewer < engineer < admin), security headers, secrets
(API keys) shown once.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
import threading
import time
from dataclasses import asdict
from pathlib import Path
from urllib.parse import quote

import yaml
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from starlette.concurrency import run_in_threadpool

from mcx_core import APP_NAME, __version__
from mcx_core.adapters import ADAPTERS, PLANNED, Health, family, make_adapter, type_label
from mcx_core.config import RISKS, Licence, Profile, Project, SourceCfg, ToolDef
from mcx_core.mcp import McpServer, Session
from mcx_core.model import Model
from mcx_core.runtime import Runtime
from mcx_core.safety import RISK_INFO, SafetyError, annotations, check_publishable, risk_level
from mcx_core.tools import input_schema, new_curated, signature
from mcx_web.icons import icon
from mcx_web.users import ROLE_HELP, ROLE_TEXT, ROLES, UserStore

HERE = Path(__file__).resolve().parent
COOKIE = "mcx_session"
STATE_TOKEN = {"online": "ok", "connecting": "warn", "error": "warn", "stopped": "muted", "disabled": "muted"}
RESULT_TOKEN = {"ok": "ok", "blocked": "bad", "error": "bad", "truncated": "warn"}
PLATFORMS = ["Copilot Studio", "ContextForge", "Docker MCP Gateway", "Claude Desktop"]
CLIENT_KIND = {"ContextForge": "Gateway", "Docker MCP Gateway": "Gateway", "Copilot Studio": "AI platform",
               "Claude Desktop": "Direct (stdio)"}
STEPS = ["Connect", "Discover", "Expose as MCP tools", "Review & publish"]


class Redirect(Exception):
    def __init__(self, url: str):
        self.url = url


class Denied(Exception):
    pass


# ======================================================================= the adapter in this process
class Console:
    def __init__(self, project_path: str, start_mcp: bool = True):
        self.path = str(Path(project_path).resolve())
        self.project = Project.load(self.path)
        self.rt = Runtime(self.project, self.path, user="web")
        self.rt.start()
        self.server = McpServer(self.rt)
        self.lock = threading.RLock()
        self.users = UserStore(Path(self.path).parent / "mcx_web.db")
        self.started = time.time()
        if start_mcp and self.project.endpoint.http:
            self.start_mcp()

    def start_mcp(self):
        try:
            self.server.start()
        except Exception as e:                      # port in use ... shown on the console
            self.server.httpd = None
            self.server.error = str(e)

    def stop(self):
        self.server.stop()
        self.rt.stop()

    def changed(self, user, action: str, structure: bool = True, **detail):
        """A configuration change: save the project, rebuild the model, audit who did it."""
        with self.lock:
            self.project.save(self.path)
            if structure:
                self.rt.model = Model.build(self.project)
            self.rt._bump()
            self.rt.audit.write(client=user.username, via="web", kind="admin", tool=action, args=detail,
                                rows=0, ms=0, result="ok")


# ======================================================================= app
def create_app(console: Console) -> FastAPI:
    app = FastAPI(title=f"{APP_NAME} console", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.c = console
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    tpl = Jinja2Templates(directory=HERE / "templates")
    tpl.env.globals.update(icon=icon, ROLE_TEXT=ROLE_TEXT, RISK_INFO=RISK_INFO, STEPS=STEPS, version=__version__,
                           state_token=STATE_TOKEN, result_token=RESULT_TOKEN, risk_level=risk_level)
    tpl.env.filters["fmt"] = lambda n: f"{int(n):,}" if isinstance(n, (int, float)) else n
    tpl.env.filters["profile_short"] = lambda p: p.upper() if len(p) <= 2 else p.capitalize()
    c = console

    # ---------------------------------------------------------------- plumbing
    @app.middleware("http")
    async def headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["Content-Security-Policy"] = ("default-src 'self'; script-src 'self'; style-src 'self' "
                                                   "'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'")
        if request.url.path not in ("/login", "/setup") and not request.url.path.startswith("/static"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.exception_handler(Redirect)
    async def _redirect(_request, exc: Redirect):
        return RedirectResponse(exc.url, status_code=303)

    @app.exception_handler(Denied)
    async def _denied(request: Request, _exc):
        return HTMLResponse("<!doctype html><title>Not allowed</title><p>Your role cannot do this. "
                            "<a href='/'>Back</a></p>", status_code=403)

    def who(request: Request, need: str = "viewer"):
        """(user, csrf, token) or redirect to sign-in / setup."""
        if c.users.count() == 0:
            raise Redirect("/setup")
        token = request.cookies.get(COOKIE)
        s = c.users.session(token)
        if s is None:
            raise Redirect("/login?next=" + quote(request.url.path))
        user, csrf = s
        if not user.can(need):
            raise Denied()
        return user, csrf, token

    async def posted(request: Request, need: str = "engineer"):
        """Form POST: role + CSRF checked. Returns (user, form, token)."""
        user, csrf, token = who(request, need)
        form = await request.form()
        if form.get("csrf") != csrf and request.headers.get("X-CSRF") != csrf:
            raise Denied()
        return user, form, token

    def back(url: str, ok: str = "", err: str = "") -> RedirectResponse:
        sep = "&" if "?" in url else "?"
        if ok:
            url += f"{sep}ok={quote(ok)}"
        elif err:
            url += f"{sep}err={quote(err)}"
        return RedirectResponse(url, status_code=303)

    def page(request: Request, name: str, user, csrf, token, active: str = "", **kw):
        p, rt = c.project, c.rt
        on, n = rt.sources_online()
        srcs = []
        for s in p.sources:
            h = rt.health.get(s.id, Health())
            st = h.state if s.enabled else "disabled"
            srcs.append({"id": s.id, "name": s.name, "type": type_label(s), "state": st, "msg": h.message,
                         "token": STATE_TOKEN.get(st, "muted"), "lat": h.latency_ms, "cfg": s})
        ctx = dict(user=user, csrf=csrf, active=active, project=p, summary=rt.summary(), nav_sources=srcs,
                   online=on, n_sources=n, pending=len(ready_to_publish(p.tools, p)), server=c.server,
                   mcp_url=p.endpoint.base_url(), ok=request.query_params.get("ok", ""),
                   err=request.query_params.get("err", ""), flash=c.users.flash(token) if token else "",
                   can_edit=user.can("engineer"), can_admin=user.can("admin"), alarms=alarms())
        ctx.update(kw)
        return tpl.TemplateResponse(request, name, ctx)

    def alarms() -> list[dict]:
        out = []
        for s in c.project.sources:
            h = c.rt.health.get(s.id, Health())
            if s.enabled and h.state == "error":
                out.append({"level": "warn", "where": f"{type_label(s)} · {s.name}", "text": h.message or "offline",
                            "href": f"/sources/{s.id}"})
        if c.server.error:
            out.append({"level": "bad", "where": "MCP endpoint", "text": c.server.error, "href": "/endpoint"})
        held = [t.name for t in c.project.tools if t.published is None and t.risk == "R2"]
        if held:
            out.append({"level": "info", "where": "Tools", "text": f"held back (R2 personal data): {', '.join(held)}",
                        "href": "/tools"})
        lic = c.project.licence
        if lic.max_sources and len(c.project.sources) > lic.max_sources:
            out.append({"level": "warn", "where": "Licence", "text": f"{len(c.project.sources)} sources, "
                        f"{lic.edition} allows {lic.max_sources}", "href": "/licence"})
        return out

    def client_label(cid: str) -> str:
        k = next((k for k in c.project.auth.api_keys if k.id == cid), None)
        if k:
            return k.client or k.name
        cl = c.rt.clients.get(cid)
        return (cl.name if cl and cl.name else cid)[:28]

    # ---------------------------------------------------------------- sign in
    @app.get("/setup", response_class=HTMLResponse)
    def setup_form(request: Request):
        if c.users.count():
            raise Redirect("/login")
        return tpl.TemplateResponse(request, "setup.html", {"project": c.project, "err": ""})

    @app.post("/setup")
    async def setup(request: Request):
        if c.users.count():
            raise Redirect("/login")
        f = await request.form()
        if f.get("password") != f.get("password2"):
            return tpl.TemplateResponse(request, "setup.html", {"project": c.project, "err": "Passwords differ"})
        try:
            u = c.users.create(f.get("username", ""), f.get("password", ""), "admin", f.get("name", ""))
        except ValueError as e:
            return tpl.TemplateResponse(request, "setup.html", {"project": c.project, "err": str(e)})
        c.rt.audit.write(client=u.username, via="web", kind="admin", tool="user.create", args={"user": u.username,
                         "role": "admin", "first": True}, rows=0, ms=0, result="ok")
        return _signed_in(request, u, "/")

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request):
        if c.users.count() == 0:
            raise Redirect("/setup")
        return tpl.TemplateResponse(request, "login.html", {"project": c.project, "err": "",
                                                            "next": request.query_params.get("next", "/")})

    def _signed_in(request: Request, user, nxt: str):
        token = c.users.new_session(user.id)
        resp = RedirectResponse(nxt if nxt.startswith("/") and not nxt.startswith("//") else "/", status_code=303)
        resp.set_cookie(COOKIE, token, httponly=True, samesite="strict", secure=request.url.scheme == "https",
                        max_age=12 * 3600, path="/")
        return resp

    @app.post("/login")
    async def login(request: Request):
        f = await request.form()
        name, nxt = str(f.get("username", "")), str(f.get("next", "/"))
        u = await run_in_threadpool(c.users.authenticate, name, str(f.get("password", "")))
        if u is None:
            wait = c.users.locked(name)
            c.rt.audit.write(client=name[:40], via="web", kind="admin", tool="login", args={}, rows=0, ms=0,
                             result="blocked", error="locked" if wait else "wrong user name or password")
            msg = f"Too many attempts - try again in {wait / 60:.0f} min" if wait else "Wrong user name or password"
            return tpl.TemplateResponse(request, "login.html", {"project": c.project, "err": msg, "next": nxt},
                                        status_code=401)
        c.rt.audit.write(client=u.username, via="web", kind="admin", tool="login", args={}, rows=0, ms=0, result="ok")
        return _signed_in(request, u, nxt)

    @app.post("/logout")
    async def logout(request: Request):
        user, _f, token = await posted(request, "viewer")
        c.users.end_session(token)
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(COOKIE, path="/")
        return resp

    # ---------------------------------------------------------------- overview
    @app.get("/", response_class=HTMLResponse)
    def overview(request: Request):
        user, csrf, token = who(request)
        rt, p = c.rt, c.project
        clients = []
        for cl in sorted(rt.clients.values(), key=lambda x: -x.calls):
            k = next((k for k in p.auth.api_keys if k.id == cl.id), None)
            plat = k.client if k else ""
            clients.append({"name": " · ".join(x for x in (plat, cl.name if cl.name not in (cl.id, plat) else "") if x)
                            or cl.id, "via": CLIENT_KIND.get(plat, cl.via), "profile": cl.profile or "all",
                            "auth": cl.auth, "calls": cl.calls, "dim": False})
        for k in p.auth.api_keys:
            if k.id not in rt.clients:
                clients.append({"name": k.client or k.name, "via": CLIENT_KIND.get(k.client, "–"),
                                "profile": k.profile or "all", "auth": "API key", "calls": 0, "dim": True})
        recent = [r for r in rt.audit.recent if r.get("kind") != "admin" and r.get("tool") != "publish"][-8:][::-1]
        audit = [{"t": str(r.get("time", ""))[11:16], "client": client_label(r.get("client", "")),
                  "tool": r.get("tool", ""), "rows": r.get("rows") if r.get("result") not in ("blocked", "error") else "–",
                  "result": r.get("result", ""), "error": r.get("error", "")} for r in recent]
        pii = sum(len(o.pii_columns()) for s in p.sources for o in s.objects if o.exposed)
        held = sum(1 for t in p.tools if t.published is None and t.risk == "R2")
        return page(request, "overview.html", user, csrf, token, "overview", clients=clients, audit=audit,
                    pii=pii, held=held)

    @app.get("/api/status")
    def status(request: Request):
        who(request)
        s = c.rt.summary()
        return {**s, "server": bool(c.server.running), "pending": len(ready_to_publish(c.project.tools, c.project)),
                "rev": c.rt.rev,
                "alarms": len([a for a in alarms() if a["level"] != "info"]),
                "sources_state": {sid: h.state for sid, h in c.rt.health.items()}}

    # ---------------------------------------------------------------- sources
    @app.get("/sources/new", response_class=HTMLResponse)
    def source_new(request: Request):
        user, csrf, token = who(request, "engineer")
        cats = {}
        for k, cls in ADAPTERS.items():
            cats.setdefault(cls.CATEGORY, []).append({"type": k, "label": cls.LABEL, "icon": cls.ICON})
        return page(request, "source_new.html", user, csrf, token, "sources", cats=cats, planned=PLANNED)

    @app.post("/sources/new")
    async def source_add(request: Request):
        user, f, _ = await posted(request)
        kind, name = f.get("type", ""), str(f.get("name", "")).strip()
        if kind not in ADAPTERS or not name:
            return back("/sources/new", err="Choose a type and give the source a name")
        with c.lock:
            sid = c.project.unique_id(name, {s.id for s in c.project.sources})
            cls = ADAPTERS[kind]
            c.project.sources.append(SourceCfg(sid, name, kind, settings={x["key"]: x["default"] for x in cls.SCHEMA
                                                                          if x["default"] not in (None, "")},
                                               isa95=c.project.site or ""))
            c.changed(user, "source.add", source=sid, type=kind)
        c.rt.start_source(sid)
        return back(f"/sources/{sid}?step=0", ok="Source added - set the connection, test it, then discover")

    def source_or_404(sid):
        s = c.project.source(sid)
        if s is None:
            raise Redirect("/?err=" + quote(f"No source {sid}"))
        return s

    def publishable(s):
        return ([t for t in c.project.tools if t.kind == "generic"] if family(s) == "tags"
                else [t for t in c.project.tools if t.kind == "curated" and t.source == s.id])

    @app.get("/sources/{sid}", response_class=HTMLResponse)
    def source_page(request: Request, sid: str):
        user, csrf, token = who(request)
        s = source_or_404(sid)
        p, rt = c.project, c.rt
        h = rt.health.get(sid, Health())
        tags = family(s) == "tags"
        mine = publishable(s)
        done = set()
        if h.state == "online":
            done.add(0)
        if s.discovered or s.objects or s.items:
            done.add(1)
        if any(t.source == sid for t in p.tools) or (tags and any(i.exposed for i in s.items)):
            done.add(2)
        if mine and not any(t.changed for t in mine):
            done.add(3)
        default = 2 if 1 in done else (1 if 0 in done else 0)
        step = int(request.query_params.get("step", default))
        cls = ADAPTERS.get(s.type)
        schema = list(cls.SCHEMA) if cls else []
        rows, cur, obj = [], None, None
        sel = request.query_params.get("tool") or request.query_params.get("obj") or ""
        if tags:
            rows = [{"key": it.address, "path": it.full_path, "address": it.address, "unit": it.unit,
                     "limits": "" if it.lo is None and it.hi is None else f"{_n(it.lo)} … {_n(it.hi)}",
                     "on": it.exposed} for it in s.items]
            cur = p.tool("read_tag")
            first = next((i for i in s.items if i.address == sel), s.items[0] if s.items else None)
            args = f'path="{first.full_path}"' if first else ""
        else:
            for o in s.objects:
                t = next((t for t in p.tools if t.source == sid and t.object.lower() == o.name.lower()), None)
                rows.append({"key": o.name, "obj": o, "tool": t, "on": bool(o.exposed and t is not None),
                             "label": t.name if t is not None and (o.exposed or t.published) else
                             ("blocked by rule" if o.blocked else "not exposed"),
                             "risk": t.risk if t is not None else o.risk,
                             "profiles": (" · ".join(_short(x) for x in t.profiles) if t is not None and o.exposed
                                          else (o.note or "–"))})
            rows.sort(key=lambda r: (r["obj"].blocked, not r["on"], r["obj"].name.lower()))
            pick = next((r for r in rows if r["key"] == sel or (r["tool"] and r["tool"].name == sel)),
                        next((r for r in rows if r["on"]), rows[0] if rows else None))
            if pick:
                sel = pick["key"]
                cur, obj = pick["tool"], pick["obj"]
            args = default_args(cur) if cur else ""
        exposed = [r for r in rows if r["on"]] if not tags else [r for r in rows if r["on"]]
        views = sum(o.kind == "view" for o in s.objects)
        stats = {"objects": len(s.objects) if not tags else len(s.items),
               "objects_detail": f"{len(s.objects) - views} tables · {views} views" if not tags else "tags / registers / topics",
               "exposed": len(exposed),
               "exposed_detail": (f"{sum(1 for r in rows if r['tool'] and r['tool'].published is None and r['risk'] == 'R2')} held back · "
                                  f"{sum(r['obj'].blocked for r in rows)} blocked") if not tags else "in the ISA-95 model",
               "risk": max((r["risk"] for r in rows if r["on"]), key=risk_level, default="R0") if not tags else "R1"}
        review = []
        for t in mine:
            try:
                check_publishable(t, p)
                chk, tok = "ok", "ok"
            except SafetyError as e:
                chk, tok = str(e).split(": ", 1)[-1], "warn"
            review.append({"t": t, "check": chk, "token": tok})
        hist = sorted(((hh, t.name) for t in mine for hh in t.history), key=lambda x: x[0].get("time", ""), reverse=True)[:40]
        return page(request, "source.html", user, csrf, token, f"source:{sid}", s=s, h=h, tags=tags, step=step,
                    done=done, schema=schema, rows=rows, cur=cur, obj=obj, sel=sel, args=args, stats=stats,
                    review=review, hist=hist, pending_here=ready_to_publish(mine, p),
                    sig=signature(cur) if cur else "", params=_params(cur) if cur else [],
                    ann=annotations(cur) if cur else {}, tz=p.timezone, settings_view=_settings(s, schema))

    @app.post("/sources/{sid}/settings")
    async def source_settings(request: Request, sid: str):
        user, f, _ = await posted(request)
        s = source_or_404(sid)
        cls = ADAPTERS.get(s.type)
        with c.lock:
            s.name = str(f.get("name", s.name)).strip() or s.name
            s.isa95 = str(f.get("isa95", "")).strip().strip("/")
            s.enabled = f.get("enabled") == "on"
            for fd in (cls.SCHEMA if cls else []) + [{"key": "poll_s", "type": "float", "default": 10}]:
                k, t = fd["key"], fd["type"]
                if t == "bool":
                    s.settings[k] = f.get(k) == "on"
                    continue
                v = f.get(k)
                if v is None or (t == "password" and v == ""):
                    continue                            # password left empty = keep the stored one
                try:
                    s.settings[k] = int(v) if t == "int" else float(v) if t == "float" else str(v)
                except ValueError:
                    return back(f"/sources/{sid}?step=0", err=f"{fd.get('label', k)}: not a number")
            c.changed(user, "source.settings", source=sid)
        c.rt.start_source(sid) if s.enabled else c.rt.stop_source(sid)
        return back(f"/sources/{sid}?step=0", ok="Saved - reconnecting")

    @app.post("/sources/{sid}/test")
    async def source_test(request: Request, sid: str):
        await posted(request)
        s = source_or_404(sid)

        def run():
            a = make_adapter(s, c.project.limits)
            try:
                return a.health()
            finally:
                a.close()
        h = await run_in_threadpool(run)
        return JSONResponse({"state": h.state, "message": h.message,
                             "latency": round(h.latency_ms) if h.latency_ms else None})

    @app.post("/sources/{sid}/discover")
    async def source_discover(request: Request, sid: str):
        user, _f, _ = await posted(request)
        source_or_404(sid)
        try:
            res = await run_in_threadpool(c.rt.discover, sid)
        except Exception as e:
            return back(f"/sources/{sid}?step=1", err=f"Discovery failed: {e}")
        with c.lock:
            c.rt.apply_discovery(sid, res)
            c.changed(user, "source.discover", source=sid, summary=res.summary)
        return back(f"/sources/{sid}?step=1", ok=res.summary or "Discovered")

    @app.post("/sources/{sid}/expose")
    async def source_expose(request: Request, sid: str):
        user, f, _ = await posted(request)
        s = source_or_404(sid)
        key, on = str(f.get("key", "")), f.get("on") == "1"
        p = c.project
        with c.lock:
            if family(s) == "tags":
                it = next((i for i in s.items if i.address == key), None)
                if it:
                    it.exposed = on
                c.changed(user, "item.expose", source=sid, item=key, on=on)
                c.rt.notify_tools_changed()
                return back(f"/sources/{sid}?step=2&obj={quote(key)}")
            o = s.object(key)
            if o is None or o.blocked:
                return back(f"/sources/{sid}?step=2", err="Blocked objects are never exposed")
            o.exposed = on
            t = next((t for t in p.tools if t.source == sid and t.object.lower() == o.name.lower()), None)
            if on and t is None:
                base = "get_" + re.sub(r"[^a-z0-9]+", "_", o.name.split(".")[-1].lower()).strip("_")
                t = new_curated(p.unique_id(base, {x.name for x in p.tools}), sid, o, [x.id for x in p.profiles])
                p.tools.append(t)
            if t is not None:
                t.enabled = on
            c.changed(user, "object.expose", source=sid, object=key, on=on)
        msg = "" if not (on and o.pii_columns()) else \
            f"{o.name} has personal-data columns ({', '.join(sorted(o.pii_columns()))}) - never returned"
        return back(f"/sources/{sid}?step=2&obj={quote(key)}", ok=msg)

    @app.post("/sources/{sid}/restart")
    async def source_restart(request: Request, sid: str):
        await posted(request)
        source_or_404(sid)
        c.rt.start_source(sid)
        return back(f"/sources/{sid}", ok="Reconnecting")

    @app.post("/sources/{sid}/delete")
    async def source_delete(request: Request, sid: str):
        user, _f, _ = await posted(request)
        s = source_or_404(sid)
        with c.lock:
            c.rt.stop_source(sid)
            c.project.sources.remove(s)
            c.project.tools = [t for t in c.project.tools if t.source != sid]
            c.changed(user, "source.delete", source=sid)
        c.rt.notify_tools_changed()
        return back("/", ok=f"Source {s.name} deleted")

    # ---------------------------------------------------------------- tools
    @app.get("/tools", response_class=HTMLResponse)
    def tools(request: Request):
        user, csrf, token = who(request)
        q = request.query_params.get("q", "").strip().lower()
        calls: dict[str, int] = {}
        for r in c.rt.audit.today():
            if r.get("kind") != "admin":
                calls[r.get("tool", "")] = calls.get(r.get("tool", ""), 0) + 1
        rows = []
        for t in c.project.tools:
            src = c.project.source(t.source)
            if q and q not in t.name and q not in t.object.lower() and q not in t.description.lower():
                continue
            rows.append({"t": t, "where": f"{src.name if src else t.source} · {t.object}" if t.kind == "curated"
                         else "ISA-95 model", "calls": calls.get(t.name, 0)})
        return page(request, "tools.html", user, csrf, token, "tools", rows=rows, q=q)

    @app.get("/tools/{name}", response_class=HTMLResponse)
    def tool_page(request: Request, name: str):
        user, csrf, token = who(request)
        t = c.project.tool(name)
        if t is None:
            raise Redirect("/tools?err=" + quote(f"No tool {name}"))
        src = c.project.source(t.source)
        obj = src.object(t.object) if src else None
        d = asdict(t)
        params_yaml = yaml.safe_dump({k: d[k] for k in ("params", "measures", "columns", "order_by", "date_column")},
                                     sort_keys=False, allow_unicode=True)
        try:
            check_publishable(t, c.project)
            check = ""
        except SafetyError as e:
            check = str(e)
        return page(request, "tool.html", user, csrf, token, "tools", t=t, src=src, obj=obj, params_yaml=params_yaml,
                    sig=signature(t), params=_params(t), ann=annotations(t), args=default_args(t), check=check,
                    columns=[col.name for col in obj.columns if not col.pii] if obj else [], risks=RISKS[:4])

    @app.post("/tools/{name}")
    async def tool_save(request: Request, name: str):
        user, f, _ = await posted(request)
        t = c.project.tool(name)
        if t is None:
            return back("/tools", err=f"No tool {name}")
        with c.lock:
            t.title = str(f.get("title", t.title)).strip()
            t.description = str(f.get("description", t.description)).strip()
            if f.get("risk") in RISKS[:4]:
                t.risk = f.get("risk")
            t.profiles = [x for x in f.getlist("profiles") if c.project.profile(x)]
            t.isa95 = str(f.get("isa95", "")).strip().strip("/")
            t.enabled = f.get("enabled") == "on"
            t.sensitive_ok = f.get("sensitive_ok") == "on"
            if t.kind == "curated" and f.get("params_yaml") is not None:
                try:
                    nt = _parse_params(t, str(f.get("params_yaml")), c.project)
                except ValueError as e:
                    return back(f"/tools/{name}", err=str(e))
                for k in ("params", "measures", "columns", "order_by", "date_column"):
                    setattr(t, k, getattr(nt, k))
            c.changed(user, "tool.edit", tool=name)
        return back(f"/tools/{name}", ok="Draft saved - publish to make it live")

    @app.post("/tools/{name}/test")
    async def tool_test(request: Request, name: str):
        user, f, _ = await posted(request)
        try:
            args = parse_args(str(f.get("args", "")))
        except ValueError as e:
            return JSONResponse({"error": f"arguments: {e}", "env": {}, "ms": 0})
        cid = f"web:{user.username}"
        c.rt.track_client(cid, f"Web console ({user.username})", "web", "", "session")
        t0 = time.perf_counter()
        env, err = await run_in_threadpool(lambda: c.rt.call_tool(name, args, client=cid, via="web", draft=True))
        return JSONResponse({"env": env, "error": err, "ms": round((time.perf_counter() - t0) * 1000),
                             "user": user.username}, headers={"Cache-Control": "no-store"})

    @app.post("/publish")
    async def publish(request: Request):
        user, f, _ = await posted(request)
        names = f.getlist("tool") or None
        errors: list = []
        with c.lock:
            c.rt.user = user.username
            done = c.rt.publish(names, errors=errors)
            if done:
                c.changed(user, "tools.publish", tools=done)
        to = str(f.get("back", "/")) if str(f.get("back", "/")).startswith("/") else "/"
        if errors:
            return back(to, err=("Published " + ", ".join(done) + ". " if done else "") +
                        "Held back: " + "; ".join(f"{n} ({e.split(': ', 1)[-1]})" for n, e in errors))
        return back(to, ok=f"Published {len(done)} tool(s) · clients told (tools/list_changed)" if done
                    else "Nothing to publish")

    @app.post("/tools/{name}/unpublish")
    async def unpublish(request: Request, name: str):
        user, _f, _ = await posted(request)
        with c.lock:
            c.project.unpublish(name, user.username)
            c.changed(user, "tool.unpublish", tool=name)
        c.rt.notify_tools_changed()
        return back(f"/tools/{name}", ok="Hidden - clients were told")

    @app.post("/tools/{name}/delete")
    async def tool_delete(request: Request, name: str):
        user, _f, _ = await posted(request)
        t = c.project.tool(name)
        if t is None or t.kind == "generic":
            return back("/tools", err="Generic tools cannot be deleted - remove them from profiles instead")
        with c.lock:
            c.project.tools.remove(t)
            c.changed(user, "tool.delete", tool=name)
        c.rt.notify_tools_changed()
        return back("/tools", ok=f"{name} deleted")

    # ---------------------------------------------------------------- model
    @app.get("/model", response_class=HTMLResponse)
    def model(request: Request):
        user, csrf, token = who(request)
        return page(request, "model.html", user, csrf, token, "model", root=c.rt.model.root,
                    n_tags=len(c.rt.model), n_eq=len(c.rt.model.equipment()))

    # ---------------------------------------------------------------- profiles
    @app.get("/profiles", response_class=HTMLResponse)
    def profiles(request: Request):
        user, csrf, token = who(request)
        p = c.project
        pid = request.query_params.get("p") or (p.profiles[0].id if p.profiles else "")
        prof = p.profile(pid)
        platform = request.query_params.get("platform", PLATFORMS[0])
        if platform not in PLATFORMS:
            platform = PLATFORMS[0]
        key = next((k for k in p.auth.api_keys if k.profile == pid), None)
        url = f"{p.endpoint.base_url()}/{pid}"
        n = len(c.rt.live_tools(pid)) if prof else 0
        from mcx_core.safety import visible
        matrix = []
        for t in p.tools:
            live = t.live()
            matrix.append({"t": t, "cells": [{"pid": x.id, "on": x.id in t.profiles,
                                              "listed": live is not None and visible(live, x, p.licence)}
                                             for x in p.profiles]})
        return page(request, "profiles.html", user, csrf, token, f"profile:{pid}", prof=prof, pid=pid,
                    platform=platform, platforms=PLATFORMS, key=key, url=url, n=n, matrix=matrix,
                    snippet=_snippet(platform, pid, url, c.path), steps=_steps(platform, pid, n),
                    risks=RISKS[:4], keys=[k for k in p.auth.api_keys if k.profile == pid])

    @app.post("/profiles")
    async def profile_new(request: Request):
        user, f, _ = await posted(request)
        pid = str(f.get("id", "")).strip().lower()
        if not re.match(r"^[a-z][a-z0-9_-]{0,30}$", pid) or c.project.profile(pid):
            return back("/profiles", err="Profile id: lower case, unique (e.g. eng, qe, mgr)")
        with c.lock:
            c.project.profiles.append(Profile(pid, str(f.get("title", "")).strip() or pid, "",
                                              f.get("max_risk") if f.get("max_risk") in RISKS[:4] else "R1"))
            c.changed(user, "profile.add", profile=pid)
        return back(f"/profiles?p={pid}", ok=f"Profile {pid} created - endpoint {c.project.endpoint.path}/{pid}")

    @app.post("/profiles/{pid}")
    async def profile_save(request: Request, pid: str):
        user, f, _ = await posted(request)
        x = c.project.profile(pid)
        if x is None:
            return back("/profiles", err="No such profile")
        with c.lock:
            x.title = str(f.get("title", x.title)).strip()
            x.description = str(f.get("description", "")).strip()
            x.scope = str(f.get("scope", "")).strip().strip("/")
            if f.get("max_risk") in RISKS[:4]:
                x.max_risk = f.get("max_risk")
            c.changed(user, "profile.edit", profile=pid)
        c.rt.notify_tools_changed()
        return back(f"/profiles?p={pid}", ok="Profile saved")

    @app.post("/profiles/{pid}/toggle")
    async def profile_toggle(request: Request, pid: str):
        user, f, _ = await posted(request)
        tname, _, target = str(f.get("cell", "")).partition("|")
        t = c.project.tool(tname)
        if t is None or c.project.profile(target) is None:
            return back(f"/profiles?p={pid}", err="Unknown tool or profile")
        with c.lock:
            t.profiles = [x for x in t.profiles if x != target] if target in t.profiles else t.profiles + [target]
            c.changed(user, "tool.profiles", tool=tname, profiles=t.profiles)
        return back(f"/profiles?p={pid}&platform={quote(str(f.get('platform', '')))}",
                    ok=f"{tname}: {'added to' if target in t.profiles else 'removed from'} {target} - publish to make it live")

    @app.post("/profiles/{pid}/delete")
    async def profile_delete(request: Request, pid: str):
        user, _f, _ = await posted(request, "admin")
        with c.lock:
            c.project.profiles = [x for x in c.project.profiles if x.id != pid]
            c.project.auth.api_keys = [k for k in c.project.auth.api_keys if k.profile != pid]
            for t in c.project.tools:
                t.profiles = [x for x in t.profiles if x != pid]
            c.changed(user, "profile.delete", profile=pid)
        c.rt.notify_tools_changed()
        return back("/profiles", ok=f"Profile {pid} and its keys deleted")

    @app.post("/profiles/{pid}/test")
    async def profile_test(request: Request, pid: str):
        await posted(request, "viewer")
        if not c.server.running:
            return JSONResponse({"ok": False, "text": "the MCP server is stopped"})
        s = Session(c.rt, "web:test", pid, "web", "session")
        s.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
        res = s.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        n = len(res["result"]["tools"])
        return JSONResponse({"ok": True, "text": f"Last test {time.strftime('%H:%M')} · endpoint reachable · {n} tools listed"})

    # ---------------------------------------------------------------- endpoint + keys (admin)
    @app.get("/endpoint", response_class=HTMLResponse)
    def endpoint(request: Request):
        user, csrf, token = who(request)
        return page(request, "endpoint.html", user, csrf, token, "endpoint", ep=c.project.endpoint,
                    auth=c.project.auth, audit_cfg=c.project.audit, sessions=len(c.server.sessions))

    @app.post("/endpoint")
    async def endpoint_save(request: Request):
        user, f, _ = await posted(request, "admin")
        ep = c.project.endpoint
        with c.lock:
            try:
                ep.port = int(f.get("port", ep.port))
            except ValueError:
                return back("/endpoint", err="Port must be a number")
            ep.host = str(f.get("host", ep.host)).strip() or "127.0.0.1"
            ep.path = "/" + str(f.get("path", ep.path)).strip().strip("/")
            ep.public_url = str(f.get("public_url", "")).strip()
            ep.tls_cert, ep.tls_key = str(f.get("tls_cert", "")).strip(), str(f.get("tls_key", "")).strip()
            ep.http, ep.stdio = f.get("http") == "on", f.get("stdio") == "on"
            ep.allowed_origins = [x.strip() for x in str(f.get("origins", "")).splitlines() if x.strip()]
            mode = f.get("mode")
            if mode in ("api_key", "none", "oauth"):
                c.project.auth.mode = mode
            c.project.audit.syslog_host = str(f.get("syslog_host", "")).strip()
            c.changed(user, "endpoint.edit", structure=False)
        return back("/endpoint", ok="Saved - restart the MCP server to apply")

    @app.post("/endpoint/restart")
    async def endpoint_restart(request: Request):
        user, _f, _ = await posted(request, "admin")
        c.server.stop()
        c.start_mcp()
        c.rt.audit.write(client=user.username, via="web", kind="admin", tool="endpoint.restart", args={}, rows=0,
                         ms=0, result="ok" if c.server.running else "error", error=c.server.error or None)
        return back("/endpoint", ok="MCP server restarted") if c.server.running else \
            back("/endpoint", err=f"MCP server not started: {c.server.error}")

    @app.post("/keys")
    async def key_new(request: Request):
        user, f, token = await posted(request, "admin")
        prof = str(f.get("profile", ""))
        if prof and not c.project.profile(prof):
            return back("/endpoint", err="Unknown profile")
        with c.lock:
            k, secret = c.project.new_api_key(f"{prof or 'key'}-key", prof, str(f.get("client", "")).strip())
            c.changed(user, "key.create", structure=False, key=k.id, profile=prof)
        c.users.flash(token, f"API key {k.id}: {secret}")
        return back(str(f.get("back", "/endpoint")) if str(f.get("back", "")).startswith("/") else "/endpoint")

    @app.post("/keys/{kid}/rotate")
    async def key_rotate(request: Request, kid: str):
        user, f, token = await posted(request, "admin")
        if not any(k.id == kid for k in c.project.auth.api_keys):
            return back("/endpoint", err="No such key")
        with c.lock:
            secret = c.project.rotate_api_key(kid)
            c.changed(user, "key.rotate", structure=False, key=kid)
        c.users.flash(token, f"API key {kid} (rotated - the old secret no longer works): {secret}")
        return back(str(f.get("back", "/endpoint")) if str(f.get("back", "")).startswith("/") else "/endpoint")

    @app.post("/keys/{kid}/delete")
    async def key_delete(request: Request, kid: str):
        user, _f, _ = await posted(request, "admin")
        with c.lock:
            c.project.auth.api_keys = [k for k in c.project.auth.api_keys if k.id != kid]
            c.changed(user, "key.delete", structure=False, key=kid)
        return back("/endpoint", ok=f"Key {kid} deleted - clients using it are refused")

    # ---------------------------------------------------------------- safety
    @app.get("/safety", response_class=HTMLResponse)
    def safety(request: Request):
        user, csrf, token = who(request)
        srcs = [s for s in c.project.sources if family(s) == "table"]
        return page(request, "safety.html", user, csrf, token, "safety", lim=c.project.limits, srcs=srcs)

    @app.post("/safety/limits")
    async def limits_save(request: Request):
        user, f, _ = await posted(request)
        lim = c.project.limits
        try:
            new = {"max_rows": int(f.get("max_rows")), "max_days": int(f.get("max_days")),
                   "timeout_s": float(f.get("timeout_s")), "rate_per_min": int(f.get("rate_per_min")),
                   "cache_s": float(f.get("cache_s"))}
        except (TypeError, ValueError):
            return back("/safety", err="Limits must be numbers")
        if new["max_rows"] < 1 or new["max_days"] < 1 or new["timeout_s"] <= 0:
            return back("/safety", err="Rows, days and timeout must be above zero")
        with c.lock:
            for k, v in new.items():
                setattr(lim, k, v)
            c.changed(user, "limits.edit", structure=False, **new)
        return back("/safety", ok="Limits saved - they apply to the next call")

    @app.post("/safety/pii")
    async def pii_save(request: Request):
        user, f, _ = await posted(request)
        marked = set(f.getlist("pii"))
        with c.lock:
            for s in c.project.sources:
                if family(s) != "table":
                    continue
                for o in s.objects:
                    for col in o.columns:
                        col.pii = f"{s.id}|{o.name}|{col.name}" in marked
            c.changed(user, "pii.edit", structure=False, columns=len(marked))
        return back("/safety", ok=f"{len(marked)} personal-data columns - never returned")

    # ---------------------------------------------------------------- audit
    @app.get("/audit", response_class=HTMLResponse)
    def audit(request: Request):
        user, csrf, token = who(request)
        qp = request.query_params
        result, q, kind = qp.get("result", ""), qp.get("q", "").strip().lower(), qp.get("kind", "calls")
        recs = list(c.rt.audit.recent)[::-1]
        if kind == "calls":
            recs = [r for r in recs if r.get("kind") != "admin"]
        elif kind == "admin":
            recs = [r for r in recs if r.get("kind") == "admin"]
        if result:
            recs = [r for r in recs if r.get("result") == result]
        if q:
            recs = [r for r in recs if q in json.dumps(r, default=str).lower()]
        page_n = max(1, int(qp.get("page", 1) or 1))
        per = 50
        shown = recs[(page_n - 1) * per: page_n * per]
        sel = None
        if qp.get("seq"):
            sel = next((r for r in c.rt.audit.recent if str(r.get("seq")) == qp.get("seq")), None)
        today = [r for r in c.rt.audit.today() if r.get("kind") != "admin"]
        chain = app.state.chain if getattr(app.state, "chain", None) else None
        return page(request, "audit.html", user, csrf, token, "audit", recs=shown, total=len(recs), page_n=page_n,
                    pages=max(1, (len(recs) + per - 1) // per), result=result, q=q, kind=kind, sel=sel,
                    sel_json=json.dumps(sel, indent=2, ensure_ascii=False, default=str) if sel else "",
                    today=today, blocked=sum(r.get("result") == "blocked" for r in today),
                    errors=sum(r.get("result") == "error" for r in today), chain=chain,
                    folder=str(c.rt.audit.folder))

    @app.post("/audit/verify")
    async def audit_verify(request: Request):
        user, _f, _ = await posted(request, "viewer")
        app.state.chain = await run_in_threadpool(c.rt.audit.verify)
        ok, n, msg = app.state.chain
        return back("/audit", ok=f"Chain verified: {msg}") if ok else back("/audit", err=f"AUDIT CHAIN BROKEN: {msg}")

    @app.get("/audit/export.csv")
    def audit_export(request: Request):
        user, _csrf, _token = who(request, "engineer")
        cols = ["seq", "time", "kind", "client", "profile", "via", "tool", "args", "rows", "ms", "result", "error", "hash"]
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(cols)
        for f in c.rt.audit.files():
            for r in c.rt.audit._read(f):
                w.writerow([json.dumps(r.get(k), default=str) if k == "args" else r.get(k, "") for k in cols])
        c.rt.audit.write(client=user.username, via="web", kind="admin", tool="audit.export", args={}, rows=0, ms=0,
                         result="ok")
        return Response(buf.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": f"attachment; filename=audit_{time.strftime('%Y%m%d')}.csv"})

    # ---------------------------------------------------------------- users (admin) / account
    @app.get("/users", response_class=HTMLResponse)
    def users(request: Request):
        user, csrf, token = who(request, "admin")
        return page(request, "users.html", user, csrf, token, "users", users=c.users.list(), roles=ROLES,
                    role_help=ROLE_HELP)

    @app.post("/users")
    async def user_new(request: Request):
        user, f, _ = await posted(request, "admin")
        try:
            u = c.users.create(str(f.get("username", "")), str(f.get("password", "")), str(f.get("role", "viewer")),
                               str(f.get("name", "")))
        except ValueError as e:
            return back("/users", err=str(e))
        c.rt.audit.write(client=user.username, via="web", kind="admin", tool="user.create",
                         args={"user": u.username, "role": u.role}, rows=0, ms=0, result="ok")
        return back("/users", ok=f"User {u.username} created ({u.role_text})")

    @app.post("/users/{uid}")
    async def user_edit(request: Request, uid: int):
        user, f, token = await posted(request, "admin")
        action = f.get("action")
        target = c.users.get(uid)
        if target is None:
            return back("/users", err="No such user")
        try:
            if action == "role":
                c.users.update(uid, role=str(f.get("role")))
            elif action == "disable":
                c.users.update(uid, active=False)
            elif action == "enable":
                c.users.update(uid, active=True)
            elif action == "password":
                c.users.set_password(uid, str(f.get("password", "")), token if uid == user.id else None)
            elif action == "delete":
                if uid == user.id:
                    return back("/users", err="You cannot delete yourself")
                c.users.delete(uid)
            else:
                return back("/users", err="Unknown action")
        except ValueError as e:
            return back("/users", err=str(e))
        c.rt.audit.write(client=user.username, via="web", kind="admin", tool=f"user.{action}",
                         args={"user": target.username, **({"role": f.get("role")} if action == "role" else {})},
                         rows=0, ms=0, result="ok")
        return back("/users", ok=f"{target.username}: {action} done")

    @app.get("/account", response_class=HTMLResponse)
    def account(request: Request):
        user, csrf, token = who(request)
        return page(request, "account.html", user, csrf, token, "account", role_help=ROLE_HELP)

    @app.post("/account/password")
    async def account_password(request: Request):
        user, f, token = await posted(request, "viewer")
        from mcx_web.users import check_password
        with c.users._db() as db:
            stored = db.execute("SELECT pw FROM users WHERE id=?", (user.id,)).fetchone()[0]
        if not check_password(str(f.get("current", "")), stored):
            return back("/account", err="Current password is wrong")
        if f.get("new") != f.get("new2"):
            return back("/account", err="New passwords differ")
        try:
            c.users.set_password(user.id, str(f.get("new", "")), token)
        except ValueError as e:
            return back("/account", err=str(e))
        c.rt.audit.write(client=user.username, via="web", kind="admin", tool="user.password", args={}, rows=0, ms=0,
                         result="ok")
        return back("/account", ok="Password changed - your other sessions were signed out")

    # ---------------------------------------------------------------- licence
    @app.get("/licence", response_class=HTMLResponse)
    def licence(request: Request):
        user, csrf, token = who(request)
        return page(request, "licence.html", user, csrf, token, "licence", lic=c.project.licence,
                    editions=Licence.EDITIONS)

    @app.post("/licence")
    async def licence_save(request: Request):
        user, f, _ = await posted(request, "admin")
        lic = c.project.licence
        with c.lock:
            if f.get("edition") in Licence.EDITIONS:
                lic.set_edition(f.get("edition"))
            lic.holder, lic.expires = str(f.get("holder", "")).strip(), str(f.get("expires", "")).strip()
            c.changed(user, "licence.edit", edition=lic.edition)
        c.rt.notify_tools_changed()
        return back("/licence", ok="Licence saved")

    return app


# ======================================================================= helpers
def ready_to_publish(tools, project) -> list:
    """Changed tools that pass the safety rules (a held-back R2 draft is not "pending")."""
    out = []
    for t in tools:
        if t.changed:
            try:
                check_publishable(t, project)
                out.append(t)
            except SafetyError:
                pass
    return out


def _n(v):
    return "" if v is None else f"{v:g}"


def _short(p: str) -> str:
    return p.upper() if len(p) <= 2 else p.capitalize()


def _params(t) -> list[dict]:
    sch = input_schema(t)["properties"]
    out = []
    for p in t.params:
        s = sch[p.name]
        notes = " · ".join(s["enum"]) if p.group else (p.description or "")
        if p.required and "required" not in notes.lower():
            notes = "required" + (", " + notes if notes else "")
        out.append({"name": p.name, "type": "enum" if p.group else p.type, "notes": notes})
    return out


def _settings(s, schema) -> list[dict]:
    out = []
    for f in schema + [{"key": "poll_s", "label": "Poll interval (s)", "type": "float", "default": 10, "extra": None,
                        "tip": "Health check / live cache refresh"}]:
        v = s.settings.get(f["key"], f["default"])
        choices = list(f["extra"].items()) if isinstance(f.get("extra"), dict) else \
            [(x, x) for x in f["extra"]] if f["type"] == "choice" and f.get("extra") else []
        out.append({**f, "value": "" if f["type"] == "password" else ("" if v is None else v),
                    "has_secret": f["type"] == "password" and bool(v), "choices": choices})
    return out


def default_args(tool) -> str:
    if tool is None:
        return ""
    today = dt.date.today()
    parts = []
    for p in tool.params:
        if p.type in ("date", "datetime") and p.required:
            d = today - dt.timedelta(days=6) if p.op in (">=", ">") or p.name in ("from", "date_from") else today
            v = d.isoformat() if p.type == "date" else dt.datetime.combine(d, dt.time()).isoformat()
            if p.name == "to":
                v = dt.datetime.now().replace(microsecond=0).isoformat()
            parts.append(f'{p.name}="{v}"')
        elif p.values and p.name == "line":
            parts.append(f'{p.name}="{p.values[min(1, len(p.values) - 1)]}"')
    return ", ".join(parts)


def parse_args(text: str) -> dict:
    text = text.strip()
    if text.startswith("{"):
        return json.loads(text)
    out = {}
    for m in re.finditer(r'(\w+)\s*=\s*("([^"]*)"|\'([^\']*)\'|[^,]*)', text):
        out[m.group(1)] = m.group(3) if m.group(3) is not None else (
            m.group(4) if m.group(4) is not None else m.group(2).strip())
    return out


def _parse_params(t, text: str, project) -> ToolDef:
    try:
        data = yaml.safe_load(text) or {}
        nt = ToolDef.from_dict({"name": t.name, **data})
    except Exception as e:
        raise ValueError(f"Parameters not valid: {e}") from None
    src = project.source(t.source)
    obj = src.object(t.object) if src else None
    cols = {col.name.lower() for col in obj.columns} if obj else set()
    used = {p.column.lower() for p in nt.params if p.column} | {m.column.lower() for m in nt.measures} | \
           {x.lower() for x in nt.columns}
    for p in nt.params:
        for cs in p.group.values():
            used |= {x.lower() for x in cs}
        if p.op not in ("=", ">=", "<=", ">", "<", "in"):
            raise ValueError(f"{p.name}: operator {p.op!r} not allowed")
    bad = sorted(u for u in used if cols and u not in cols and u != "*")
    if bad:
        raise ValueError("Unknown column(s): " + ", ".join(bad))
    return nt


def _snippet(platform: str, pid: str, url: str, path: str) -> str:
    if platform == "Claude Desktop":
        return json.dumps({"mcpServers": {f"mcx-{pid}": {"command": "mcx-adapter",
                                                         "args": ["stdio", path, "--profile", pid]}}}, indent=2)
    if platform == "ContextForge":
        return json.dumps({"name": f"mcx-{pid}", "url": url, "transport": "STREAMABLEHTTP", "auth_type": "bearer",
                           "auth_token": "<your API key>", "description": f"MCX Adapter - profile {pid}"}, indent=2)
    if platform == "Docker MCP Gateway":
        return (f"registry:\n  mcx-{pid}:\n    title: MCX Adapter ({pid})\n    type: remote\n    remote:\n"
                f"      transport_type: streamable-http\n      url: {url}\n      headers:\n"
                f"        Authorization: \"Bearer ${{secrets.mcx.api_key}}\"\n")
    return f"Server URL: {url}\nTransport: Streamable HTTP\nAuthorization: Bearer <your API key>\n"


def _steps(platform: str, pid: str, n: int) -> list[str]:
    return {
        "Copilot Studio": ["In your agent, add a new tool of type Model Context Protocol.",
                           "Paste the server URL and choose API key authentication (header Authorization, value Bearer <key>).",
                           "Enter the key, then Create.",
                           f"Copilot lists the {n} tools of profile {pid}; every call shows up in the audit log."],
        "ContextForge": ["In ContextForge Admin, add an MCP server (gateway).",
                         "Transport Streamable HTTP, the URL below, bearer token with the key.",
                         f"Put the {n} tools of profile {pid} into a virtual server for the team.",
                         "ContextForge users and the adapter's own limits both apply; calls appear in the audit log."],
        "Docker MCP Gateway": ["Add a catalog entry for a remote server (Copy gives the YAML).",
                               "Store the key as a gateway secret: docker mcp secret set mcx.api_key=<key>.",
                               f"Enable it: docker mcp server enable mcx-{pid}.",
                               f"Clients of the gateway see the {n} tools of profile {pid}."],
        "Claude Desktop": ["Claude Desktop runs the adapter on the same PC over stdio.",
                           "Add the JSON below to claude_desktop_config.json (Settings > Developer > Edit config).",
                           f"Restart Claude Desktop; the {n} tools of profile {pid} appear.",
                           "For claude.ai, add a custom connector with the server URL over HTTPS."],
    }[platform]


__all__ = ["Console", "create_app", "Markup"]
