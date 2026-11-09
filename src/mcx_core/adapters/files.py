"""
files.py - CSV and Excel files (folder or single file) and REST / JSON APIs as tables. No Qt.

Each CSV file, each Excel sheet and each REST endpoint is one object. A query loads the object
into an in-memory SQLite table (cached until the file changes / for cache_s) and runs the same
structured Query as the SQL adapter - so filters, aggregates and limits behave identically.
"""
from __future__ import annotations

import copy
import csv
import json
import sqlite3
import time
import urllib.request
from pathlib import Path

from mcx_core.adapters.base import Adapter, AdapterError, DiscoverResult, F, Query
from mcx_core.adapters.sql import SqlAdapter, classify
from mcx_core.config import Column, SourceObject


def _guess(values) -> str:
    vals = [v for v in values if v not in (None, "")][:50]
    if not vals:
        return "string"
    def all_(fn):
        try:
            return all(fn(v) is not None for v in vals)
        except (TypeError, ValueError):
            return False
    if all(isinstance(v, bool) for v in vals):
        return "integer"
    if all_(lambda v: int(str(v))):
        return "integer"
    if all_(lambda v: float(str(v))):
        return "number"
    s = str(vals[0])
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return "date" if len(s) == 10 else "datetime"
    return "string"


class _Mem:
    """Rows -> in-memory SQLite table, queried through SqlAdapter.build (dialect sqlite)."""

    def __init__(self):
        self._q = SqlAdapter.__new__(SqlAdapter)
        self._q.settings = {"dialect": "sqlite"}

    def run(self, obj: SourceObject, header: list, rows: list, q: Query, timeout_s: float):
        obj = copy.copy(obj)
        obj.name = "data"                       # sheet names may hold dots / spaces: one fixed table name
        db = sqlite3.connect(":memory:")
        try:
            cols = ", ".join(self._q.quote(h) for h in header)
            db.execute(f"CREATE TABLE {self._q.quote(obj.name)} ({cols})")
            db.executemany(f"INSERT INTO {self._q.quote(obj.name)} VALUES ({', '.join('?' * len(header))})",
                           [list(r) + [None] * (len(header) - len(r)) for r in rows])
            sql, params = self._q.build(obj, q)
            deadline = time.monotonic() + timeout_s
            db.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 2000)
            cur = db.execute(sql, params)
            return [d[0] for d in cur.description], [list(r) for r in cur.fetchall()]
        finally:
            db.close()


def _num(v):
    if isinstance(v, str):
        try:
            return int(v)
        except ValueError:
            try:
                return float(v)
            except ValueError:
                return v
    return v


class FilesAdapter(Adapter):
    TYPE = "files"
    LABEL = "CSV / Excel files"
    FAMILY = "table"
    CATEGORY = "Files and APIs"
    ICON = "sheet"
    SCHEMA = [
        F("path", "Folder or file", "str", "", tip="A folder (every .csv / .xlsx in it) or one file"),
        F("delimiter", "CSV delimiter", "choice", ",", {",": "Comma", ";": "Semicolon", "\t": "Tab"}),
        F("encoding", "CSV encoding", "str", "utf-8-sig"),
    ]

    def __init__(self, cfg, limits=None):
        super().__init__(cfg, limits)
        self._cache: dict[str, tuple[float, list, list]] = {}

    def _files(self) -> list[Path]:
        p = Path(self.setting("path") or "")
        if not str(p) or not p.exists():
            raise AdapterError(f"not found: {p}")
        if p.is_file():
            return [p]
        return sorted(f for f in p.iterdir() if f.suffix.lower() in (".csv", ".xlsx", ".xlsm"))

    def ping(self):
        for f in self._files():
            try:
                with open(f, "rb") as fh:
                    fh.read(1)
            except PermissionError:
                raise AdapterError(f"{f.name}: file locked") from None

    def _load(self, f: Path) -> dict[str, tuple[list, list]]:
        """{object name: (header, rows)} for one file."""
        if f.suffix.lower() == ".csv":
            with open(f, newline="", encoding=self.setting("encoding")) as fh:
                rows = list(csv.reader(fh, delimiter=self.setting("delimiter") or ","))
            if not rows:
                return {f.stem: ([], [])}
            return {f.stem: (rows[0], [[_num(v) for v in r] for r in rows[1:]])}
        try:
            import openpyxl
        except ImportError:
            raise AdapterError("Excel files need openpyxl (pip install openpyxl)") from None
        try:
            wb = openpyxl.load_workbook(f, read_only=True, data_only=True)
        except PermissionError:
            raise AdapterError(f"{f.name}: file locked") from None
        out = {}
        for ws in wb.worksheets:
            rows = [list(r) for r in ws.iter_rows(values_only=True)]
            rows = [r for r in rows if any(v is not None for v in r)]
            if rows:
                header = [str(h) if h is not None else f"col{i + 1}" for i, h in enumerate(rows[0])]
                data = [[v.isoformat() if hasattr(v, "isoformat") else v for v in r] for r in rows[1:]]
                out[f"{f.stem}.{ws.title}"] = (header, data)
        wb.close()
        return out

    def _all(self) -> dict[str, tuple[list, list]]:
        out = {}
        for f in self._files():
            m = f.stat().st_mtime
            hit = self._cache.get(str(f))
            if hit is None or hit[0] != m:
                hit = (m, self._load(f))
                self._cache[str(f)] = hit
            out.update(hit[1])
        return out

    def discover(self) -> DiscoverResult:
        objs = []
        for name, (header, rows) in self._all().items():
            cols = [Column(h, _guess([r[i] if i < len(r) else None for r in rows])) for i, h in enumerate(header)]
            o = SourceObject(name, "sheet" if "." in name else "file", cols)
            classify(o)
            objs.append(o)
        return DiscoverResult(objects=objs, summary=f"{len(objs)} sheets / files")

    def select(self, obj, q: Query, timeout_s: float = 10.0):
        data = self._all().get(obj.name)
        if data is None:
            raise AdapterError(f"{obj.name} not found (file renamed or removed?)")
        return _Mem().run(obj, *data, q, timeout_s)


