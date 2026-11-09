"""mcx_core: project file, safety rules, curated / generic tools, audit chain. No network, no Qt."""
import datetime as dt
import json

import pytest

from mcx_core.adapters import Query
from mcx_core.adapters.sql import SqlAdapter
from mcx_core.audit import AuditLog
from mcx_core.config import Project, ToolParam
from mcx_core.safety import SafetyError, annotations, check_publishable

D1 = dt.date.today()
D0 = D1 - dt.timedelta(days=6)
RANGE = {"date_from": D0.isoformat(), "date_to": D1.isoformat()}


def test_project_roundtrip(demo, tmp_path):
    p, path, keys = demo
    p.save(tmp_path / "x.mcx")
    q = Project.load(tmp_path / "x.mcx")
    assert [t.name for t in q.tools] == [t.name for t in p.tools]
    assert q.tool("get_oee").published == p.tool("get_oee").published
    assert all(len(k.hash) == 64 for k in q.auth.api_keys)          # only hashes on disk
    text = (tmp_path / "x.mcx").read_text()
    assert not any(secret in text for secret in keys.values())


def test_generic_tools_always_exist():
    p = Project.from_dict({"name": "x"})
    assert {"browse_model", "list_assets", "read_tag", "query_history"} <= {t.name for t in p.tools}


def test_draft_vs_published(demo):
    p, _, _ = demo
    t = p.tool("get_oee")
    assert t.state == "published"
    t.description = "changed"
    assert t.state == "changed" and t.live().description != "changed"   # clients still see the old text
    assert p.publish(["get_oee"]) == ["get_oee"] and t.version == 2 and t.live().description == "changed"


def test_curated_call_and_envelope(runtime):
    env, err = runtime.call_tool("get_oee", {"line": "L2", **RANGE}, client="t", profile="qe")
    assert not err
    assert {r["shift"] for r in env["rows"]} == {"A", "B", "C"}
    assert env["units"]["oee"] == "ratio" and env["context"] == {"isa95": "Bangkok/Assembly/L2", "tz": "Asia/Bangkok"}
    assert env["source"]["object"] == "oee_shift_summary" and env["truncated"] is False
    assert env["as_of"].endswith("+07:00")


def test_limits(runtime):
    _, err = runtime.call_tool("get_oee", {"date_from": "2026-01-01", "date_to": "2026-06-01"}, client="t", profile="qe")
    assert "31 days" in err
    _, err = runtime.call_tool("get_oee", {**RANGE, "sql": "1; DROP TABLE x"}, client="t", profile="qe")
    assert "unknown argument" in err
    _, err = runtime.call_tool("get_oee", {**RANGE, "group_by": "operator_name"}, client="t", profile="qe")
    assert "must be one of" in err
    runtime.project.limits.max_rows = 5
    env, err = runtime.call_tool("get_oee", {"date_from": D0.isoformat(), "date_to": D1.isoformat(), "group_by": "machine"},
                                 client="t", profile="eng")
    assert not err and env["truncated"] and len(env["rows"]) == 5 and "narrow" in env["note"].lower()


def test_rate_limit(runtime):
    runtime.project.limits.rate_per_min = 3
    errs = [runtime.call_tool("list_assets", {}, client="burst", profile="eng")[1] for _ in range(4)]
    assert errs[:3] == ["", "", ""] and "rate limit" in errs[3]


def test_profiles_filter_tools(runtime):
    qe = {t["name"] for t in runtime.mcp_tools("qe")}
    mgr = {t["name"] for t in runtime.mcp_tools("mgr")}
    assert "get_reject_pareto" in qe and "get_reject_pareto" not in mgr
    assert "read_tag" not in mgr                                 # managers: KPIs, not raw tags
    _, err = runtime.call_tool("get_carbon_summary", RANGE, client="t", profile="qe")
    assert "not published for this client" in err


def test_personal_data_never_published(demo):
    p, _, _ = demo
    t = p.tool("get_operator_comments")
    assert t.published is None
    p.source("smc_demo").object("oee_operator_comments").exposed = True
    with pytest.raises(SafetyError, match="personal-data"):
        check_publishable(t, p)


def test_v1_refuses_writes(demo):
    p, _, _ = demo
    t = p.tool("get_oee")
    t.risk = "R4"
    with pytest.raises(SafetyError, match="not available in v1"):
        check_publishable(t, p)
    assert annotations(t)["readOnlyHint"] is False


