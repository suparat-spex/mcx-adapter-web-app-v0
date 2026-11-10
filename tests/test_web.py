"""Web console: first admin, sign-in, roles, CSRF, every page renders, main actions change the adapter."""
import re

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def web(demo, monkeypatch):
    from mcx_web.app import Console, create_app
    p, path, keys = demo
    con = Console(path, start_mcp=False)
    client = TestClient(create_app(con), follow_redirects=False)
    yield con, client
    con.stop()


def csrf_of(client, url="/"):
    html = client.get(url).text
    return re.search(r'name="csrf" content="([^"]+)"', html).group(1)


def sign_in(client, user, pw):
    r = client.post("/login", data={"username": user, "password": pw, "next": "/"})
    assert r.status_code == 303, r.text
    return csrf_of(client)


def setup_admin(con, client):
    assert client.get("/").headers["location"] == "/setup"
    r = client.post("/setup", data={"username": "admin", "name": "Admin", "password": "plant-admin-1",
                                    "password2": "plant-admin-1"})
    assert r.status_code == 303
    return csrf_of(client)


PAGES = ["/", "/sources/smc_demo?step=0", "/sources/smc_demo?step=1", "/sources/smc_demo?step=2",
         "/sources/smc_demo?step=3", "/sources/plant_sim?step=2", "/tools", "/tools/get_oee", "/tools/read_tag",
         "/model", "/profiles?p=qe", "/profiles?p=eng&platform=Claude%20Desktop", "/endpoint", "/safety",
         "/audit", "/audit?kind=admin", "/users", "/account", "/licence", "/sources/new", "/project",
         "/tools?sel=get_oee", "/sources/smc_demo?step=1&sel=oee_daily", "/safety?size=50&page=2"]


def test_every_page_renders_for_admin(web):
    con, client = web
    setup_admin(con, client)
    for url in PAGES:
        r = client.get(url)
        assert r.status_code == 200, (url, r.status_code, r.text[:500])
        assert "MCX" in r.text and "Traceback" not in r.text
    assert "X-Frame-Options" in client.get("/").headers


def test_sign_in_and_lockout(web):
    con, client = web
    setup_admin(con, client)
    client.cookies.clear()
    assert client.get("/tools").headers["location"].startswith("/login")
    for _ in range(5):
        assert client.post("/login", data={"username": "admin", "password": "wrong"}).status_code == 401
    r = client.post("/login", data={"username": "admin", "password": "plant-admin-1"})
    assert r.status_code == 401 and "Too many attempts" in r.text            # locked even with the right one
    assert [x["result"] for x in con.rt.audit.recent if x.get("tool") == "login"][-1] == "blocked"


def test_roles(web):
    con, client = web
    tok = setup_admin(con, client)
    for name, role in (("eng1", "engineer"), ("view1", "viewer")):
        assert client.post("/users", data={"csrf": tok, "username": name, "password": "longpass-123",
                                           "role": role}).status_code == 303
    client.cookies.clear()
    tok = sign_in(client, "view1", "longpass-123")
    assert client.get("/tools").status_code == 200
    assert client.get("/users").status_code == 403
    assert client.post("/safety/limits", data={"csrf": tok, "max_rows": 1, "max_days": 1, "timeout_s": 1,
                                               "rate_per_min": 1, "cache_s": 1}).status_code == 403
    client.cookies.clear()
    tok = sign_in(client, "eng1", "longpass-123")
    assert client.post("/safety/limits", data={"csrf": tok, "max_rows": 200, "max_days": 14, "timeout_s": 5,
                                               "rate_per_min": 30, "cache_s": 5}).status_code == 303
    assert con.project.limits.max_rows == 200
    assert client.post("/keys", data={"csrf": tok, "profile": "qe"}).status_code == 403   # keys: admin only
    assert client.get("/users").status_code == 403


