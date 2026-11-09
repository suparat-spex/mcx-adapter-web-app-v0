"""MCP endpoint: Streamable HTTP (auth, profiles, sessions, change notices), stdio, health / metrics."""
import io
import json
import socket
import urllib.error
import urllib.request

import pytest

from mcx_core.mcp import McpServer, run_stdio


def _port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
    return p


@pytest.fixture()
def server(runtime):
    runtime.project.endpoint.port = _port()
    srv = McpServer(runtime)
    srv.start()
    yield srv, f"http://127.0.0.1:{runtime.project.endpoint.port}"
    srv.stop()


def rpc(url, method, params=None, key=None, sid=None, mid=1):
    msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if mid is not None:
        msg["id"] = mid
    h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if key:
        h["Authorization"] = f"Bearer {key}"
    if sid:
        h["Mcp-Session-Id"] = sid
    r = urllib.request.urlopen(urllib.request.Request(url, json.dumps(msg).encode(), h), timeout=10)
    body = r.read()
    return r.status, r.headers.get("Mcp-Session-Id"), (json.loads(body) if body else None)


def test_http_session(server, demo):
    srv, base = server
    key = demo[2]["qe"]
    st, sid, res = rpc(base + "/mcp/qe", "initialize", {"protocolVersion": "2025-06-18", "clientInfo": {"name": "pytest"}}, key)
    assert st == 200 and sid and res["result"]["capabilities"]["tools"]["listChanged"]
    st, _, res = rpc(base + "/mcp/qe", "notifications/initialized", key=key, sid=sid, mid=None)
    assert st == 202 and res is None
    _, _, res = rpc(base + "/mcp/qe", "tools/list", key=key, sid=sid)
    tools = {t["name"]: t for t in res["result"]["tools"]}
    assert "get_reject_pareto" in tools and "get_energy_kwh" not in tools
    assert tools["get_oee"]["annotations"]["readOnlyHint"] is True
    assert tools["get_oee"]["inputSchema"]["additionalProperties"] is False
    _, _, res = rpc(base + "/mcp/qe", "tools/call", {"name": "list_plant_assets", "arguments": {"line": "L1"}}, key, sid)
    out = res["result"]
    assert not out["isError"] and len(out["structuredContent"]["rows"]) == 2
    assert json.loads(out["content"][0]["text"])["tool"] == "list_plant_assets"


def test_http_auth(server, demo):
    srv, base = server
    with pytest.raises(urllib.error.HTTPError) as e:
        rpc(base + "/mcp/qe", "tools/list")
    assert e.value.code == 401
    with pytest.raises(urllib.error.HTTPError) as e:
        rpc(base + "/mcp/qe", "tools/list", key="mcx_wrong")
    assert e.value.code == 401
    with pytest.raises(urllib.error.HTTPError) as e:          # a qe key on the eng endpoint
        rpc(base + "/mcp/eng", "tools/list", key=demo[2]["qe"])
    assert e.value.code == 401
    with pytest.raises(urllib.error.HTTPError) as e:
        rpc(base + "/mcp/nope", "tools/list", key=demo[2]["qe"])
    assert e.value.code == 404


def test_health_and_metrics(server):
    _, base = server
    h = json.loads(urllib.request.urlopen(base + "/health").read())
    assert h["status"] == "ok" and h["tools"] == 11
    m = urllib.request.urlopen(base + "/metrics").read().decode()
    assert "mcx_tools_published 11" in m


def test_list_changed_notice(server, demo):
    srv, base = server
    key = demo[2]["eng"]
    _, sid, _ = rpc(base + "/mcp/eng", "initialize", {"protocolVersion": "2025-06-18"}, key)
    sess = srv.sessions[sid]
    import queue
    q = queue.Queue(); sess.streams.append(q)
    rt = srv.rt
    rt.project.tool("get_oee").description = "new"
    rt.publish(["get_oee"])
    assert q.get(timeout=2)["method"] == "notifications/tools/list_changed"


def test_stdio(runtime):
    lines = [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}}),
             json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
             json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
             json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "browse_model", "arguments": {}}}),
             json.dumps({"jsonrpc": "2.0", "id": 4, "method": "nope"}), "not json"]
    out = io.StringIO()
    run_stdio(runtime, "eng", io.StringIO("\n".join(lines) + "\n"), out)
    res = [json.loads(x) for x in out.getvalue().splitlines()]
    assert res[0]["result"]["protocolVersion"] == "2024-11-05"
    assert len(res[1]["result"]["tools"]) == 9
    assert res[2]["result"]["structuredContent"]["rows"][0]["name"] == "Bangkok"
    assert res[3]["error"]["code"] == -32601 and res[4]["error"]["code"] == -32700
