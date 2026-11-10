# MCX Adapter

SPEX product: connects to manufacturing data sources and serves them as safe, standard MCP tools
behind any MCP gateway / AI platform or directly to Claude. Must be **safe on its own**. Engine
`mcx_core` (no Qt) + **web console `mcx_web`** (FastAPI + Jinja2, the main UI, local accounts and
roles) + optional desktop app `mcx_studio` (PySide6). Both UIs use the Spex theme carried over from
NX Logger. Formerly NX Logger (branch `nx-connectivity-categories`, tag `v6-mqdaq`).

## Layout (src/)

| Path | Owns |
|---|---|
| `mcx_core/config.py` | Project (.mcx YAML): sources (settings, discovered objects / items), tools (draft + `published` snapshot, version history), profiles, endpoint, auth (API keys: SHA-256 hashes only), limits, licence, audit cfg. `Project.publish` |
| `mcx_core/adapters/` | One plug-in per source type: `base` (contract: SCHEMA, connect, ping/health, discover, read, history, select(Query)), `sql` (SQL Server / Postgres / MySQL / SQLite, identifier quoting, bound values, PII / block hints), `files` (CSV / Excel / REST via in-memory SQLite), `docs` (PDF / Word .docx: passages page / section / para / text + Word tables; search via the `contains` op), `google` (Google Sheets / Docs via a service account, read-only scopes), `tags` (sim, Modbus, OPC UA, MQTT). Registry in `__init__` |
| `mcx_core/model.py` | ISA-95 tree from exposed items; `in_scope` for profile scopes |
| `mcx_core/tools.py` | Generic tool defs, JSON schemas, `check_args`, `curated_query`, answer `envelope` |
| `mcx_core/safety.py` | Risk classes / annotations, `check_publishable`, `visible`, `coerce`, range check, RateLimiter |
| `mcx_core/audit.py` | Hash-chained JSONL audit (`verify`, CSV export, syslog) |
| `mcx_core/runtime.py` | The service: source threads (health, poll, history ring), `call_tool` (rate limit, timeout, audit), `publish`, clients, `rev` counter |
| `mcx_core/mcp.py` | MCP JSON-RPC Session, Streamable HTTP server (profiles, auth, SSE notices, /health, /metrics), `run_stdio` |
| `mcx_core/demo.py` | Demo plant: smc_demo SQLite, Excel, simulator, Modbus meters, tools, profiles, keys |
| `mcx_web/app.py` | Console (runtime + MCP server in one process; `changed()` = save project + audit), every route; helpers (default_args, parse_args, snippets) |
| `mcx_web/users.py` | Local accounts (SQLite `mcx_web.db` next to the project): PBKDF2 passwords, roles viewer < engineer < admin, sessions + CSRF, lockout, one-time flash |
| `mcx_web/templates/`, `static/` | Jinja2 pages (base = top bar brand / search / actions, nav with project info, main, right panel, footer); `_detail_*.html` = right-panel fragments (`/detail/...`); `_drawer.html` = add / edit forms in the drawer (`/drawer/<kind>/<key>`, posting to the normal routes with `back=`); `_macros` `pager` + `Pager` in app.py = server-side pagination; `mcx.css` = Spex tokens light / dark; `mcx.js` = theme, search (Ctrl K), resizable / closable right panel, row details, trees, confirm dialog, test call, live status (no inline scripts - CSP) |
| `mcx_studio/main_window.py` | `AdapterWindow`: tree, document tabs, Properties targets (`_prop_*`), Runtime dock, actions |
| `mcx_studio/widgets/` | `common` (Page, panel, table, Kpi, KpiRow, Columns, StepBar, Worker), `overview`, `source_page` (Connect > Discover > Expose > Review & publish), `profiles_page`, `pages` (Tools, Model, Safety, Endpoint, Audit, Licence, Alarms), `property_grid` |
| `mcx_studio/theme/` | `ui_kit` (tokens `T`, QSS, AppWindow), `app_style` (extra QSS), `DESIGN.md` |

## Commands

```
pip install -e ".[dev]"
python -m mcx_web --demo              # adapter + web console http://127.0.0.1:8810
python -m mcx_studio                  # optional desktop app
python -m mcx_core demo|serve|stdio|key|audit|tools ...
python -m mcx_core.sim.factory_energy # Modbus power meters 127.0.0.1:5020
python -m pytest -q
python -m pyflakes src tests
```

## Rules

- `mcx_core` never imports Qt. Only `mcx_core/paths.py` (data) and `mcx_studio/paths.py` (resources) know file locations; only `icons.py` loads icons.
- **No free SQL / protocol commands, ever.** Tools take typed parameters; table reads go through `adapters.base.Query` (columns must exist in the discovered object and are quoted, values are bound). No write path in v1 (R4 / R5 refuse to publish).
- Every tool call goes through `Runtime.call_tool` (rate limit, args check, timeout, audit). Every answer uses `tools.envelope`. PII columns are dropped from results and block publishing.
- MCP edge stays pure standard MCP (gateways need configuration only). Clients see the *published snapshot*; edits are drafts until Publish, which sends `tools/list_changed`.
- Secrets: API keys stored as hashes, shown once; source passwords may be `${ENV}`.
- New source type: subclass `Adapter` (FAMILY table | tags, SCHEMA, ping, discover, read / select), add to `ADAPTERS`, test against a local server / file in `tests/`.
- Web console: every POST goes through `posted(request, role)` (role + CSRF); every config change through `Console.changed()` (saves the project, audits with the user); role checks on the server, never only by hiding buttons. No CDN / web fonts / inline scripts. Colours only via the CSS variables in `mcx.css`; check light + dark and a 1280 px window.
- Studio: colours only from theme tokens (dark + light, Fusion). `fusion_qss` drops `QPushButton…`, `QLineEdit…`, `QPlainTextEdit…` QSS lines - style page widgets with `QAbstractButton#…`, `QWidget#…`, `QAbstractScrollArea#…`. Pages: `refresh()` = full rebuild (structure changed), `tick()` = live state only (never rebuild editors). Blocking work via `widgets.common.Worker`. Toolbars never move (`fixed_width`). Must work at 1280 x 800 (`Columns`, `KpiRow`).
- Use git branches for new versions. Project files are YAML `.mcx`; new fields need defaults in the dataclasses (`from_dict` ignores unknown keys).
- Put scratch files in the session scratchpad, not the repo.
