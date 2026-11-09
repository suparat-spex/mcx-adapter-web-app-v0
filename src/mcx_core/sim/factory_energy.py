"""
factory_energy.py - factory energy simulator: 11 power meters on a local Modbus TCP server, for
demos and testing MCX Adapter's Modbus source (the "power meters" of the demo project).
Runs offline (no broker). No Qt.

  Main         incomer (transformer LV): the sum of the feeders minus solar
  Production   line 1, line 2, injection moulding - shift pattern (day 08-17 with a lunch dip,
               night shift 20-05 on line 1 and moulding, Sunday standby)
  Utilities    air compressors 1 / 2 (load / unload cycles, 2 assists at high demand), chiller
               (follows the outside temperature), cooling tower
  Building     office HVAC (office hours), lighting
  Solar        roof PV inverter (sun + clouds)

Events now and then: a compressor trips for ~10 min (kW 0 in a shift), the capacitor bank drops
out (incomer PF falls to ~0.78), two lines start together (a demand peak).

Every meter is one Modbus unit (1 - 11) with the same register map: holding registers, float32,
big-endian words (ABCD), address = offset below (0-based on the wire).

  0 Voltage (V, L-N avg)   2 Current (A, avg)   4 Active power (kW)   6 Reactive power (kvar)
  8 Apparent power (kVA)  10 Power factor      12 Frequency (Hz)     14 Energy (kWh, import counter)
 16 Current THD (%)

Run:
  python -m mcx_core.sim.factory_energy                     server on 127.0.0.1:5020
  python -m mcx_core.sim.factory_energy --speed 60          one factory hour per real minute
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as _dt
import math
import random
import struct
import sys
import threading
import time

PORT = 5020
# (unit id, key, name, group, rated kW, power factor, current THD %)
METERS = [
    (1, "main", "Incomer", "Main", 0, 0.0, 6.0),
    (2, "line1", "Line 1", "Production", 180, 0.88, 12.0),
    (3, "line2", "Line 2", "Production", 150, 0.87, 12.0),
    (4, "molding", "Injection moulding", "Production", 220, 0.90, 18.0),
    (5, "comp1", "Air compressor 1", "Utilities", 75, 0.86, 25.0),
    (6, "comp2", "Air compressor 2", "Utilities", 75, 0.86, 25.0),
    (7, "chiller", "Chiller", "Utilities", 120, 0.90, 10.0),
    (8, "tower", "Cooling tower", "Utilities", 22, 0.85, 8.0),
    (9, "hvac", "Office HVAC", "Building", 40, 0.92, 9.0),
    (10, "light", "Lighting", "Building", 25, 0.95, 15.0),
    (11, "solar", "Solar PV", "Solar", 200, 1.0, 3.0),
]
# (key, tag suffix, unit, decimals, register offset, description)
FIELDS = [
    ("v", "V", "V", 1, 0, "Voltage L-N (average)"),
    ("i", "A", "A", 1, 2, "Current (average)"),
    ("kw", "kW", "kW", 1, 4, "Active power"),
    ("kvar", "kvar", "kvar", 1, 6, "Reactive power"),
    ("kva", "kVA", "kVA", 1, 8, "Apparent power"),
    ("pf", "PF", "", 3, 10, "Power factor"),
    ("hz", "Hz", "Hz", 2, 12, "Frequency"),
    ("kwh", "kWh", "kWh", 1, 14, "Active energy counter (import; solar: export)"),
    ("thd", "THD", "%", 1, 16, "Current total harmonic distortion"),
]
REGS = 18                                            # registers per meter
SHORT = {"main": "Main", "line1": "Line1", "line2": "Line2", "molding": "Molding", "comp1": "Comp1",
         "comp2": "Comp2", "chiller": "Chiller", "tower": "Tower", "hvac": "HVAC", "light": "Light", "solar": "Solar"}


def tag_name(meter: str, field: str) -> str:
    """'Main_kW', 'Comp1_PF' - short, so formula names stay readable (Energy_meters_Main_kW)."""
    return f"{SHORT[meter]}_{next(f[1] for f in FIELDS if f[0] == field)}"


# ====================================================================== the factory
class Factory:
    """One step per call: step(dt seconds of factory time, factory clock) -> {meter: {field: value}}."""

    def __init__(self, seed: int | None = None):
        self.r = random.Random(seed)
        self.kwh = {m[1]: self.r.uniform(2e4, 3e5) for m in METERS}
        self.kwh["main"] = self.r.uniform(1.5e6, 2.5e6)
        self.comp_loaded = {"comp1": True, "comp2": False}
        self.comp_t = {"comp1": 0.0, "comp2": 0.0}
        self.cloud = 1.0
        self.trip_until = 0.0                     # compressor 1 tripped until (factory time)
        self.capbank_off_until = 0.0
        self.peak_until = 0.0
        self.temp = 30.0

    def _n(self, sigma: float) -> float:
        return self.r.gauss(0, sigma)

    @staticmethod
    def _production(t: float) -> dict[str, float]:
        """Load fraction of each production feeder at factory time t."""
        d = _dt.datetime.fromtimestamp(t)
        h, wd = d.hour + d.minute / 60, d.weekday()     # 0 = Monday
        if wd == 6:                                     # Sunday: standby
            return {"line1": 0.08, "line2": 0.06, "molding": 0.1}
        day = 1.0 if 8 <= h < 17 else 0.0
        if 12 <= h < 13:
            day = 0.4                                   # lunch
        ramp = min(1.0, max(0.0, (h - 7.5) * 2)) if 7.5 <= h < 8 else 1.0
        night = 0.6 if (h >= 20 or h < 5) and wd < 6 else 0.0
        base = {"line1": max(day * ramp, night), "line2": day * ramp, "molding": max(day * ramp, night * 0.9)}
        return {k: max(0.06, v) for k, v in base.items()}

    def step(self, dt: float, t: float) -> dict[str, dict[str, float]]:
        r = self.r
        d = _dt.datetime.fromtimestamp(t)
        h, wd = d.hour + d.minute / 60 + d.second / 3600, d.weekday()
        self.temp = 29 + 5 * math.sin((h - 8) / 24 * 2 * math.pi) + self._n(0.1)      # outside air, peak ~14 h
        if r.random() < dt / 7200:                       # events (on average every 2 h of factory time)
            ev = r.choice(("trip", "capbank", "peak"))
            setattr(self, {"trip": "trip_until", "capbank": "capbank_off_until", "peak": "peak_until"}[ev],
                    t + r.uniform(300, 1200))
        prod = self._production(t)
        if t < self.peak_until and max(prod.values()) > 0.3:     # two lines start together (only in a shift)
            prod = {k: min(1.15, v + 0.35) for k, v in prod.items()}
        load = {k: v + self._n(0.02) for k, v in prod.items()}
        demand = sum(prod.values()) / 3                  # compressed air follows production
        for c, thr in (("comp1", 0.0), ("comp2", 0.65)):  # load / unload cycles; 2 assists at high demand
            self.comp_t[c] -= dt
            if self.comp_t[c] <= 0:
                self.comp_loaded[c] = not self.comp_loaded[c]
                busy = demand > thr
                self.comp_t[c] = r.uniform(40, 120) * (1.5 if self.comp_loaded[c] == busy else 0.5)
            run = demand > thr or c == "comp1"
            on = self.comp_loaded[c] and run and demand > 0.1
            load[c] = 0.0 if not run else (0.95 if on else 0.3) + self._n(0.02)
        if t < self.trip_until and demand > 0.3:
            load["comp1"] = 0.0
        office = 8 <= h < 18 and wd < 5
        load["chiller"] = max(0.1, 0.35 + 0.04 * (self.temp - 29) + 0.4 * demand) + self._n(0.02)
        load["tower"] = 0.2 + 0.7 * load["chiller"] + self._n(0.02)
        load["hvac"] = (0.5 + 0.05 * (self.temp - 29) if office else 0.08) + self._n(0.02)
        load["light"] = (1.0 if office or (7 <= h < 19 and wd < 6) else (0.6 if demand > 0.3 else 0.3)) + self._n(0.01)
        self.cloud = min(1.0, max(0.35, self.cloud + self._n(0.02 * math.sqrt(max(dt, 0.1)))))
        sun = max(0.0, math.sin((h - 6) / 12 * math.pi)) if 6 <= h <= 18 else 0.0
        load["solar"] = sun * self.cloud * 0.92
        out, sum_kw, sum_kvar = {}, 0.0, 0.0
        hz = 50 + self._n(0.02)
        v_grid = 230 + self._n(0.8)
        for unit, key, _name, _g, rated, pf, thd in METERS:
            if key == "main":
                continue
            kw = max(0.0, rated * max(0.0, load[key]))
            p = min(0.99, pf + self._n(0.005)) if kw > 0.5 else 0.6
            kva = kw / p if kw else 0.0
            kvar = math.sqrt(max(kva * kva - kw * kw, 0.0))
            v = v_grid + self._n(0.3)
            if key == "solar":
                sum_kw -= kw
            else:
                sum_kw += kw; sum_kvar += kvar
            self.kwh[key] += kw * dt / 3600
            out[key] = {"v": v, "i": kva * 1000 / (3 * v) if v else 0.0, "kw": kw, "kvar": kvar, "kva": kva,
                        "pf": p if kw else 0.0, "hz": hz, "kwh": self.kwh[key],
                        "thd": thd * (0.7 + 0.6 * r.random()) if kw else 0.0}
        comp = 0.15 if t < self.capbank_off_until else 0.62       # capacitor bank compensation
        kw_m = max(sum_kw, 0.0)
        kvar_m = sum_kvar * (1 - comp)
        kva_m = math.hypot(kw_m, kvar_m)
        self.kwh["main"] += kw_m * dt / 3600
        out["main"] = {"v": v_grid, "i": kva_m * 1000 / (3 * v_grid), "kw": kw_m, "kvar": kvar_m, "kva": kva_m,
                       "pf": kw_m / kva_m if kva_m else 1.0, "hz": hz, "kwh": self.kwh["main"],
                       "thd": 6.0 + self._n(0.3)}
        return {m[1]: out[m[1]] for m in METERS}


def registers(values: dict[str, float]) -> list[int]:
    """One meter's values -> its 18 holding registers (float32, ABCD)."""
    regs = [0] * REGS
    for key, _s, _u, _d, off, _desc in FIELDS:
        hi, lo = struct.unpack(">HH", struct.pack(">f", float(values.get(key, 0.0))))
        regs[off], regs[off + 1] = hi, lo
    return regs


