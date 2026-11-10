"""
google.py - Google Sheets and Google Docs, read-only, through a Google service account. No Qt.

Auth: a service-account JSON key file (Google Cloud console > IAM > Service accounts > Keys). Share
each Sheet / Doc with the service account's e-mail as Viewer - it sees nothing else. Tokens use the
read-only scopes only; the JWT is signed locally with `cryptography` (already needed by OPC UA).

  Google Sheets   each tab is one object (<spreadsheet>.<tab>), first row = header - like Excel
  Google Docs     each document is one object of passages: section | para | text (no pages in
                  Google Docs), each table one more object (<document>.table<n>)

Files are listed one per line, as a link or an ID, optionally named:  production = https://docs...
Like the file adapters, a query loads the object into an in-memory SQLite table and runs the same
structured Query. Values are cached for the project's cache_s (min 30 s) to stay inside API quotas.
"""
from __future__ import annotations

import base64
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from mcx_core.adapters.base import Adapter, AdapterError, DiscoverResult, F, Query
from mcx_core.adapters.docs import _split
from mcx_core.adapters.files import _guess, _Mem, _num
from mcx_core.adapters.sql import classify
from mcx_core.config import Column, SourceObject

TOKEN_URI = "https://oauth2.googleapis.com/token"
SHEETS = "https://sheets.googleapis.com/v4/spreadsheets/"
DOCS = "https://docs.googleapis.com/v1/documents/"
SCOPES = "https://www.googleapis.com/auth/spreadsheets.readonly https://www.googleapis.com/auth/documents.readonly"
DOC_HEADER = ["section", "para", "text"]
MIN_CACHE_S = 30.0


