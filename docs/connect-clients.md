# Connecting clients

MCX Adapter is a standard MCP server. Each **tool profile** is its own endpoint:

    http(s)://<host>:8811/mcp/<profile>        e.g. /mcp/eng, /mcp/qe, /mcp/mgr

Authentication (v1): API keys sent as `Authorization: Bearer <key>` (or `X-API-Key: <key>`).
A key bound to a profile works only on that profile's endpoint. Create / rotate keys in Studio
(MCP endpoint, or Tool profiles > Connect a client > Rotate key) or with
`python -m mcx_core key PROJECT.mcx NAME --profile eng`. The secret is shown once.
OAuth 2.1 bearer tokens are planned; the server refuses to start in `oauth` mode rather than run open.

| Platform | How |
|---|---|
| Microsoft Copilot Studio | Agent > add tool > Model Context Protocol; server URL; API key auth (header `Authorization`, value `Bearer <key>`) |
| IBM ContextForge | Add MCP server / gateway: transport Streamable HTTP, URL, auth type bearer with the key; group tools into virtual servers |
| Docker MCP Gateway | Catalog entry of type remote (Studio > Copy settings gives the YAML); key as a gateway secret |
| Claude Desktop | Local stdio: `claude_desktop_config.json` -> `{"command": "python", "args": ["-m", "mcx_core", "stdio", "<project.mcx>", "--profile", "eng"]}` |
| claude.ai | Custom connector with the server URL over HTTPS |
| Any MCP client / MCP Inspector | Streamable HTTP URL + Bearer header, or stdio |

Server endpoints: `POST` JSON-RPC, `GET` (Accept: text/event-stream) for notifications
(`notifications/tools/list_changed` after every publish), `DELETE` to end a session,
`GET /health`, `GET /metrics` (Prometheus).

TLS: set `endpoint.tls_cert` / `tls_key`, or put the adapter behind a TLS proxy and set
`endpoint.public_url`. Browsers' `Origin` headers must be loopback or listed in `endpoint.allowed_origins`.
