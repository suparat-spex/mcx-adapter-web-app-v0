"""
sql.py - SQL Server, PostgreSQL, MySQL and SQLite tables and views. No Qt.

Read-only by design: use a read-only login (SQL Server: `mcp_reader` with db_datareader). There
is no path from a tool to SQL text: `select()` takes a structured Query whose column names must
exist in the discovered object (quoted per dialect) and whose values are always bound parameters.

Drivers are imported only when used: pyodbc (SQL Server), psycopg2 (PostgreSQL), pymysql (MySQL),
sqlite3 (built in - the demo database).
"""
from __future__ import annotations

import re
import sqlite3
import time

from mcx_core.adapters.base import Adapter, AdapterError, DiscoverResult, F, Query
from mcx_core.config import Column, SourceObject

PII_HINT = re.compile(r"(operator|employee|person|first_?name|last_?name|full_?name|e_?mail|phone|mobile|"
                      r"address|comment|note|badge|user_?name|login|ip_addr|salary|wage|birth|supervisor|technician|signature)", re.I)
BLOCK_HINT = re.compile(r"(^sys_|^sys\.|audit|login|password|credential|token|secret|session)", re.I)
AGGS = {"avg": "AVG", "min": "MIN", "max": "MAX", "sum": "SUM", "count": "COUNT"}
OPS = {"=", ">=", "<=", ">", "<", "in", "contains"}   # contains: LIKE with a bound, escaped value


def classify(obj: SourceObject):
    """First guess on discovery: personal-data columns, blocked system objects, risk."""
    for c in obj.columns:
        c.pii = bool(PII_HINT.search(c.name))
    obj.blocked = bool(BLOCK_HINT.search(obj.name))
    obj.risk = "R2" if obj.pii_columns() else "R1"
    if obj.blocked:
        obj.note = "blocked by rule"
    elif obj.pii_columns():
        obj.note = "personal data"


def odbc_driver(installed: list, wanted: str) -> str:
    """The configured SQL Server ODBC driver if installed, else the newest installed 'ODBC Driver N for
    SQL Server' (17 when 18 is set, ...). The legacy 'SQL Server' driver is never picked by itself."""
    if wanted in installed:
        return wanted
    found = sorted(((int(m.group(1)), d) for d in installed
                    if (m := re.fullmatch(r"ODBC Driver (\d+) for SQL Server", d))), reverse=True)
    if found:
        return found[0][1]
    raise AdapterError(f"ODBC driver {wanted!r} is not installed on this computer - install Microsoft "
                       f"'ODBC Driver 18 for SQL Server' or set ODBC driver to one of: "
                       f"{', '.join(installed) or 'none installed'}")


def _type(t: str) -> str:
    t = (t or "").lower()
    if t in ("bit", "bool", "boolean"):            # yes / no flags: never averaged or summed
        return "boolean"
    if "int" in t:
        return "integer"
    if any(x in t for x in ("real", "float", "double", "dec", "numeric", "money")):
        return "number"
    if "date" in t or "time" in t:
        return "datetime" if "time" in t else "date"
    return "string"


