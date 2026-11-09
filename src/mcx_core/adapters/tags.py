"""
tags.py - tag sources: plant simulator, Modbus TCP / RTU, OPC UA client, MQTT (+ Sparkplug B
planned). No Qt. They feed the ISA-95 model; the generic tools read them.

Item addresses
  sim      <signal>                    temperature | pressure | speed | power | state | good | reject
           optional seed in the path, the value is a deterministic function of time (history too)
  modbus   unit:area:offset:dtype:order   1:holding:4:float32:ABCD   (area holding | input | coil |
           discrete; dtype int16 | uint16 | int32 | uint32 | float32 | float64; 0-based offsets)
  opcua    node id                     ns=2;s=Line2.Press1.Temperature
  mqtt     topic[#json.path]           plant/l2/press1#temperature
Reads are batched per call; the runtime keeps a short cache and a local history ring (runtime.py)
for sources without their own history (Modbus, MQTT).
"""
from __future__ import annotations

import hashlib
import inspect
import json
import logging
import math
import struct
import threading
import time

from mcx_core.adapters.base import Adapter, AdapterError, DiscoverResult, F, Reading
from mcx_core.config import Item

# ======================================================================= simulator
SIM_SIGNALS = {
    # name: (unit, lo, hi, base, amplitude, period s, noise)
    "temperature": ("°C", 20, 95, 62, 8, 1800, 0.6),
    "pressure": ("bar", 0, 10, 6.2, 0.6, 600, 0.05),
    "speed": ("rpm", 0, 1800, 1450, 60, 900, 4),
    "power": ("kW", 0, 250, 118, 22, 3600, 2),
    "vibration": ("mm/s", 0, 12, 2.4, 0.8, 1200, 0.15),
    "good_count": ("pcs", None, None, 0, 0, 0, 0),
    "reject_count": ("pcs", None, None, 0, 0, 0, 0),
    "state": ("", 0, 2, 0, 0, 0, 0),                 # 0 running, 1 idle, 2 stopped
}


def _h(*parts) -> float:
    """Repeatable pseudo-random in [0, 1)."""
    d = hashlib.blake2b("|".join(map(str, parts)).encode(), digest_size=8).digest()
    return int.from_bytes(d, "big") / 2 ** 64