def test_csrf_required(web):
    con, client = web
    setup_admin(con, client)
    assert client.post("/safety/limits", data={"max_rows": 1}).status_code == 403
    assert client.post("/safety/limits", data={"csrf": "forged", "max_rows": 1}).status_code == 403


def test_expose_test_publish(web):
    con, client = web
    tok = setup_admin(con, client)
    r = client.post("/sources/smc_demo/expose", data={"csrf": tok, "key": "ecm_production", "on": "1"})
    assert r.status_code == 303
    t = con.project.tool("get_ecm_production")
    assert t is not None and t.state == "draft"
    r = client.post("/tools/get_oee/test", data={"csrf": tok, "args": 'line="L2", date_from="2026-11-01", '
                                                                      'date_to="2026-11-07"'})
    assert r.json()["env"]["tool"] == "get_oee"
    r = client.post("/publish", data={"csrf": tok, "tool": "get_ecm_production", "back": "/tools"})
    assert r.status_code == 303 and "Published" in r.headers["location"].replace("%20", " ")
    assert "get_ecm_production" in {x["name"] for x in con.rt.mcp_tools("eng")}
    saved = open(con.path, encoding="utf-8").read()
    assert "get_ecm_production" in saved                                       # saved to the project file
    assert any(x.get("tool") == "tools.publish" and x.get("client") == "admin" for x in con.rt.audit.recent)


def test_profile_toggle_and_keys(web):
    con, client = web
    tok = setup_admin(con, client)
    assert "qe" not in con.project.tool("get_carbon_summary").profiles
    client.post("/profiles/qe/toggle", data={"csrf": tok, "cell": "get_carbon_summary|qe"})
    assert "qe" in con.project.tool("get_carbon_summary").profiles
    r = client.post("/keys", data={"csrf": tok, "profile": "qe", "client": "Copilot Studio"})
    page = client.get(r.headers["location"]).text
    secret = re.search(r'id="flash-secret">(mcx_[^<]+)<', page).group(1)
    assert "mcx_" not in client.get("/endpoint").text                         # shown once only
    assert any(k.hash for k in con.project.auth.api_keys) and secret not in open(con.path).read()


def test_users_keep_one_admin(web):
    con, client = web
    tok = setup_admin(con, client)
    admin = con.users.list()[0]
    r = client.post(f"/users/{admin.id}", data={"csrf": tok, "action": "role", "role": "viewer"})
    assert "keep" in r.headers["location"]
    assert con.users.get(admin.id).role == "admin"


def test_add_document_source(web):
    con, client = web
    csrf = setup_admin(con, client)
    html = client.get("/sources/new").text
    assert 'value="docs"' in html and "PDF / Word documents" in html
    r = client.post("/sources/new", data={"csrf": csrf, "type": "docs", "name": "Manuals"})
    assert r.status_code == 303 and r.headers["location"].startswith("/sources/manuals")
    assert con.project.source("manuals").type == "docs"
    page = client.get("/sources/manuals?step=0")
    assert page.status_code == 200 and "Max characters per passage" in page.text


def test_test_call_hint_is_escaped(web):
    con, client = web
    setup_admin(con, client)
    html = client.get("/tools/get_oee").text
    hint = re.search(r'<input id="args"[^>]*placeholder="([^"]*)"', html).group(1)
    assert hint.startswith("e.g. ") and "&#34;" in hint and "date_from=" in hint and "required:" in hint


def test_project_settings(web):
    con, client = web
    csrf = setup_admin(con, client)
    html = client.get("/").text
    assert 'class="crumbs"' in html and con.project.name in html and 'class="rail"' in html and 'class="smenu"' in html
    r = client.post("/project", data={"csrf": csrf, "name": "Plant adapter", "site": "Bangkok/",
                                      "timezone": "Asia/Bangkok", "description": "x"})
    assert r.status_code == 303 and "ok=" in r.headers["location"]
    p = con.project
    assert (p.name, p.site, p.timezone) == ("Plant adapter", "Bangkok", "Asia/Bangkok")
    assert "Bangkok · Asia/Bangkok" in client.get("/").text
    r = client.post("/project", data={"csrf": csrf, "name": "x", "timezone": "Mars/Base"})
    assert "err=" in r.headers["location"] and con.project.timezone == "Asia/Bangkok"
    assert any(x.get("tool") == "project.edit" for x in con.rt.audit.recent)