def file_id(ref: str) -> str:
    """A Google link (…/d/<id>/edit…) or a bare ID -> the ID."""
    m = re.search(r"/d/([A-Za-z0-9_-]{20,})", ref)
    if m:
        return m.group(1)
    ref = ref.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,}", ref):
        raise AdapterError(f"not a Google file link or ID: {ref[:80]!r}")
    return ref


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _safe_name(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()[:100] or "untitled"


class _Token:
    """Service-account access token (JWT bearer grant), refreshed 5 min before it expires."""

    def __init__(self, key_file: str):
        self.key_file = key_file
        self._tok, self._exp = "", 0.0
        self._lock = threading.Lock()
        self.email = ""

    def _key(self) -> dict:
        p = Path(self.key_file or "")
        if not str(p) or not p.is_file():
            raise AdapterError(f"service-account key file not found: {p}")
        try:
            k = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise AdapterError(f"{p.name}: not a service-account JSON key") from None
        if k.get("type") != "service_account" or not k.get("private_key") or not k.get("client_email"):
            raise AdapterError(f"{p.name}: not a service-account JSON key")
        self.email = k["client_email"]
        return k

    def get(self, timeout_s: float = 10.0) -> str:
        with self._lock:
            if self._tok and time.time() < self._exp - 300:
                return self._tok
            k = self._key()
            try:
                from cryptography.hazmat.primitives import hashes, serialization
                from cryptography.hazmat.primitives.asymmetric import padding
            except ImportError:
                raise AdapterError("Google sources need the cryptography package (pip install cryptography)") \
                    from None
            now = int(time.time())
            head = _b64(json.dumps({"alg": "RS256", "typ": "JWT", "kid": k.get("private_key_id", "")}).encode())
            body = _b64(json.dumps({"iss": k["client_email"], "scope": SCOPES, "aud": k.get("token_uri", TOKEN_URI),
                                    "iat": now, "exp": now + 3600}).encode())
            try:
                pk = serialization.load_pem_private_key(k["private_key"].encode(), password=None)
                sig = pk.sign(f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
            except (ValueError, TypeError):
                raise AdapterError("service-account key: private key not readable") from None
            data = urllib.parse.urlencode({"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                           "assertion": f"{head}.{body}.{_b64(sig)}"}).encode()
            r = _http(urllib.request.Request(k.get("token_uri", TOKEN_URI), data=data), timeout_s, "")
            self._tok, self._exp = r["access_token"], time.time() + int(r.get("expires_in", 3600))
            return self._tok


def _http(req: urllib.request.Request, timeout_s: float, email: str) -> dict:
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8"))
            msg = (msg.get("error") or {}).get("message") if isinstance(msg.get("error"), dict) \
                else msg.get("error_description") or msg.get("error")
        except Exception:
            msg = ""
        if e.code in (403, 404) and email:
            raise AdapterError(f"Google answered {e.code}: share the file with {email} (Viewer) and check "
                               f"the link; enable the Sheets / Docs API in the Google Cloud project") from None
        raise AdapterError(f"Google answered {e.code}: {msg or e.reason}") from None
    except urllib.error.URLError as e:
        raise AdapterError(f"cannot reach Google: {e.reason}") from None


class _GoogleAdapter(Adapter):
    FAMILY = "table"
    CATEGORY = "Files and APIs"
    LIST_LABEL = "Files"
    API = ""

    def __init__(self, cfg, limits=None):
        super().__init__(cfg, limits)
        self._token = _Token(self.setting("credentials"))
        self._cache: dict[str, tuple[float, dict]] = {}

    def _refs(self) -> list[tuple[str, str]]:
        """[(name or '', file id)] from the files setting."""
        out = []
        for line in str(self.setting("files") or "").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            name, ref = ("", line)
            if "=" in line and not line.lower().startswith("http"):
                name, ref = (x.strip() for x in line.split("=", 1))
            out.append((name, file_id(ref)))
        if not out:
            raise AdapterError(f"no {self.LIST_LABEL.lower()} listed")
        return out

    def _get(self, url: str, timeout_s: float = 10.0) -> dict:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self._token.get(timeout_s)}",
                                                   "Accept": "application/json"})
        return _http(req, timeout_s, self._token.email)

    def _fetch(self, name: str, fid: str, timeout_s: float) -> dict[str, tuple[list, list]]:
        raise NotImplementedError

    def _all(self, timeout_s: float = 10.0) -> dict[str, tuple[list, list]]:
        ttl = max(MIN_CACHE_S, float(getattr(self.limits, "cache_s", 0) or 0))
        out = {}
        for name, fid in self._refs():
            hit = self._cache.get(fid)
            if hit is None or time.time() - hit[0] > ttl:
                hit = (time.time(), self._fetch(name, fid, timeout_s))
                self._cache[fid] = hit
            out.update(hit[1])
        return out

    def ping(self):
        name, fid = self._refs()[0]
        self._get(f"{self.API}{fid}?fields={'properties.title' if self.API == SHEETS else 'title'}", 5)

    def select(self, obj, q: Query, timeout_s: float = 10.0):
        data = self._all(timeout_s).get(obj.name)
        if data is None:
            raise AdapterError(f"{obj.name} not found (file, tab or table renamed or removed?)")
        return _Mem().run(obj, *data, q, timeout_s)


def _header(row: list) -> list[str]:
    out, seen = [], set()
    for j, h in enumerate(row):
        h = str(h).strip() if h not in (None, "") else f"col{j + 1}"
        while h.lower() in seen:
            h += "_"
        seen.add(h.lower())
        out.append(h)
    return out


def _table(rows: list) -> tuple[list, list]:
    width = max(len(r) for r in rows)
    header = _header(list(rows[0]) + [None] * (width - len(rows[0])))
    data = [[_num(v) if v != "" else None for v in r] + [None] * (width - len(r)) for r in rows[1:]]
    return header, data


class GoogleSheetsAdapter(_GoogleAdapter):
    TYPE = "gsheets"
    LABEL = "Google Sheets"
    ICON = "sheet"
    LIST_LABEL = "Spreadsheets"
    API = SHEETS
    SCHEMA = [
        F("credentials", "Service-account key file", "str", "",
          tip="JSON key of a Google service account; share each sheet with its e-mail as Viewer"),
        F("files", "Spreadsheets", "text", "",
          tip="One per line: a link or ID, optionally named - e.g. production = https://docs.google.com/spreadsheets/d/…"),
    ]

    def _fetch(self, name, fid, timeout_s):
        meta = self._get(f"{SHEETS}{fid}?fields=properties.title,sheets.properties.title", timeout_s)
        base = _safe_name(name or meta.get("properties", {}).get("title", fid))
        tabs = [s["properties"]["title"] for s in meta.get("sheets", [])]
        if not tabs:
            return {}
        q = "&".join("ranges=" + urllib.parse.quote(f"'{t.replace(chr(39), chr(39) * 2)}'") for t in tabs)
        vals = self._get(f"{SHEETS}{fid}/values:batchGet?{q}&valueRenderOption=UNFORMATTED_VALUE"
                         f"&dateTimeRenderOption=FORMATTED_STRING&majorDimension=ROWS", timeout_s)
        out = {}
        for t, vr in zip(tabs, vals.get("valueRanges", [])):
            rows = [r for r in vr.get("values", []) if any(v not in (None, "") for v in r)]
            if rows:
                out[f"{base}.{_safe_name(t)}"] = _table(rows)
        return out

    def discover(self) -> DiscoverResult:
        objs = []
        for name, (header, rows) in self._all().items():
            o = SourceObject(name, "sheet", [Column(h, _guess([r[i] for r in rows])) for i, h in enumerate(header)])
            classify(o)
            objs.append(o)
        return DiscoverResult(objects=objs, summary=f"{len(objs)} tabs")


class GoogleDocsAdapter(_GoogleAdapter):
    TYPE = "gdocs"
    LABEL = "Google Docs"
    ICON = "file-text"
    LIST_LABEL = "Documents"
    API = DOCS
    SCHEMA = [
        F("credentials", "Service-account key file", "str", "",
          tip="JSON key of a Google service account; share each document with its e-mail as Viewer"),
        F("files", "Documents", "text", "",
          tip="One per line: a link or ID, optionally named - e.g. sop = https://docs.google.com/document/d/…"),
        F("max_chars", "Max characters per passage", "int", 1500, (200, 10000),
          tip="Long paragraphs are split into passages of at most this length"),
    ]

    def _fetch(self, name, fid, timeout_s):
        doc = self._get(f"{DOCS}{fid}", timeout_s)
        base = _safe_name(name or doc.get("title", fid))
        try:
            mc = max(200, min(10000, int(self.setting("max_chars"))))
        except (TypeError, ValueError):
            mc = 1500

        def text(elements) -> str:
            return "".join((e.get("textRun") or {}).get("content", "") for e in elements or [])

        passages, tables, section, n = [], [], "", 0
        for el in (doc.get("body") or {}).get("content", []):
            if "table" in el:
                rows = []
                for tr in el["table"].get("tableRows", []):
                    rows.append([re.sub(r"\s+", " ", "".join(text(p.get("paragraph", {}).get("elements"))
                                                             for p in tc.get("content", []))).strip()
                                 for tc in tr.get("tableCells", [])])
                if rows:
                    tables.append(rows)
                continue
            p = el.get("paragraph")
            if not p:
                continue
            txt = text(p.get("elements")).strip()
            if not txt:
                continue
            style = (p.get("paragraphStyle") or {}).get("namedStyleType", "")
            if style.startswith("HEADING") or style in ("TITLE", "SUBTITLE"):
                section = re.sub(r"\s+", " ", txt)[:200]
            for piece in _split(txt, mc):
                n += 1
                passages.append([section, n, piece])
        out = {base: (DOC_HEADER, passages)}
        for i, rows in enumerate(tables, 1):
            out[f"{base}.table{i}"] = _table(rows)
        return out

    def discover(self) -> DiscoverResult:
        objs, nd = [], 0
        for name, (header, rows) in self._all().items():
            if header is DOC_HEADER:
                nd += 1
                o = SourceObject(name, "document", [Column("section", "string"), Column("para", "integer"),
                                                    Column("text", "string")])
            else:
                o = SourceObject(name, "table", [Column(h, _guess([r[i] for r in rows])) for i, h in enumerate(header)])
            classify(o)
            if header is DOC_HEADER and not o.note:
                o.note = f"{len(rows)} passages"
            objs.append(o)
        nt = len(objs) - nd
        return DiscoverResult(objects=objs, summary=f"{nd} documents" + (f", {nt} tables" if nt else ""))