def test_sql_builder_quotes_and_binds(demo):
    p, _, _ = demo
    src = p.source("smc_demo")
    a = SqlAdapter(src)
    obj = src.object("oee_shift_summary")
    sql, params = a.build(obj, Query([("oee", "avg", "oee")], [("line", "=", "L2'; DROP TABLE x;--")], ["line"],
                                     [("line", False)], 10))
    assert "DROP" not in sql and params == ["L2'; DROP TABLE x;--"] and sql.endswith("LIMIT 11")
    with pytest.raises(Exception, match="unknown column"):
        a.build(obj, Query([("password", None, "p")]))
    for d in ("sqlserver", "mysql"):
        a.settings["dialect"] = d
        sql, _ = a.build(obj, Query([("oee", "avg", "oee")], limit=5))
        assert ("TOP 6" in sql) if d == "sqlserver" else ("`oee`" in sql)


def test_pii_columns_never_returned(demo):
    p, _, _ = demo
    src = p.source("smc_demo")
    obj = src.object("oee_operator_comments")
    assert {"operator_name", "comment"} <= obj.pii_columns()
    cols, _ = SqlAdapter(src).select(obj, Query(limit=3))
    assert "operator_name" not in cols and "comment" not in cols


def test_generic_tools(runtime):
    env, err = runtime.call_tool("browse_model", {}, client="t", profile="eng")
    assert not err and env["rows"][0]["path"] == "Bangkok"
    env, err = runtime.call_tool("read_tag", {"path": "Bangkok/Assembly/L2/Press1"}, client="t", profile="eng")
    assert not err and len(env["rows"]) == 8 and env["rows"][0]["unit"] == "°C" and env["quality"] == "good"
    t1 = dt.datetime.now().replace(microsecond=0)
    env, err = runtime.call_tool("query_history", {"path": "Bangkok/Assembly/L2/Press1/temperature",
                                                   "from": (t1 - dt.timedelta(hours=1)).isoformat(),
                                                   "to": t1.isoformat(), "interval": "15m"}, client="t", profile="eng")
    assert not err and 4 <= len(env["rows"]) <= 5 and env["units"] == {"value": "°C"}
    runtime.project.profile("qe").scope = "Bangkok/Molding"
    _, err = runtime.call_tool("read_tag", {"path": "Bangkok/Assembly/L2/Press1"}, client="t", profile="qe")
    assert "outside" in err


def test_files_adapter(runtime):
    from mcx_core.adapters import make_adapter
    src = runtime.project.source("shift_reports")
    a = make_adapter(src)
    obj = src.object("shift_reports.Shift log")
    assert "supervisor" in obj.pii_columns()
    cols, rows = a.select(obj, Query([("output", "sum", "output")], [("line", "=", "L1")], limit=10))
    assert cols == ["output"] and rows[0][0] > 0


def test_audit_chain_detects_tampering(tmp_path):
    log = AuditLog(tmp_path)
    for i in range(5):
        log.write(client="c", tool="t", args={"i": i}, rows=i, ms=1, result="ok")
    assert log.verify() == (True, 5, "5 records, chain verified")
    assert AuditLog(tmp_path).seq == 5                         # chain continues after a restart
    f = next(tmp_path.glob("*.jsonl"))
    lines = f.read_text().splitlines()
    rec = json.loads(lines[2]); rec["rows"] = 999
    lines[2] = json.dumps(rec)
    f.write_text("\n".join(lines) + "\n")
    ok, n, msg = AuditLog(tmp_path).verify()
    assert not ok and n == 2 and "seq 3" in msg


def test_every_call_audited(runtime):
    before = runtime.audit.seq
    runtime.call_tool("get_oee", RANGE, client="a", profile="qe")
    runtime.call_tool("get_oee", {"date_from": "x"}, client="a", profile="qe")
    recs = list(runtime.audit.recent)[-2:]
    assert runtime.audit.seq == before + 2
    assert [r["result"] for r in recs] == ["ok", "blocked"] and recs[0]["profile"] == "qe"
    assert runtime.audit.verify()[0]


def test_coerce_types():
    from mcx_core.safety import coerce
    assert coerce(ToolParam("n", "integer"), "5") == 5
    with pytest.raises(SafetyError):
        coerce(ToolParam("b", "boolean"), "yes")
    with pytest.raises(SafetyError):
        coerce(ToolParam("s", "string"), "x" * 500)
