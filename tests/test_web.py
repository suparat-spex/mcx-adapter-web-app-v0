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
         "/audit", "/audit?kind=admin", "/users", "/account", "/licence", "/sources/new"]


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
