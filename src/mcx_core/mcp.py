"""
mcp.py - the MCP endpoint: pure standard MCP so any gateway / client needs only configuration. No Qt.

Transports
  Streamable HTTP   POST <path>[/<profile>] JSON-RPC (single or batch) -> application/json
                    GET  same path, Accept: text/event-stream -> server notifications (SSE)
                    DELETE ends the session (Mcp-Session-Id)
                    GET /health (JSON), GET /metrics (Prometheus text)
  stdio             one JSON-RPC message per line (Claude Desktop, .mcpb)

Methods: initialize, notifications/initialized, ping, tools/list, tools/call. Change notice:
notifications/tools/list_changed when an admin publishes or hides a tool.

Auth: api_key - `Authorization: Bearer <key>` or `X-API-Key: <key>`, compared by SHA-256 hash; a key
bound to a profile may use only that profile. none - loopback clients only. OAuth 2.1 bearer tokens
are planned (the server refuses to start with mode oauth rather than run open).
Tool profiles: /mcp/eng, /mcp/qe ... each lists only its tools (a gateway without per-tool
permissions still gives each team only its tools). Browser Origins are checked (DNS rebinding).
"""
from __future__ import annotations

import hmac
import ipaddress
import json
import logging
import queue
import ssl
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from mcx_core import APP_NAME, MCP_PROTOCOL, MCP_PROTOCOLS, __version__
from mcx_core.config import hash_secret

log = logging.getLogger(__name__)

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL = -32700, -32600, -32601, -32602, -32603

INSTRUCTIONS = ("Plant data from MCX Adapter. Use browse_model / list_assets to find ISA-95 paths, read_tag "
                "for current values, query_history for trends and the curated tools (get_oee ...) for KPIs. "
                "Every answer carries unit, time, quality, ISA-95 path and source: cite the source. Read-only.")


class Session:
    """One MCP conversation (HTTP session or the stdio pipe)."""

    def __init__(self, runtime, client: str, profile: str | None, via: str, auth: str):
        self.id = uuid.uuid4().hex
        self.rt, self.client, self.profile, self.via, self.auth = runtime, client, profile, via, auth
        self.protocol = MCP_PROTOCOL
        self.client_name = ""
        self.streams: list[queue.Queue] = []       # open SSE streams
        self.last = time.time()

    # ---- JSON-RPC
    def handle(self, msg) -> dict | None:
        self.last = time.time()
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            return _error(msg.get("id") if isinstance(msg, dict) else None, INVALID_REQUEST, "not JSON-RPC 2.0")
        mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
        if method is None:                         # a response from the client (we send no requests)
            return None
        is_note = "id" not in msg
        try:
            res = self._dispatch(method, params)
        except _RpcError as e:
            return None if is_note else _error(mid, e.code, str(e))
        except Exception as e:                     # never kill the session
            log.exception("MCP %s", method)
            return None if is_note else _error(mid, INTERNAL, str(e))
        return None if is_note else {"jsonrpc": "2.0", "id": mid, "result": res}

    def _dispatch(self, method, params):
        if method == "initialize":
            v = params.get("protocolVersion")
            self.protocol = v if v in MCP_PROTOCOLS else MCP_PROTOCOL
            info = params.get("clientInfo") or {}
            self.client_name = str(info.get("name", ""))[:80]
            self.rt.track_client(self.client, self.client_name, self.via, self.profile or "", self.auth)
            return {"protocolVersion": self.protocol,
                    "capabilities": {"tools": {"listChanged": True}},
                    "serverInfo": {"name": "mcx-adapter", "title": APP_NAME, "version": __version__},
                    "instructions": INSTRUCTIONS}
        if method.startswith("notifications/"):
            return None
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": self.rt.mcp_tools(self.profile)}
        if method == "tools/call":
            name = params.get("name")
            if not isinstance(name, str):
                raise _RpcError(INVALID_PARAMS, "tools/call needs a tool name")
            args = params.get("arguments") or {}
            if not isinstance(args, dict):
                raise _RpcError(INVALID_PARAMS, "arguments must be an object")
            env, err = self.rt.call_tool(name, args, client=self.client, profile=self.profile, via=self.via)
            text = json.dumps(env, ensure_ascii=False, default=str)
            out = {"content": [{"type": "text", "text": text}], "isError": bool(err)}
            if not err:
                out["structuredContent"] = env
            return out
        raise _RpcError(METHOD_NOT_FOUND, f"method not found: {method}")

    # ---- server -> client
    def notify(self, method: str, params: dict | None = None):
        msg = {"jsonrpc": "2.0", "method": method}
        if params:
            msg["params"] = params
        for q in list(self.streams):
            q.put(msg)