def test_search_and_details(web):
    con, client = web
    setup_admin(con, client)
    g = client.get("/api/search?q=oee").json()["groups"]
    titles = [x["title"] for x in g]
    assert "Tools" in titles and all(it["href"].startswith("/") for x in g for it in x["items"])
    assert client.get("/api/search?q=").json() == {"groups": []}
    r = client.get("/detail/tool/get_oee")
    assert r.status_code == 200 and "get_oee" in r.text and "<html" not in r.text
    assert client.get("/detail/tool/nope").status_code == 404
    seq = con.rt.audit.recent[-1]["seq"]
    assert "Record #" in client.get(f"/detail/audit/{seq}").text
    src = next(s for s in con.project.sources if s.objects)
    o = src.objects[0]
    assert o.name in client.get(f"/detail/object/{src.id}?name={o.name}").text
    client.cookies.clear()
    assert client.get("/detail/tool/get_oee").status_code in (302, 303, 307)   # sign-in first


def test_pager_and_pii_save_keeps_other_pages(web):
    con, client = web
    csrf = setup_admin(con, client)
    src = next(s for s in con.project.sources if s.objects)
    a, b = src.objects[0], src.objects[1]
    b.columns[0].pii = True                                   # on a page that is not submitted
    seen = [f"{src.id}|{a.name}|{c.name}" for c in a.columns]
    r = client.post("/safety/pii", data={"csrf": csrf, "seen": seen, "pii": seen[:1]})
    assert r.status_code == 303
    assert a.columns[0].pii and not any(c.pii for c in a.columns[1:]) and b.columns[0].pii
    html = client.get("/tools?size=25").text
    assert "/ page" not in html or 'data-psize' in html


def test_expose_step_shows_object_on_the_right(web):
    con, client = web
    csrf = setup_admin(con, client)
    src = next(s for s in con.project.sources if s.objects and s.type == "sql")
    html = client.get(f"/sources/{src.id}?step=2").text
    assert 'aria-label="Tool preview"' not in html and "data-detail=" in html          # no preview card any more
    hidden = next(o for o in src.objects if not o.exposed and not o.blocked)
    r = client.post(f"/sources/{src.id}/expose", data={"csrf": csrf, "key": hidden.name, "on": "1"})
    page = client.get(r.headers["location"]).text                                      # back on step 3, object picked
    side = page.split('<aside class="side"', 1)[1]
    assert hidden.name in side and "MCP tool" in side and "Edit tool" in side
    frag = client.get(f"/detail/object/{src.id}?step=2&name={hidden.name}").text
    assert "Hide" in frag and "Columns" in frag


def test_drawers_render_for_every_kind(web):
    con, client = web
    setup_admin(con, client)
    src = next(s for s in con.project.sources if s.objects and s.type == "sql")
    obj = src.objects[0]
    uid = con.users.list()[0].id
    for url in ("/drawer/tool/get_oee", "/drawer/tool/read_tag", "/drawer/source/new", f"/drawer/source/{src.id}",
                "/drawer/profile/new", "/drawer/profile/qe", "/drawer/user/new", f"/drawer/user/{uid}", "/drawer/key/new",
                f"/drawer/template/{src.id}?obj={obj.name}"):
        r = client.get(url + ("&" if "?" in url else "?") + "back=/tools")
        assert r.status_code == 200 and "<html" not in r.text and "Traceback" not in r.text, url
    assert 'name="back" value="/tools"' in client.get("/drawer/tool/get_oee?back=/tools").text
    assert client.get("/drawer/tool/get_oee?back=//evil.example").text.count('value="//evil') == 0
    assert client.get("/drawer/nope/x").status_code == 404