# ====================================================================== Modbus TCP server
class Server:
    """The 11 meters on one Modbus TCP server (a thread). update(values) sets what the next read
    returns. pymodbus >= 3.12 (SimDevice: filled on each read), else the classic data blocks."""

    def __init__(self, host: str = "127.0.0.1", port: int = PORT):
        self.host, self.port = host, port
        self.regs = {m[0]: [0] * REGS for m in METERS}
        self._lock = threading.Lock()
        self._thread = None
        self._legacy = None                      # unit -> data block (old pymodbus)

    def update(self, values: dict[str, dict[str, float]]):
        with self._lock:
            for unit, key, *_ in METERS:
                self.regs[unit] = registers(values[key])
            if self._legacy:
                for unit, block in self._legacy.items():
                    block.setValues(1, self.regs[unit])

    def _context(self):
        try:
            from pymodbus.simulator import DataType, SimData, SimDevice

            def action(unit):
                async def fill(function_code, start_address, address, count, current_registers, set_values):
                    with self._lock:
                        current_registers[:REGS] = self.regs[unit]
                    return None
                return fill
            return [SimDevice(id=unit, simdata=[SimData(0, count=REGS, values=0, datatype=DataType.REGISTERS)],
                              action=action(unit)) for unit, *_ in METERS]
        except ImportError:
            from pymodbus.datastore import ModbusSequentialDataBlock, ModbusServerContext
            try:
                from pymodbus.datastore import ModbusSlaveContext as Ctx
            except ImportError:
                from pymodbus.datastore import ModbusDeviceContext as Ctx
            self._legacy = {unit: ModbusSequentialDataBlock(1, list(self.regs[unit])) for unit, *_ in METERS}
            ctx = {unit: Ctx(hr=b) for unit, b in self._legacy.items()}
            try:
                return ModbusServerContext(devices=ctx, single=False)
            except TypeError:
                return ModbusServerContext(slaves=ctx, single=False)

    def start(self):
        from pymodbus.server import StartTcpServer
        ctx = self._context()

        def run():
            asyncio.set_event_loop(asyncio.new_event_loop())
            StartTcpServer(context=ctx, address=(self.host, self.port))
        self._thread = threading.Thread(target=run, daemon=True, name="factory-energy-modbus")
        self._thread.start()

    def stop(self):
        try:
            from pymodbus.server import ServerStop
            ServerStop()
        except Exception:                           # not running / already stopped
            pass
        if self._thread is not None:
            self._thread.join(2.0)
            self._thread = None


