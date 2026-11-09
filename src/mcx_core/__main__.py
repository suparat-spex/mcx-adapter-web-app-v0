"""
python -m mcx_core <command>

  serve  PROJECT.mcx [--host H --port P]   run the adapter: Streamable HTTP endpoint (+ hot reload)
  stdio  PROJECT.mcx [--profile ID]        MCP over stdin / stdout (Claude Desktop, .mcpb)
  demo   [FOLDER]                          create the demo plant project (prints the API keys once)
  key    PROJECT.mcx NAME --profile ID     add an API key (prints the secret once)
  audit  PROJECT.mcx [--export FILE.csv]   verify the audit hash chain (and export it)
  tools  PROJECT.mcx [--profile ID]        list the published tools as MCP clients see them
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

from mcx_core import APP_NAME, __version__


def _load(path):
    from mcx_core.config import Project
    return Project.load(path)


def cmd_serve(a):
    from mcx_core.mcp import McpServer
    from mcx_core.runtime import Runtime
    p = _load(a.project)
    if a.host:
        p.endpoint.host = a.host
    if a.port:
        p.endpoint.port = a.port
    rt = Runtime(p, a.project, user="service")
    rt.start()
    srv = McpServer(rt)
    srv.start()
    logging.info("%s %s - %s (profiles: %s)", APP_NAME, __version__, srv.url(),
                 ", ".join(x.id for x in p.profiles) or "-")
    mtime = os.path.getmtime(a.project)
    try:
        while True:
            time.sleep(2)
            m = os.path.getmtime(a.project)
            if m != mtime:                     # hot reload
                mtime = m
                try:
                    np = _load(a.project)
                    np.endpoint = rt.project.endpoint
                    rt.reload(np)
                    logging.info("reloaded %s", a.project)
                except Exception as e:
                    logging.error("reload failed, keeping the running config: %s", e)
    except KeyboardInterrupt:
        pass
    finally:
        srv.stop()
        rt.stop()


def cmd_stdio(a):
    from mcx_core.mcp import run_stdio
    from mcx_core.runtime import Runtime
    rt = Runtime(_load(a.project), a.project, user="stdio")
    rt.start()
    try:
        run_stdio(rt, a.profile)
    finally:
        rt.stop()


def cmd_demo(a):
    from mcx_core import demo
    path, keys = demo.create(a.folder)
    print(f"Demo project: {path}")
    for k, v in keys.items():
        print(f"  API key {k}: {v}")
    print("Run:  python -m mcx_core serve \"%s\"" % path)


def cmd_key(a):
    p = _load(a.project)
    if a.profile and not p.profile(a.profile):
        sys.exit(f"no profile {a.profile!r}")
    k, secret = p.new_api_key(a.name, a.profile or "", a.client or "")
    p.save(a.project)
    print(f"{k.id}: {secret}   (shown once; profile {k.profile or 'any'})")


def cmd_audit(a):
    from mcx_core import paths
    from mcx_core.audit import AuditLog
    log = AuditLog(paths.audit_dir(a.project))
    ok, n, msg = log.verify()
    print(("OK  " if ok else "FAIL  ") + msg)
    if a.export:
        log.export_csv(a.export)
        print(f"exported -> {a.export}")
    sys.exit(0 if ok else 2)


def cmd_tools(a):
    from mcx_core.runtime import Runtime
    rt = Runtime(_load(a.project), a.project)
    print(json.dumps(rt.mcp_tools(a.profile), indent=2, ensure_ascii=False))


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    ap = argparse.ArgumentParser(prog="python -m mcx_core", description=f"{APP_NAME} {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve"); s.add_argument("project"); s.add_argument("--host"); s.add_argument("--port", type=int)
    s.set_defaults(fn=cmd_serve)
    s = sub.add_parser("stdio"); s.add_argument("project"); s.add_argument("--profile"); s.set_defaults(fn=cmd_stdio)
    s = sub.add_parser("demo"); s.add_argument("folder", nargs="?"); s.set_defaults(fn=cmd_demo)
    s = sub.add_parser("key"); s.add_argument("project"); s.add_argument("name"); s.add_argument("--profile")
    s.add_argument("--client"); s.set_defaults(fn=cmd_key)
    s = sub.add_parser("audit"); s.add_argument("project"); s.add_argument("--export"); s.set_defaults(fn=cmd_audit)
    s = sub.add_parser("tools"); s.add_argument("project"); s.add_argument("--profile"); s.set_defaults(fn=cmd_tools)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
