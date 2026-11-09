"""Adapter registry: source type -> plug-in class. New source type: subclass Adapter, add it here,
test it against a local server / file in tests/test_adapters.py."""
from __future__ import annotations

from mcx_core.adapters.base import Adapter, AdapterError, DiscoverResult, Health, Query, Reading
from mcx_core.adapters.files import FilesAdapter, RestAdapter
from mcx_core.adapters.sql import SqlAdapter
from mcx_core.adapters.tags import ModbusAdapter, MqttAdapter, OpcUaAdapter, SimAdapter

ADAPTERS: dict[str, type[Adapter]] = {a.TYPE: a for a in (
    SqlAdapter, OpcUaAdapter, ModbusAdapter, MqttAdapter, FilesAdapter, RestAdapter, SimAdapter)}

# shown in Add source as planned (later in the roadmap), not selectable
PLANNED = ["Oracle", "InfluxDB", "PI / Canary historian", "PDF / Word documents", "NX Logger",
           "Siemens S7", "EtherNet/IP"]

DIALECT_LABEL = {"sqlserver": "SQL Server", "postgres": "PostgreSQL", "mysql": "MySQL", "sqlite": "SQLite"}


def make_adapter(cfg, limits=None) -> Adapter:
    cls = ADAPTERS.get(cfg.type)
    if cls is None:
        raise AdapterError(f"unknown source type {cfg.type!r}")
    return cls(cfg, limits)


def type_label(cfg) -> str:
    """'SQL Server', 'OPC UA', 'Modbus TCP', 'Excel' ... for lists and the tree."""
    if cfg.type == "sql":
        return DIALECT_LABEL.get(cfg.settings.get("dialect", "sqlserver"), "SQL")
    if cfg.type == "modbus":
        return "Modbus RTU" if cfg.settings.get("mode") == "rtu" else "Modbus TCP"
    if cfg.type == "files":
        p = str(cfg.settings.get("path", "")).lower()
        return "CSV" if p.endswith(".csv") else "Excel"
    return {"opcua": "OPC UA", "mqtt": "MQTT", "rest": "REST API", "sim": "Simulator"}.get(cfg.type, cfg.type)


def family(cfg) -> str:
    cls = ADAPTERS.get(cfg.type)
    return cls.FAMILY if cls else "table"


__all__ = ["ADAPTERS", "PLANNED", "Adapter", "AdapterError", "DiscoverResult", "Health", "Query", "Reading",
           "make_adapter", "type_label", "family"]
