---
name: mcx-adapter-dev
description: Use when developing MCX Adapter in this repo - mcx_core (sources / adapters, ISA-95 model, tools, safety, audit, MCP endpoint, runtime, demo) or MCX Studio (PySide6 setup UI: documents, Properties, theme) - adding a source type, a tool feature, a safety rule, a Studio page, or running / testing / screenshotting the app.
---

# MCX Adapter development

`CLAUDE.md` has the layout and the rules; this is the how-to.

| Task | Where |
|---|---|
| New source type | `mcx_core/adapters/<x>.py` (subclass `Adapter`), `ADAPTERS` in `adapters/__init__.py`, test against a local server / file |
| New tool behaviour | `mcx_core/tools.py` (schema, args, query) + `runtime._curated` / `_generic`; keep the envelope |
| New limit / rule | `mcx_core/safety.py` + `config.Limits` (default!) + Studio Safety page + a test in `tests/test_core.py` |
| Studio document | subclass `widgets.common.Page` (`refresh` full, `tick` live), register in `main_window._page` / `DOCS` |
| Properties for a new object | `AdapterWindow._prop_<kind>` returning a `PropTarget` |

Checks before done:
1. `python -m pytest -q` and `python -m pyflakes src tests`.
2. Screenshots in light, dark and 1280 x 800:
   `PYTHONPATH=src python3 .claude/skills/mcx-adapter-dev/scripts/screenshots.py <scratch dir>`
   (offscreen; makes a demo in `<scratch>/data`, drives real HTTP MCP calls, writes `<scratch>/shots/*.png`). Look at the PNGs.
3. MCP compliance: connect with the official SDK client (`mcp.client.streamable_http`) or MCP Inspector.