# ====================================================================== MCX Adapter register map
AREA = {"Main": "Utilities/Incomer", "Production": "Production", "Utilities": "Utilities",
        "Building": "Building", "Solar": "Utilities/Solar"}


def register_map(prefix: str = "Bangkok/Energy") -> list[dict]:
    """Items for the Modbus source: address unit:area:offset:dtype:order, ISA-95 path, name, unit."""
    out = []
    for unit, key, mname, group, *_ in METERS:
        path = f"{prefix.strip('/')}/{AREA[group]}/{SHORT[key]}".strip("/")
        for f, suffix, u, _dec, off, desc in FIELDS:
            out.append({"address": f"{unit}:holding:{off}:float32:ABCD", "path": path,
                        "name": suffix.lower(), "unit": u, "description": f"{mname}: {desc}"})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m mcx_core.sim.factory_energy",
                                 description="Factory energy simulator: 11 power meters on a Modbus TCP server.")
    ap.add_argument("--host", default="127.0.0.1", help="listen address (default 127.0.0.1)")
    ap.add_argument("--port", type=int, default=PORT, help=f"Modbus TCP port (default {PORT})")
    ap.add_argument("--interval", type=float, default=1.0, help="seconds between updates (default 1)")
    ap.add_argument("--speed", type=float, default=1.0, help="factory clock speed: 60 = one hour per minute")
    ap.add_argument("--seed", type=int, default=None, help="random seed (repeatable runs)")
    ap.add_argument("--count", type=int, default=0, help="stop after N updates (0 = until Ctrl+C)")
    a = ap.parse_args(argv)
    fac, srv = Factory(a.seed), Server(a.host, a.port)
    srv.update(fac.step(0, time.time()))
    try:
        srv.start()
    except Exception as e:
        print(f"Cannot start the Modbus server on {a.host}:{a.port} - {e}", file=sys.stderr)
        return 1
    print(f"Factory energy: 11 meters on Modbus TCP {a.host}:{a.port} (units 1-11, float32 ABCD from address 0)"
          f"  - factory clock x{a.speed:g} - Ctrl+C to stop")
    sim_t, last, n = time.time(), time.time(), 0
    try:
        while not a.count or n < a.count:
            time.sleep(a.interval)
            now = time.time()
            sim_t += (now - last) * a.speed
            values = fac.step((now - last) * a.speed, sim_t)
            last = now
            srv.update(values)
            n += 1
            if n % 10 == 1:
                m = values["main"]
                print(f"{time.strftime('%H:%M:%S', time.localtime(sim_t))}  incomer {m['kw']:7.1f} kW  "
                      f"PF {m['pf']:.3f}  solar {values['solar']['kw']:6.1f} kW")
    except KeyboardInterrupt:
        pass
    finally:
        srv.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
