"""Google Sheets / Docs sources against a fake Google (urlopen patched): token, tabs, docs, search, errors."""
import base64
import io
import json
import urllib.error
import urllib.parse

import pytest

from mcx_core.adapters import Query, make_adapter
from mcx_core.adapters.base import AdapterError
from mcx_core.adapters.google import file_id
from mcx_core.config import SourceCfg
from mcx_core.tools import new_curated

SHEET_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
DOC_ID = "1ZyXwVuTsRqPoNmLkJiHgFeDcBa9876543210"
EMAIL = "mcx-reader@plant.iam.gserviceaccount.com"


@pytest.fixture()
def key_file(tmp_path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    pk = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = pk.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                           serialization.NoEncryption()).decode()
    f = tmp_path / "sa.json"
    f.write_text(json.dumps({"type": "service_account", "client_email": EMAIL, "private_key": pem,
                             "private_key_id": "k1", "token_uri": "https://oauth2.googleapis.com/token"}))
    return str(f), pk.public_key()


class FakeGoogle:
    def __init__(self, public_key):
        self.pub, self.calls, self.token_requests = public_key, [], 0

    def __call__(self, req, timeout=None, **kw):
        url = req.full_url
        self.calls.append(url)
        if url.startswith("https://oauth2.googleapis.com/token"):
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.asymmetric import padding
            form = urllib.parse.parse_qs(req.data.decode())
            head, body, sig = form["assertion"][0].split(".")
            pad = lambda s: base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))   # noqa: E731
            self.pub.verify(pad(sig), f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
            claims = json.loads(pad(body))
            assert claims["iss"] == EMAIL and "readonly" in claims["scope"] and "drive" not in claims["scope"]
            self.token_requests += 1
            return self._ok({"access_token": "tok", "expires_in": 3600})
        assert req.get_header("Authorization") == "Bearer tok"
        if SHEET_ID in url and "values:batchGet" in url:
            return self._ok({"valueRanges": [
                {"values": [["date", "line", "output"], ["2026-11-01", "L1", 120], ["2026-11-01", "L2", 95.5],
                            ["2026-11-02", "L1", 130]]},
                {"values": [["Name", "Phone"], ["A", "123"]]}]})
        if SHEET_ID in url:
            return self._ok({"properties": {"title": "Production log"},
                             "sheets": [{"properties": {"title": "Daily"}}, {"properties": {"title": "Contacts"}}]})
        if DOC_ID in url:
            para = lambda t, s="NORMAL_TEXT": {"paragraph": {"paragraphStyle": {"namedStyleType": s},   # noqa: E731
                                                             "elements": [{"textRun": {"content": t + "\n"}}]}}
            cell = lambda t: {"content": [para(t)]}                                                    # noqa: E731
            return self._ok({"title": "SOP Molding", "body": {"content": [
                para("Start-up", "HEADING_1"), para("Preheat the mould to 80 C."),
                para("Changeover", "HEADING_1"), para("Purge the barrel before a colour change."),
                {"table": {"tableRows": [{"tableCells": [cell("Step"), cell("Minutes")]},
                                         {"tableCells": [cell("Purge"), cell("15")]}]}}]}})
        raise urllib.error.HTTPError(url, 403, "Forbidden", {}, io.BytesIO(b'{"error": {"message": "denied"}}'))

    @staticmethod
    def _ok(obj):
        return io.BytesIO(json.dumps(obj).encode())


@pytest.fixture()
def google(key_file, monkeypatch):
    fake = FakeGoogle(key_file[1])
    monkeypatch.setattr("urllib.request.urlopen", fake)
    return key_file[0], fake


def test_file_id():
    assert file_id(f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit#gid=0") == SHEET_ID
    assert file_id(SHEET_ID) == SHEET_ID
    with pytest.raises(AdapterError):
        file_id("SELECT * FROM x")


def test_google_sheets(google):
    key, fake = google
    a = make_adapter(SourceCfg("g", "G", "gsheets", settings={
        "credentials": key, "files": f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit?usp=sharing"}))
    a.ping()
    r = a.discover()
    by = {o.name: o for o in r.objects}
    assert set(by) == {"Production log.Daily", "Production log.Contacts"}
    daily = by["Production log.Daily"]
    assert {c.name: c.type for c in daily.columns} == {"date": "date", "line": "string", "output": "number"}
    assert by["Production log.Contacts"].pii_columns() == {"Phone"}
    cols, rows = a.select(daily, Query([("output", "sum", "total")], [("line", "=", "L1")]))
    assert rows == [[250]]
    assert fake.token_requests == 1                            # token reused
    n = len(fake.calls)
    a.select(daily, Query())
    assert len(fake.calls) == n                                # values cached


def test_google_docs_search_tool(google, runtime):
    key, _ = google
    src = SourceCfg("gd", "SOPs", "gdocs", settings={"credentials": key, "files": f"sop = {DOC_ID}"})
    a = make_adapter(src)
    src.objects = a.discover().objects
    by = {o.name: o for o in src.objects}
    assert set(by) == {"sop", "sop.table1"}
    _, rows = a.select(by["sop"], Query(where=[("text", "contains", "PURGE")]))
    assert rows == [["Changeover", 4, "Purge the barrel before a colour change."]]
    _, rows = a.select(by["sop.table1"], Query())
    assert rows == [["Purge", 15]]
    t = new_curated("search_sop", "gd", by["sop"], ["all"])
    assert [p.name for p in t.params] == ["contains"] and t.columns == ["section", "para", "text"]


def test_google_errors(google, tmp_path):
    key, _ = google
    a = make_adapter(SourceCfg("g", "G", "gdocs", settings={"credentials": key, "files": "x" * 30}))
    with pytest.raises(AdapterError, match=f"share the file with {EMAIL}"):
        a.ping()
    (tmp_path / "bad.json").write_text("{}")
    b = make_adapter(SourceCfg("g", "G", "gsheets", settings={"credentials": str(tmp_path / "bad.json"),
                                                              "files": SHEET_ID}))
    with pytest.raises(AdapterError, match="not a service-account JSON key"):
        b.ping()
    c = make_adapter(SourceCfg("g", "G", "gsheets", settings={"credentials": key, "files": ""}))
    with pytest.raises(AdapterError, match="no spreadsheets listed"):
        c.ping()
