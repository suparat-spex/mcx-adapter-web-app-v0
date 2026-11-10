"""
python -m mcx_web PROJECT.mcx | "Project name" [--host 127.0.0.1] [--port 8810]

Runs the adapter (sources, MCP endpoint on the project's port, 8811 by default) and the web console
(8810 by default) in one process. First visit with no users: create the first admin account.
  --demo              create / open the demo plant project (also the default with no project)
  --create-admin NAME create an admin from the command line (asks for the password)
A project file or name that does not exist yet is created as a new, empty project
("My plant" = <user data>/Projects/My plant.mcx).
"""
from __future__ import annotations

import argparse
import getpass
import logging
import sys
from pathlib import Path

if __package__ in (None, ""):                       # run as a script (IDE Run button): put src/ on the path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("pymodbus").setLevel(logging.CRITICAL)
    ap = argparse.ArgumentParser(prog="python -m mcx_web", description="MCX Adapter with its web console")
    ap.add_argument("project", nargs="?", help="project file (.mcx)")
    ap.add_argument("--host", default="127.0.0.1", help="console address (0.0.0.0 = every network card)")
    ap.add_argument("--port", type=int, default=8810, help="console port (default 8810)")
    ap.add_argument("--demo", action="store_true", help="create / open the demo plant project")
    ap.add_argument("--create-admin", metavar="NAME")
    ap.add_argument("--cert", help="TLS certificate (PEM) for the console")
    ap.add_argument("--key", help="TLS key (PEM) for the console")
    a = ap.parse_args(argv)
    from mcx_core import paths
    project = a.project
    if a.demo or not project:
        f = paths.demo_dir() / "Plant adapter.mcx"
        if not f.exists():
            from mcx_core import demo
            f, keys = demo.create(paths.demo_dir())
            print(f"Demo project created: {f}\n  API keys (shown once): " + ", ".join(f"{k}={v}" for k, v in keys.items()))
        project = str(f)
    else:
        f = Path(project)
        if f.suffix.lower() != ".mcx" and f.parent == Path("."):    # a bare name: "My plant"
            f = paths.projects_dir() / f"{project}.mcx"
        if not f.exists():
            from mcx_core.config import Project
            Project.new(f.stem).save(f)
            print(f"New empty project created: {f.resolve()}")
        project = str(f)
    if a.create_admin:
        from mcx_web.users import UserStore
        store = UserStore(Path(project).resolve().parent / "mcx_web.db")
        pw = getpass.getpass(f"Password for {a.create_admin}: ")
        u = store.create(a.create_admin, pw, "admin")
        print(f"Admin {u.username} created")
        return
    import uvicorn
    from mcx_web.app import Console, create_app
    console = Console(project)
    app = create_app(console)
    mcp = console.server.url() if console.server.running else f"not started ({console.server.error})"
    print(f"MCX Adapter console  http{'s' if a.cert else ''}://{a.host}:{a.port}   MCP endpoint  {mcp}")
    try:
        uvicorn.run(app, host=a.host, port=a.port, log_level="warning", ssl_certfile=a.cert, ssl_keyfile=a.key)
    finally:
        console.stop()


if __name__ == "__main__":
    main()