class _RpcError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _error(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


# ======================================================================= HTTP
class _Http(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            return                                 # client went away (closed SSE stream ...)
        log.exception("HTTP request from %s", client_address[0])


class McpServer:
    def __init__(self, runtime):
        self.rt = runtime
        self.sessions: dict[str, Session] = {}
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.error = ""
        runtime.list_listeners.append(self._tools_changed)

    @property
    def running(self) -> bool:
        return self.httpd is not None

    def url(self, profile: str = "") -> str:
        base = self.rt.project.endpoint.base_url()
        return f"{base}/{profile}" if profile else base

    def start(self):
        ep, auth = self.rt.project.endpoint, self.rt.project.auth
        if auth.mode == "oauth":
            raise RuntimeError("OAuth 2.1 is not available yet - use API keys (auth mode api_key)")
        if auth.mode == "none" and not _loopback(ep.host):
            raise RuntimeError("auth mode 'none' is allowed only on 127.0.0.1 / localhost")
        server = self

        class Handler(_Handler):
            srv = server
        self.httpd = _Http((ep.host, int(ep.port)), Handler)
        if ep.tls_cert and ep.tls_key:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(ep.tls_cert, ep.tls_key)
            self.httpd.socket = ctx.wrap_socket(self.httpd.socket, server_side=True)
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="mcp-http", daemon=True)
        self.thread.start()
        self.error = ""

    def stop(self):
        if self.httpd:
            for s in self.sessions.values():
                for q in s.streams:
                    q.put(None)
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
        self.sessions.clear()

    def _tools_changed(self):
        for s in list(self.sessions.values()):
            s.notify("notifications/tools/list_changed")

    # ---- auth: (client id, profile, auth label) or raise PermissionError
    def authenticate(self, headers, peer: str, profile: str | None):
        auth = self.rt.project.auth
        if profile is not None and self.rt.project.profile(profile) is None:
            raise LookupError(f"no profile {profile!r}")
        if auth.mode == "none":
            if not _loopback(peer):
                raise PermissionError("only local clients may connect without a key")
            return f"local:{peer}", profile, "local"
        token = headers.get("X-API-Key") or ""
        h = headers.get("Authorization") or ""
        if h.lower().startswith("bearer "):
            token = h[7:].strip()
        if not token:
            raise PermissionError("missing API key (Authorization: Bearer <key>)")
        digest = hash_secret(token)
        for k in auth.api_keys:
            if hmac.compare_digest(k.hash, digest):
                if k.profile and profile and k.profile != profile:
                    raise PermissionError(f"this key is for profile {k.profile!r}")
                return k.id, profile or (k.profile or None), "API key"
        raise PermissionError("invalid API key")

    def check_origin(self, origin: str | None) -> bool:
        if not origin:
            return True
        host = urlparse(origin).hostname or ""
        return _loopback(host) or origin in self.rt.project.endpoint.allowed_origins


def _loopback(host: str) -> bool:
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class _Handler(BaseHTTPRequestHandler):
    srv: McpServer
    protocol_version = "HTTP/1.1"
    server_version = "mcx-adapter"

    def log_message(self, fmt, *args):
        log.debug("http %s - " + fmt, self.client_address[0], *args)

    # ---- helpers
    def _send(self, code: int, body=None, ctype="application/json", headers: dict | None = None):
        data = b"" if body is None else (body if isinstance(body, bytes) else
                                         (json.dumps(body, default=str) if ctype == "application/json"
                                          else str(body)).encode("utf-8"))
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        if code == 401:
            self.send_header("WWW-Authenticate", 'Bearer realm="mcx-adapter"')
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if data:
            self.wfile.write(data)

    def _route(self):
        """('mcp', profile or None) | ('health', None) | ('metrics', None) | (None, None)."""
        path = urlparse(self.path).path.rstrip("/")
        base = self.srv.rt.project.endpoint.path.rstrip("/") or "/mcp"
        if path == "/health":
            return "health", None
        if path == "/metrics":
            return "metrics", None
        if path == base:
            return "mcp", None
        if path.startswith(base + "/") and "/" not in path[len(base) + 1:]:
            return "mcp", path[len(base) + 1:]
        return None, None

    def _session(self, profile):
        if not self.srv.check_origin(self.headers.get("Origin")):
            self._send(403, {"error": "origin not allowed"})
            return None
        try:
            client, prof, auth = self.srv.authenticate(self.headers, self.client_address[0], profile)
        except LookupError as e:
            self._send(404, {"error": str(e)})
            return None
        except PermissionError as e:
            self._send(401, {"error": str(e)})
            return None
        return client, prof, auth

    # ---- verbs
    def do_GET(self):
        kind, profile = self._route()
        rt = self.srv.rt
        if kind == "health":
            on, n = rt.sources_online()
            self._send(200, {"status": "ok", "server": APP_NAME, "version": __version__,
                             "sources_online": on, "sources": n, "tools": len(rt.live_tools())})
            return
        if kind == "metrics":
            s = rt.summary()
            lines = [f"mcx_sources_online {s['sources_online']}", f"mcx_sources {s['sources']}",
                     f"mcx_tools_published {s['tools']}", f"mcx_calls_today {s['calls_today']}",
                     f"mcx_blocked_today {s['blocked_today']}", f"mcx_clients {s['clients']}"]
            for sid, h in rt.health.items():
                lines.append(f'mcx_source_up{{source="{sid}"}} {int(h.state == "online")}')
            self._send(200, "\n".join(lines) + "\n", "text/plain; version=0.0.4")
            return
        if kind != "mcp":
            self._send(404, {"error": "not found"})
            return
        if "text/event-stream" not in (self.headers.get("Accept") or ""):
            self._send(405, {"error": "GET needs Accept: text/event-stream"}, headers={"Allow": "POST, GET, DELETE"})
            return
        ok = self._session(profile)
        if not ok:
            return
        sess = self.srv.sessions.get(self.headers.get("Mcp-Session-Id") or "")
        if sess is None:
            self._send(400 if not self.headers.get("Mcp-Session-Id") else 404, {"error": "unknown session"})
            return
        q: queue.Queue = queue.Queue()
        sess.streams.append(q)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Mcp-Session-Id", sess.id)
        self.end_headers()
        try:
            while True:
                try:
                    msg = q.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                    continue
                if msg is None:
                    break
                self.wfile.write(f"event: message\ndata: {json.dumps(msg)}\n\n".encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            if q in sess.streams:
                sess.streams.remove(q)
        self.close_connection = True

    def do_DELETE(self):
        kind, profile = self._route()
        if kind != "mcp":
            self._send(404, {"error": "not found"})
            return
        if not self._session(profile):
            return
        s = self.srv.sessions.pop(self.headers.get("Mcp-Session-Id") or "", None)
        if s:
            for q in s.streams:
                q.put(None)
        self._send(200 if s else 404, {})

    def do_POST(self):
        kind, profile = self._route()
        if kind != "mcp":
            self._send(404, {"error": "not found"})
            return
        ok = self._session(profile)
        if not ok:
            return
        client, prof, auth = ok
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 1_000_000:
                self._send(413, {"error": "request too large"})
                return
            body = json.loads(self.rfile.read(n).decode("utf-8") or "null")
        except (ValueError, UnicodeDecodeError):
            self._send(400, _error(None, PARSE_ERROR, "invalid JSON"))
            return
        msgs = body if isinstance(body, list) else [body]
        sid = self.headers.get("Mcp-Session-Id")
        sess = self.srv.sessions.get(sid or "")
        headers = {}
        if any(isinstance(m, dict) and m.get("method") == "initialize" for m in msgs):
            sess = Session(self.srv.rt, client, prof, "HTTP", auth)
            self.srv.sessions[sess.id] = sess
            self._prune()
        elif sid and sess is None:
            self._send(404, _error(None, INVALID_REQUEST, "unknown or expired session - initialize again"))
            return
        elif sess is None:                          # stateless client: a session per request
            sess = Session(self.srv.rt, client, prof, "HTTP", auth)
            self.srv.rt.track_client(client, "", "HTTP", prof or "", auth)
        if sess.client != client:
            self._send(403, {"error": "session belongs to another client"})
            return
        headers["Mcp-Session-Id"] = sess.id
        out = [r for r in (sess.handle(m) for m in msgs) if r is not None]
        if not out:
            self._send(202, None, headers=headers)
        else:
            self._send(200, out if isinstance(body, list) else out[0], headers=headers)

    def _prune(self, idle_s: float = 3600):
        now = time.time()
        for k, s in list(self.srv.sessions.items()):
            if now - s.last > idle_s and not s.streams:
                self.srv.sessions.pop(k, None)


# ======================================================================= stdio
def run_stdio(runtime, profile: str | None = None, stdin=None, stdout=None):
    """Serve MCP over stdin / stdout until EOF. Logs go to stderr (stdout is the protocol)."""
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    lock = threading.Lock()

    def send(msg):
        with lock:
            stdout.write(json.dumps(msg, ensure_ascii=False, default=str) + "\n")
            stdout.flush()

    sess = Session(runtime, "stdio", profile, "stdio", "local")
    runtime.list_listeners.append(lambda: send({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}))
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            send(_error(None, PARSE_ERROR, "invalid JSON"))
            continue
        for m in (msg if isinstance(msg, list) else [msg]):
            r = sess.handle(m)
            if r is not None:
                send(r)