class SqlAdapter(Adapter):
    TYPE = "sql"
    LABEL = "SQL database"
    FAMILY = "table"
    CATEGORY = "Databases"
    ICON = "database"
    DIALECT = "sqlite"
    SCHEMA = [
        F("dialect", "Database", "choice", "sqlserver",
          {"sqlserver": "SQL Server", "postgres": "PostgreSQL", "mysql": "MySQL", "sqlite": "SQLite file"}),
        F("host", "Server", "str", "localhost", tip="Host name or IP (SQL Server: host\\instance allowed)"),
        F("port", "Port", "int", 0, (0, 65535), "0 = default for the database"),
        F("database", "Database name / file", "str", ""),
        F("auth", "Authentication", "choice", "sql",
          {"sql": "SQL Server login", "windows": "Windows (account running MCX Adapter)"},
          tip="SQL Server only. Windows: no login / password - the Windows account running the adapter "
              "must be read-only (db_datareader)"),
        F("user", "Login", "str", "mcp_reader", tip="A read-only login (db_datareader)"),
        F("password", "Password", "password", "", tip="Or ${ENV_VAR} to keep it out of the file"),
        F("schema", "Schema filter", "str", "", tip="Only this schema (empty = all)"),
        F("encrypt", "Encrypt connection", "bool", True),
        F("driver", "ODBC driver", "str", "ODBC Driver 18 for SQL Server"),
    ]

    # ---- connection
    @property
    def dialect(self) -> str:
        return self.setting("dialect", "sqlite")

    def _conn(self, timeout_s: float = 10.0):
        d, s = self.dialect, self.setting
        if d == "sqlite":
            db = s("database")
            if not db:
                raise AdapterError("no database file set")
            return sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=timeout_s, check_same_thread=False)
        if d == "sqlserver":
            try:
                import pyodbc
            except ImportError:
                raise AdapterError("SQL Server needs the pyodbc package (pip install pyodbc)") from None
            if s("auth") != "windows" and "user" in self.settings and not str(self.settings["user"] or "").strip():
                raise AdapterError("no login set - enter a SQL Server login and password, or set "
                                   "Authentication to Windows")
            server = s("host") + (f",{s('port')}" if int(s("port") or 0) else "")
            cs = (f"DRIVER={{{odbc_driver(pyodbc.drivers(), s('driver'))}}};SERVER={server};DATABASE={s('database')};"
                  + ("Trusted_Connection=yes;" if s("auth") == "windows" else f"UID={s('user')};PWD={s('password')};")
                  + f"Encrypt={'yes' if s('encrypt') else 'no'};TrustServerCertificate=yes;"
                  "ApplicationIntent=ReadOnly")
            try:
                c = pyodbc.connect(cs, timeout=int(timeout_s), readonly=True)
            except pyodbc.Error as e:
                msg = str(e)
                if "08001" in msg or "HYT00" in msg:
                    raise AdapterError(f"SQL Server {server!r} not found or not reachable - for a named instance "
                                       f"(e.g. SQL Express) use HOST\\INSTANCE such as localhost\\SQLEXPRESS, "
                                       f"and check the SQL Server service is running") from None
                if "28000" in msg or "18456" in msg:
                    raise AdapterError("SQL Server refused the login - check Authentication, login and password "
                                       "(SQL Server logins need 'SQL Server and Windows Authentication mode')") \
                        from None
                raise
            c.timeout = int(timeout_s)
            return c
        if d == "postgres":
            try:
                import psycopg2
            except ImportError:
                raise AdapterError("PostgreSQL needs psycopg2 (pip install psycopg2-binary)") from None
            c = psycopg2.connect(host=s("host"), port=int(s("port") or 5432), dbname=s("database"),
                                 user=s("user"), password=s("password"), connect_timeout=int(timeout_s),
                                 options=f"-c statement_timeout={int(timeout_s * 1000)} "
                                         "-c default_transaction_read_only=on")
            return c
        if d == "mysql":
            try:
                import pymysql
            except ImportError:
                raise AdapterError("MySQL needs pymysql (pip install pymysql)") from None
            return pymysql.connect(host=s("host"), port=int(s("port") or 3306), database=s("database"),
                                   user=s("user"), password=s("password"), connect_timeout=int(timeout_s),
                                   read_timeout=int(timeout_s))
        raise AdapterError(f"unknown database type {d!r}")

    def ping(self):
        c = self._conn(self.limits.timeout_s if self.limits else 5)
        try:
            c.cursor().execute("SELECT 1")
        finally:
            c.close()

    # ---- discovery
    def discover(self) -> DiscoverResult:
        c = self._conn()
        try:
            objs = self._discover_sqlite(c) if self.dialect == "sqlite" else self._discover_info_schema(c)
        finally:
            c.close()
        for o in objs:
            classify(o)
        nt = sum(o.kind == "table" for o in objs)
        return DiscoverResult(objects=objs, summary=f"{nt} tables · {len(objs) - nt} views")

    def _discover_sqlite(self, c):
        out = []
        for name, kind in c.execute("SELECT name, type FROM sqlite_master WHERE type IN ('table','view') "
                                    "AND name NOT LIKE 'sqlite_%' ORDER BY name"):
            cols = [Column(r[1], _type(r[2])) for r in c.execute(f"PRAGMA table_info({self.quote(name)})")]
            out.append(SourceObject(name, kind, cols))
        return out

    def _discover_info_schema(self, c):
        cur = c.cursor()
        flt, args = "", []
        sch = self.setting("schema")
        ph = self.placeholder
        if sch:
            flt, args = f" AND t.table_schema = {ph}", [sch]
        cur.execute("SELECT t.table_schema, t.table_name, t.table_type, c.column_name, c.data_type "
                    "FROM information_schema.tables t JOIN information_schema.columns c "
                    "ON c.table_schema = t.table_schema AND c.table_name = t.table_name "
                    "WHERE t.table_schema NOT IN ('information_schema','pg_catalog','sys','mysql',"
                    "'performance_schema')" + flt + " ORDER BY t.table_schema, t.table_name, c.ordinal_position",
                    args)
        objs: dict[str, SourceObject] = {}
        for schema, table, ttype, col, dtype in cur.fetchall():
            name = f"{schema}.{table}"
            o = objs.get(name)
            if o is None:
                o = objs[name] = SourceObject(name, "view" if "VIEW" in str(ttype).upper() else "table", [])
            o.columns.append(Column(col, _type(dtype)))
        return list(objs.values())

    # ---- query
    @property
    def placeholder(self) -> str:
        return "?" if self.dialect in ("sqlite", "sqlserver") else "%s"

    def quote(self, ident: str) -> str:
        parts = str(ident).split(".")
        if self.dialect == "sqlserver":
            return ".".join("[" + p.replace("]", "]]") + "]" for p in parts)
        if self.dialect == "mysql":
            return ".".join("`" + p.replace("`", "``") + "`" for p in parts)
        return ".".join('"' + p.replace('"', '""') + '"' for p in parts)

    def build(self, obj: SourceObject, q: Query) -> tuple[str, list]:
        """Structured query -> (SQL, params). Every identifier must be a discovered column."""
        known = {c.name.lower(): c.name for c in obj.columns}
        pii = {c.lower() for c in obj.pii_columns()}

        def col(name):
            n = known.get(str(name).lower())
            if n is None:
                raise AdapterError(f"unknown column {name!r} in {obj.name}")
            if n.lower() in pii:
                raise AdapterError(f"column {n!r} is personal data")
            return self.quote(n)

        sel = []
        for c, agg, alias in q.select:
            if agg:
                if agg not in AGGS:
                    raise AdapterError(f"unknown aggregate {agg!r}")
                oc = obj.column(c) if c != "*" else None
                if oc is not None and oc.type == "boolean" and agg != "count":
                    raise AdapterError(f"{c} is a yes / no column - {agg} is not possible (use count, "
                                       f"or filter / group by it)")
                expr = f"{AGGS[agg]}({col(c)})" if c != "*" else "COUNT(*)"
            else:
                expr = col(c)
            sel.append(f"{expr} AS {self.quote(alias or c)}")
        if not sel:
            sel = [self.quote(c.name) for c in obj.columns if c.name.lower() not in pii]
        where, params = [], []
        ph = self.placeholder
        for c, op, v in q.where:
            if op not in OPS:
                raise AdapterError(f"operator {op!r} not allowed")
            if op == "in":
                vals = list(v)[:100]
                where.append(f"{col(c)} IN ({', '.join([ph] * len(vals))})")
                params += vals
            elif op == "contains":
                like = "ILIKE" if self.dialect == "postgres" else "LIKE"
                where.append(f"{col(c)} {like} {ph} ESCAPE '!'")
                params.append("%" + re.sub(r"([!%_\[])", r"!\1", str(v)) + "%")
            else:
                where.append(f"{col(c)} {op} {ph}")
                params.append(v)
        sql = f"SELECT {', '.join(sel)} FROM {self.quote(obj.name)}"
        if where:
            sql += " WHERE " + " AND ".join(where)
        if q.group:
            sql += " GROUP BY " + ", ".join(col(g) for g in q.group)
        aliases = {(a or c).lower() for c, _, a in q.select}
        if q.order:
            sql += " ORDER BY " + ", ".join(
                (self.quote(o) if o.lower() in aliases else col(o)) + (" DESC" if desc else "") for o, desc in q.order)
        n = int(q.limit) + 1                       # one more than allowed: tells us it was truncated
        if self.dialect == "sqlserver":
            sql = sql.replace("SELECT ", f"SELECT TOP {n} ", 1)
        else:
            sql += f" LIMIT {n}"
        return sql, params

    def select(self, obj, q: Query, timeout_s: float = 10.0):
        sql, params = self.build(obj, q)
        c = self._conn(timeout_s)
        try:
            if self.dialect == "sqlite":
                deadline = time.monotonic() + timeout_s
                c.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 2000)
            cur = c.cursor()
            try:
                cur.execute(sql, params)
            except Exception as e:
                if "interrupt" in str(e).lower():
                    raise AdapterError(f"timeout after {timeout_s:g} s") from None
                raise
            cols = [d[0] for d in cur.description]
            return cols, [list(r) for r in cur.fetchall()]
        finally:
            c.close()
