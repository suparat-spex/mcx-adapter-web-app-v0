"""
base.py - the adapter contract. One plug-in per source type. No Qt.

    SCHEMA                    config_schema: fields of the Connect step (Studio builds the form)
    connect() / close()
    health() -> Health        state online | error | stopped, message, latency ms
    discover()                -> DiscoverResult (objects for tables / files, items for tag sources)
    read(items)               -> [Reading]        live values (tag sources)
    history(item, t0, t1, n)  -> [(t, value)] or None when the source keeps no history
    select(obj, query)        -> (columns, rows)   table sources: a structured Query, never SQL text

Families: "table" (SQL, CSV / Excel, REST) serve curated tools; "tags" (OPC UA, Modbus, MQTT,
simulator) feed the ISA-95 model and the generic tools.

Field dicts (also used by the Properties grid): key, label, type (str | password | int | float |
bool | choice | text), default, extra (choices / (min, max)), tip.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


def F(key, label, type="str", default=None, extra=None, tip=""):
    return {"key": key, "label": label, "type": type, "default": default, "extra": extra, "tip": tip}


@dataclass
class Health:
    state: str = "stopped"            # stopped | connecting | online | error
    message: str = ""
    latency_ms: float | None = None
    checked: float = 0.0


@dataclass
class Reading:
    value: object = None
    quality: str = "bad"              # good | uncertain | bad
    t: float = 0.0
    error: str = ""


@dataclass
class DiscoverResult:
    objects: list = field(default_factory=list)      # [SourceObject]
    items: list = field(default_factory=list)        # [Item] (address + suggested path / name)
    summary: str = ""


@dataclass
class Query:
    """A structured read of one table - built by tools.py from typed parameters only."""
    select: list = field(default_factory=list)      # [(column, agg or None, alias)]
    where: list = field(default_factory=list)       # [(column, op, value)]
    group: list = field(default_factory=list)       # [column]
    order: list = field(default_factory=list)       # [(column or alias, desc)]
    limit: int = 500


class AdapterError(Exception):
    pass


class Adapter:
    TYPE = ""
    LABEL = ""
    FAMILY = "table"                 # table | tags
    CATEGORY = "Databases"           # tree / Add source menu group
    ICON = "database"
    SCHEMA: list = []
    LIVE = True                      # implemented (False = shown as planned)

    def __init__(self, cfg, limits=None):
        self.cfg = cfg
        self.settings = cfg.resolved_settings()
        self.limits = limits
        self.last_health = Health()

    # ---- contract
    def connect(self):
        pass

    def close(self):
        pass

    def ping(self):
        """Cheapest round trip that proves the source answers (raises on failure)."""
        raise NotImplementedError

    def health(self) -> Health:
        t0 = time.perf_counter()
        try:
            self.ping()
            h = Health("online", "", (time.perf_counter() - t0) * 1000, time.time())
        except Exception as e:                       # a source error is a state, not a crash
            h = Health("error", short_error(e), None, time.time())
        self.last_health = h
        return h

    def discover(self) -> DiscoverResult:
        return DiscoverResult()

    def read(self, items) -> list:
        raise AdapterError(f"{self.LABEL} has no live values")

    def history(self, item, t0: float, t1: float, max_points: int):
        return None

    def select(self, obj, q: Query, timeout_s: float = 10.0):
        raise AdapterError(f"{self.LABEL} has no tables")

    def setting(self, key, default=None):
        v = self.settings.get(key)
        if v in (None, ""):
            for f in self.SCHEMA:
                if f["key"] == key:
                    return f["default"] if default is None else default
            return default
        return v


def short_error(e: Exception) -> str:
    s = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
    return s[:200]