def sim_value(address: str, t: float) -> float:
    sig, _, seed = address.partition("@")
    unit, lo, hi, base, amp, period, noise = SIM_SIGNALS.get(sig, SIM_SIGNALS["temperature"])
    k = _h(seed) * 2 * math.pi
    slot = int(t // 300)                              # 5-minute blocks: machine state
    stopped = _h(seed, "stop", slot) < 0.06
    if sig == "state":
        return 2.0 if stopped else (1.0 if _h(seed, "idle", slot) < 0.1 else 0.0)
    if sig in ("good_count", "reject_count"):
        rate = 0.9 if sig == "good_count" else 0.02 + 0.01 * _h(seed)
        day0 = t - (t % 86400)
        return float(int((t - day0) * rate * (0.6 + 0.4 * _h(seed, "rate"))))
    v = base * (0.9 + 0.2 * _h(seed, "base")) + amp * math.sin(2 * math.pi * t / period + k)
    v += noise * (2 * _h(seed, int(t)) - 1)
    if stopped and sig in ("speed", "power"):
        v = 0.0
    return round(v, 3)


class SimAdapter(Adapter):
    TYPE = "sim"
    LABEL = "Plant simulator"
    FAMILY = "tags"
    CATEGORY = "Industrial protocols"
    ICON = "activity"
    SCHEMA = [
        F("site", "Site", "str", "Bangkok"),
        F("areas", "Areas and lines", "text", "Assembly: L1, L2\nMolding: M1",
          tip="One area per line: Area: line, line ..."),
        F("machines", "Machines per line", "int", 2, (1, 10)),
    ]

    def ping(self):
        pass

    def _layout(self):
        out = []
        for line in str(self.setting("areas")).splitlines():
            area, _, lines = line.partition(":")
            if area.strip():
                out.append((area.strip(), [x.strip() for x in lines.split(",") if x.strip()] or ["L1"]))
        return out

    def discover(self) -> DiscoverResult:
        items = []
        site = self.setting("site")
        for area, lines in self._layout():
            for ln in lines:
                for m in range(1, int(self.setting("machines")) + 1):
                    path = f"{site}/{area}/{ln}/{'Press' if area.lower() != 'molding' else 'Mold'}{m}"
                    for sig, (unit, lo, hi, *_r) in SIM_SIGNALS.items():
                        items.append(Item(f"{sig}@{path}", path, sig, unit, lo, hi,
                                          "int" if sig in ("good_count", "reject_count", "state") else "float",
                                          {"state": "0 running · 1 idle · 2 stopped"}.get(sig, "")))
        return DiscoverResult(items=items, summary=f"{len(items)} tags")

    def read(self, items):
        now = time.time()
        return [Reading(sim_value(it.address, now), "good", now) for it in items]

    def history(self, item, t0, t1, max_points):
        n = max(2, min(max_points, int((t1 - t0) // 10) + 1))
        step = (t1 - t0) / (n - 1)
        return [(t0 + i * step, sim_value(item.address, t0 + i * step)) for i in range(n)]


# ======================================================================= Modbus
DTYPES = {"int16": (1, ">h"), "uint16": (1, ">H"), "int32": (2, ">i"), "uint32": (2, ">I"),
          "float32": (2, ">f"), "float64": (4, ">d")}


def parse_modbus(addr: str, default_unit: int = 1):
    p = [x.strip() for x in addr.split(":")]
    if len(p) < 3:
        raise AdapterError(f"Modbus address {addr!r}: expected unit:area:offset[:dtype[:order]]")
    unit = int(p[0] or default_unit)
    area = p[1].lower()
    if area not in ("holding", "input", "coil", "discrete"):
        raise AdapterError(f"Modbus address {addr!r}: area must be holding / input / coil / discrete")
    return unit, area, int(p[2]), (p[3] if len(p) > 3 else "uint16").lower(), (p[4] if len(p) > 4 else "ABCD").upper()


def regs_to_value(regs, dtype, order):
    n, fmt = DTYPES[dtype]
    words = list(regs[:n])
    if n > 1 and order in ("CDAB", "DCBA"):
        words.reverse()
    raw = b"".join(struct.pack(">H", w & 0xFFFF) for w in words)
    if order in ("BADC", "DCBA"):
        raw = b"".join(raw[i:i + 2][::-1] for i in range(0, len(raw), 2))
    return struct.unpack(fmt, raw)[0]


class ModbusAdapter(Adapter):
    TYPE = "modbus"
    LABEL = "Modbus TCP / RTU"
    FAMILY = "tags"
    CATEGORY = "Industrial protocols"
    ICON = "ethernet-port"
    SCHEMA = [
        F("mode", "Mode", "choice", "tcp", {"tcp": "Modbus TCP", "rtu": "Modbus RTU (serial)"}),
        F("host", "Host", "str", "127.0.0.1"),
        F("port", "TCP port / serial port", "str", "502"),
        F("baudrate", "Baud rate (RTU)", "int", 9600, (1200, 115200)),
        F("unit", "Default unit id", "int", 1, (0, 247)),
        F("timeout_s", "Timeout (s)", "float", 1.0, (0.1, 30)),
        F("register_map", "Register map", "choice", "",
          {"": "Enter items by hand", "factory_energy": "Factory energy demo (11 meters)"},
          "Modbus cannot be browsed: discovery reads a known register map"),
    ]

    def __init__(self, cfg, limits=None):
        super().__init__(cfg, limits)
        self.client = None
        self._lock = threading.Lock()
        self._kw = "slave"

    def connect(self):
        from pymodbus.client import ModbusSerialClient, ModbusTcpClient
        logging.getLogger("pymodbus").setLevel(logging.CRITICAL)     # errors are reported as source health
        t = float(self.setting("timeout_s"))
        if self.setting("mode") == "rtu":
            self.client = ModbusSerialClient(str(self.setting("port")), baudrate=int(self.setting("baudrate")),
                                             timeout=t, retries=1)
        else:
            self.client = ModbusTcpClient(self.setting("host"), port=int(self.setting("port") or 502),
                                          timeout=t, retries=1)
        params = inspect.signature(self.client.read_holding_registers).parameters
        self._kw = "device_id" if "device_id" in params else "slave"
        if not self.client.connect():
            raise AdapterError(f"cannot connect to {self.setting('host')}:{self.setting('port')}")

    def close(self):
        if self.client is not None:
            self.client.close()
            self.client = None

    def _read_raw(self, unit, area, off, n):
        if self.client is None or not getattr(self.client, "connected", True):
            self.connect()
        fn = {"holding": "read_holding_registers", "input": "read_input_registers",
              "coil": "read_coils", "discrete": "read_discrete_inputs"}[area]
        rr = getattr(self.client, fn)(off, count=n, **{self._kw: unit})
        if rr.isError():
            if not hasattr(rr, "exception_code"):
                self.close()
            raise AdapterError(str(rr))
        return rr

    def ping(self):
        with self._lock:
            self._read_raw(int(self.setting("unit")), "holding", 0, 1)

    def discover(self) -> DiscoverResult:
        if self.setting("register_map") == "factory_energy":
            from mcx_core.sim.factory_energy import register_map
            items = [Item(**d) for d in register_map(self.cfg.isa95 or "Bangkok/Energy")]
            return DiscoverResult(items=items, summary=f"{len(items)} registers")
        return DiscoverResult(items=list(self.cfg.items), summary="Modbus cannot be browsed - add items by address")

    def read(self, items):
        out = []
        now = time.time()
        with self._lock:
            for it in items:
                try:
                    unit, area, off, dtype, order = parse_modbus(it.address, int(self.setting("unit")))
                    if area in ("coil", "discrete"):
                        v = float(bool(self._read_raw(unit, area, off, 1).bits[0]))
                    else:
                        rr = self._read_raw(unit, area, off, DTYPES.get(dtype, (1,))[0])
                        v = regs_to_value(rr.registers, dtype, order)
                        v = round(v, 6) if isinstance(v, float) else v
                    out.append(Reading(v, "good", now))
                except Exception as e:
                    out.append(Reading(None, "bad", now, str(e)[:120]))
        return out


# ======================================================================= OPC UA
class OpcUaAdapter(Adapter):
    TYPE = "opcua"
    LABEL = "OPC UA client"
    FAMILY = "tags"
    CATEGORY = "Industrial protocols"
    ICON = "network"
    SCHEMA = [
        F("endpoint", "Endpoint URL", "str", "opc.tcp://localhost:4840"),
        F("username", "User", "str", ""),
        F("password", "Password", "password", ""),
        F("timeout_s", "Timeout (s)", "float", 4.0, (0.5, 60)),
        F("browse_root", "Browse from node", "str", "", tip="Node id to start discovery (empty = Objects)"),
        F("browse_depth", "Browse depth", "int", 6, (1, 12)),
    ]

    def __init__(self, cfg, limits=None):
        super().__init__(cfg, limits)
        self.client = None
        self._lock = threading.Lock()

    def connect(self):
        from asyncua.sync import Client
        c = Client(self.setting("endpoint"), timeout=float(self.setting("timeout_s")))
        if self.setting("username"):
            c.set_user(self.setting("username"))
            c.set_password(self.setting("password") or "")
        c.connect()
        self.client = c

    def close(self):
        if self.client is not None:
            try:
                self.client.disconnect()
            except Exception:
                pass
            self.client = None

    def _c(self):
        if self.client is None:
            self.connect()
        return self.client

    def ping(self):
        with self._lock:
            try:
                self._c().get_node("i=2259").read_value()          # Server_ServerStatus_State
            except Exception:
                self.close()
                raise

    def discover(self) -> DiscoverResult:
        from asyncua import ua
        from mcx_core.model import suggest_path
        items, prefix = [], self.cfg.isa95
        with self._lock:
            c = self._c()
            root = c.get_node(self.setting("browse_root")) if self.setting("browse_root") else c.get_objects_node()

            def walk(node, names, depth):
                if depth > int(self.setting("browse_depth")) or len(items) >= 5000:
                    return
                for ch in node.get_children():
                    try:
                        name = ch.read_browse_name().Name
                        if name == "Server" and not names:
                            continue
                        if ch.read_node_class() == ua.NodeClass.Variable:
                            path, nm = suggest_path("/".join(names + [name]), prefix)
                            items.append(Item(ch.nodeid.to_string(), path, nm))
                        else:
                            walk(ch, names + [name], depth + 1)
                    except Exception:
                        continue
            walk(root, [], 1)
        return DiscoverResult(items=items, summary=f"{len(items)} tags")

    def read(self, items):
        now = time.time()
        with self._lock:
            c = self._c()
            nodes = [c.get_node(it.address) for it in items]
            try:
                vals = c.read_values(nodes)
                return [Reading(v if not isinstance(v, bool) else float(v), "good", now) for v in vals]
            except Exception as e:
                if "Bad" not in str(e):
                    self.close()
                    raise
            out = []
            for n in nodes:
                try:
                    out.append(Reading(n.read_value(), "good", now))
                except Exception as e:
                    out.append(Reading(None, "bad", now, str(e)[:120]))
            return out

    def history(self, item, t0, t1, max_points):
        import datetime as dt
        with self._lock:
            try:
                node = self._c().get_node(item.address)
                vals = node.read_raw_history(dt.datetime.fromtimestamp(t0, dt.timezone.utc),
                                             dt.datetime.fromtimestamp(t1, dt.timezone.utc), numvalues=max_points)
            except Exception:
                return None                       # server has no history: the runtime ring answers
        return [(v.SourceTimestamp.timestamp(), v.Value.Value) for v in vals if v.Value is not None]


# ======================================================================= MQTT
class MqttAdapter(Adapter):
    TYPE = "mqtt"
    LABEL = "MQTT (+ Sparkplug B)"
    FAMILY = "tags"
    CATEGORY = "Industrial protocols"
    ICON = "radio"
    SCHEMA = [
        F("host", "Broker", "str", "localhost"),
        F("port", "Port", "int", 1883, (1, 65535)),
        F("tls", "TLS", "bool", False),
        F("username", "User", "str", ""),
        F("password", "Password", "password", ""),
        F("topics", "Subscribe", "str", "#", tip="Comma separated topic filters"),
        F("listen_s", "Discovery listen time (s)", "float", 4.0, (1, 60)),
        F("sparkplug", "Sparkplug B payloads", "bool", False, tip="Planned: decode spBv1.0 NDATA / DDATA"),
    ]

    def __init__(self, cfg, limits=None):
        super().__init__(cfg, limits)
        self.client = None
        self.values: dict[str, tuple[float, object]] = {}     # topic -> (t, payload)
        self._ok = threading.Event()

    def connect(self):
        import paho.mqtt.client as mqtt
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"mcx-adapter-{int(time.time()) % 100000}")
        if self.setting("username"):
            c.username_pw_set(self.setting("username"), self.setting("password") or "")
        if self.setting("tls"):
            c.tls_set()
        c.on_connect = self._on_connect
        c.on_message = self._on_message
        c.on_disconnect = lambda *a: self._ok.clear()
        c.connect_async(self.setting("host"), int(self.setting("port")), 30)
        c.loop_start()
        self.client = c
        if not self._ok.wait(float(self.limits.timeout_s if self.limits else 5)):
            raise AdapterError(f"no answer from broker {self.setting('host')}:{self.setting('port')}")

    def _on_connect(self, c, _u, _f, rc, _p=None):
        if not getattr(rc, "is_failure", False):
            for t in str(self.setting("topics") or "#").split(","):
                if t.strip():
                    c.subscribe(t.strip())
            self._ok.set()

    def _on_message(self, _c, _u, msg):
        try:
            payload = msg.payload.decode("utf-8")
            try:
                payload = json.loads(payload)
            except ValueError:
                pass
        except UnicodeDecodeError:
            return
        self.values[msg.topic] = (time.time(), payload)

    def close(self):
        if self.client is not None:
            self.client.loop_stop()
            self.client.disconnect()
            self.client = None

    def ping(self):
        if self.client is None:
            self.connect()
        if not self._ok.is_set():
            raise AdapterError("not connected to the broker")

    def discover(self) -> DiscoverResult:
        from mcx_core.model import suggest_path
        self.ping()
        time.sleep(float(self.setting("listen_s")))
        items = []
        for topic, (_t, p) in sorted(self.values.items()):
            if isinstance(p, dict):
                for k, v in _flatten(p):
                    if isinstance(v, (int, float)):
                        path, name = suggest_path(topic + "/" + k.replace(".", "_"), self.cfg.isa95)
                        items.append(Item(f"{topic}#{k}", path, name))
            elif isinstance(p, (int, float)):
                path, name = suggest_path(topic, self.cfg.isa95)
                items.append(Item(topic, path, name))
        return DiscoverResult(items=items, summary=f"{len(self.values)} topics · {len(items)} values")

    def read(self, items):
        self.ping()
        out = []
        for it in items:
            topic, _, key = it.address.partition("#")
            t, p = self.values.get(topic, (0.0, None))
            if key and isinstance(p, dict):
                p = dict(_flatten(p)).get(key)
            stale = t and time.time() - t > 300
            out.append(Reading(p, "bad" if p is None else ("uncertain" if stale else "good"), t))
        return out


def _flatten(d, prefix=""):
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else str(k)
        if isinstance(v, dict):
            yield from _flatten(v, key)
        else:
            yield key, v