def test_tool_structured_form_check_and_save(web):
    con, client = web
    csrf = setup_admin(con, client)
    t = con.project.tool("get_oee")
    src = con.project.source(t.source)
    cols = [c.name for c in src.object(t.object).columns if not c.pii]
    text = next(c.name for c in src.object(t.object).columns if c.type == "string" and not c.pii)
    num = next(c.name for c in src.object(t.object).columns if c.type in ("number", "integer") and not c.pii)
    form = {"csrf": csrf, "title": "OEE", "description": "d", "risk": "R1", "profiles": ["qe"], "enabled": "on",
            "mode": "fields", "p_n": "1", "p0_name": "grp", "p0_kind": "group", "p0_choices": [text],
            "m_n": "1", "m0_column": num, "m0_agg": "avg", "order_by": "", "date_column": "", "back": "/tools?state=draft"}
    r = client.post("/tools/get_oee/check", data=form).json()
    assert r["ok"] and "grp" in r["sig"]
    bad = dict(form, m0_column="no_such_col")
    assert not client.post("/tools/get_oee/check", data=bad).json()["ok"]
    assert t.title != "OEE"                                     # check never saves
    r = client.post("/tools/get_oee", data=form)
    assert r.status_code == 303 and r.headers["location"].startswith("/tools?state=draft")
    assert t.title == "OEE" and [p.name for p in t.params] == ["grp"] and t.measures[0].column == num
    assert cols


def test_bulk_export_templates_flow(web):
    con, client = web
    csrf = setup_admin(con, client)
    curated = [t.name for t in con.project.tools if t.kind == "curated"][:2]
    r = client.post("/bulk/tools", data={"csrf": csrf, "tool": curated, "action": "add_profile", "profile": "mgr"})
    assert r.status_code == 303 and all("mgr" in con.project.tool(n).profiles for n in curated)
    client.post("/bulk/tools", data={"csrf": csrf, "tool": curated, "action": "disable"})
    assert not any(con.project.tool(n).enabled for n in curated)
    csv_text = client.get("/export/tools.csv").text
    assert csv_text.startswith("name,kind") and curated[0] in csv_text
    assert "held back" in client.get("/tools?state=held").text or "No tools match" in client.get("/tools?state=held").text
    src = next(s for s in con.project.sources if s.objects and s.type == "sql")
    o = next(x for x in src.objects if not x.exposed and not x.blocked)
    r = client.post(f"/sources/{src.id}/expose", data={"csrf": csrf, "key": o.name, "on": "1", "template": "lookup"})
    assert r.status_code == 303
    t = next(t for t in con.project.tools if t.source == src.id and t.object == o.name)
    assert t.columns and not t.measures
    html = client.get(f"/tools/{t.name}?view=flow").text
    assert html.count('class="fnode') == 5 and "Who can call" in html
    uid = con.users.create("ann", "plant-user-123", "viewer").id
    r = client.post(f"/users/{uid}", data={"csrf": csrf, "action": "edit", "role": "engineer", "name": "Ann B",
                                            "active": "on", "password": ""})
    assert r.status_code == 303 and con.users.get(uid).role == "engineer" and con.users.get(uid).name == "Ann B"
    page = client.get("/").text
    assert 'class="avatar"' in page and 'data-theme-set="system"' in page and 'id="nav-btn"' in page


def test_drawer_keeps_the_date_range_column(web):
    """Regression: a text date column must still be offered (and selected), or Save would drop the range limit."""
    con, client = web
    setup_admin(con, client)
    t = con.project.tool("get_oee")
    assert t.date_column
    html = client.get("/drawer/tool/get_oee").text
    sel = html.split('name="date_column"', 1)[1].split("</select>", 1)[0]
    assert f'value="{t.date_column}" selected' in sel
