# MCX Adapter

"Kepware for AI": MCX Adapter connects to manufacturing data sources and serves them as **safe,
standard MCP tools**. It works behind any MCP gateway or AI platform (IBM ContextForge, Docker MCP
Gateway, cloud AI gateways, Microsoft Copilot Studio) or directly with Claude. The customer keeps
their gateway and LLM; the adapter gives it plant data, and is **safe on its own**.

- **Sources:** SQL Server / PostgreSQL / MySQL / SQLite tables and views, OPC UA (GENESIS64 and other
  SCADA servers), Modbus TCP / RTU, MQTT, CSV / Excel files, PDF / Word documents, Google Sheets / Docs, REST / JSON APIs, a plant simulator.
- **Model:** every live item gets an ISA-95 path (site / area / line / machine), unit and limits.
- **Tools:** generic `browse_model`, `list_assets`, `read_tag`, `query_history` over the model;
  curated tools (`get_oee`, `get_downtime_pareto` ...) built from views with typed parameters -
  never free SQL, never protocol writes. Draft -> test call -> publish, with version history.
- **Answers:** one envelope with unit, timestamp, quality, ISA-95 path, source and `truncated`;
  aggregated on the server, plant time zone.
- **Safety:** risk classes R0-R5 as MCP annotations (v1 serves R0-R3, R2 off until enabled),
  allow-list (new objects start hidden), personal-data columns never returned, row / date-range /
  timeout / rate limits, hash-chained audit log (syslog export).
- **Edge:** MCP over Streamable HTTP and stdio, API keys (Bearer), tool profiles (`/mcp/eng`,
  `/mcp/qe` ...), `tools/list_changed` on publish, `/health` and Prometheus `/metrics`.
- **Web console** (`mcx_web`, the main UI): connect -> discover -> expose -> test -> publish, tool
  profiles with "Connect a client" steps per platform, safety, audit, users & roles (local accounts:
  Admin / Engineer / Viewer), Spex theme light / dark, works offline.
- **MCX Studio** (`mcx_studio`, optional): the same setup as a PySide6 desktop app.

Scope and roadmap: *MCX Adapter v1: scope and design*. Formerly this repository held NX Logger
(tag `v6-mqdaq`, branch `nx-connectivity-categories`); MCX Studio keeps its theme and layout.

## Install and run

```bash
pip install -e ".[dev]"
python -m mcx_web --demo                          # adapter + web console http://127.0.0.1:8810 (demo plant)
python -m mcx_web PROJECT.mcx --host 0.0.0.0      # your project, reachable on the plant network
python -m mcx_studio                              # optional desktop app
python -m mcx_core demo                           # or: create the demo project (prints API keys once)
python -m mcx_core serve "~/Documents/MCX Adapter/Demo/Plant adapter.mcx"   # headless service
python -m mcx_core stdio PROJECT.mcx --profile eng                        # Claude Desktop (stdio)
python -m mcx_core.sim.factory_energy             # 11 Modbus power meters on 127.0.0.1:5020 (demo)
python -m pytest -q                               # tests (local Modbus / OPC UA / HTTP servers)
```

Connect a client to `http://127.0.0.1:8811/mcp/<profile>` with `Authorization: Bearer <key>`;
the web console's Tool profiles > Connect a client gives the settings for Copilot Studio, ContextForge,
Docker MCP Gateway and Claude Desktop. See [docs/](docs/).

## Layout

```
src/mcx_core/     no Qt: config (.mcx YAML), adapters/, model (ISA-95), tools, safety, audit,
                  mcp (Streamable HTTP + stdio), runtime, demo, sim/factory_energy
src/mcx_web/      web console: app (FastAPI routes), users (local accounts, roles, sessions),
                  templates/ (Jinja2), static/ (Spex theme CSS, small JS)
src/mcx_studio/   PySide6 (optional): main_window, widgets/ (overview, source_page, profiles_page, pages,
                  property_grid, common), dialogs/, theme/ (Spex ui_kit, app_style), icons
tests/            pytest
Dockerfile        adapter + web console image
```
