"""MCX core - the engine of MCX Adapter (and later MCX Gateway). No Qt.

Connects to plant data sources (SQL databases, OPC UA, Modbus, MQTT, CSV / Excel, REST, a
simulator), maps them to an ISA-95 model and serves them as safe, standard MCP tools:

  adapters   one plug-in per source type (config_schema / connect / health / discover / read /
             history / search)
  model      ISA-95 paths, units, limits, friendly names
  tools      generic tools (read_tag, browse_model, query_history, list_assets) and curated tools
             built from SQL views with typed parameters - never free SQL
  safety     risk classes R0-R5, allow-lists, hidden personal-data columns, row / date / timeout /
             rate limits
  audit      hash-chained JSON-lines audit log
  mcp        MCP over Streamable HTTP and stdio, API keys, tool profiles, change notices
  runtime    the adapter service tying it together (used headless and by MCX Studio)
"""
APP_NAME = "MCX Adapter"
ORG_NAME = "SPEX"
__version__ = "0.3.0"
MCP_PROTOCOL = "2025-06-18"
MCP_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