class RestAdapter(Adapter):
    TYPE = "rest"
    LABEL = "REST / JSON API"
    FAMILY = "table"
    CATEGORY = "Files and APIs"
    ICON = "globe"
    SCHEMA = [
        F("base_url", "Base URL", "str", "https://"),
        F("endpoints", "Endpoints", "text", "",
          tip="One per line: name = /path [| records.json.path], e.g. orders = /api/orders | data.items"),
        F("auth_header", "Authorization header", "password", "", tip="e.g. Bearer ${MES_TOKEN}"),
        F("verify_tls", "Verify TLS certificate", "bool", True),
    ]

    def _endpoints(self) -> dict[str, tuple[str, str]]:
        out = {}
        for line in str(self.setting("endpoints") or "").splitlines():
            if "=" in line:
                name, rest = line.split("=", 1)
                path, _, ptr = rest.partition("|")
                out[name.strip()] = (path.strip(), ptr.strip())
        return out

    def _get(self, path: str, timeout_s: float):
        url = self.setting("base_url").rstrip("/") + "/" + path.lstrip("/")
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        if self.setting("auth_header"):
            req.add_header("Authorization", self.setting("auth_header"))
        ctx = None
        if not self.setting("verify_tls", True):
            import ssl
            ctx = ssl._create_unverified_context()
        with urllib.request.urlopen(req, timeout=timeout_s, context=ctx) as r:
            return json.loads(r.read().decode("utf-8"))

    def ping(self):
        eps = self._endpoints()
        if not eps:
            raise AdapterError("no endpoints set")
        self._get(next(iter(eps.values()))[0], 5)

    def _records(self, name, timeout_s=10.0):
        path, ptr = self._endpoints()[name]
        data = self._get(path, timeout_s)
        for k in [p for p in ptr.split(".") if p]:
            data = data.get(k, []) if isinstance(data, dict) else []
        if isinstance(data, dict):
            data = [data]
        recs = [r for r in data if isinstance(r, dict)]
        header = list(dict.fromkeys(k for r in recs for k in r))
        rows = [[r.get(h) if not isinstance(r.get(h), (dict, list)) else json.dumps(r.get(h)) for h in header]
                for r in recs]
        return header, rows

    def discover(self) -> DiscoverResult:
        objs = []
        for name in self._endpoints():
            header, rows = self._records(name)
            o = SourceObject(name, "endpoint",
                             [Column(h, _guess([r[i] for r in rows])) for i, h in enumerate(header)])
            classify(o)
            objs.append(o)
        return DiscoverResult(objects=objs, summary=f"{len(objs)} endpoints")

    def select(self, obj, q: Query, timeout_s: float = 10.0):
        if obj.name not in self._endpoints():
            raise AdapterError(f"endpoint {obj.name} not configured")
        return _Mem().run(obj, *self._records(obj.name, timeout_s), q, timeout_s)

