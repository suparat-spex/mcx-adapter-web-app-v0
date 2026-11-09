"""
demo.py - the "Plant adapter" demo: a mock-up plant to try MCX Adapter without any plant. No Qt.

  smc_demo.db          SQLite stand-in for the SQL Server `smc_demo` database: OEE per shift, downtime,
                       rejects, energy meters, kWh per unit, carbon, assets, operator comments (personal
                       data - held back) and a login audit table (blocked by rule)
  shift_reports.xlsx   Excel shift log (supervisor / notes are personal data)
  Plant adapter.mcx    the project: 4 sources (SQL, simulator, Modbus power meters - start
                       `python -m mcx_core.sim.factory_energy` -, Excel), curated tools, profiles eng / qe /
                       mgr, an API key per profile (secrets in demo_keys.txt - a demo only)

    python -m mcx_core demo [folder]
"""
from __future__ import annotations

import datetime as dt
import math
import random
import sqlite3
from pathlib import Path

from mcx_core.adapters import make_adapter
from mcx_core.config import Limits, Measure, Profile, Project, SourceCfg, ToolDef, ToolParam
from mcx_core.paths import demo_dir

LINES = {"L1": ["P11", "P12"], "L2": ["P21", "P22"], "L3": ["P31", "P32"], "L4": ["P41", "P42"]}
SHIFTS = {"A": "06:00", "B": "14:00", "C": "22:00"}
REASONS = ["Changeover", "Material shortage", "Die change", "Jam", "Quality check", "Maintenance", "Waiting operator"]
DEFECTS = ["Burr", "Short shot", "Scratch", "Dimension", "Crack", "Colour"]
METERS = [("MDB-1", "Assembly"), ("MDB-2", "Molding"), ("COMP-1", "Utilities"), ("CHILLER", "Utilities"),
          ("OFFICE", "Building")]
DAYS = 75


def build_database(path: Path, seed: int = 7) -> Path:
    rnd = random.Random(seed)
    if path.exists():
        path.unlink()
    db = sqlite3.connect(path)
    db.executescript("""
    CREATE TABLE oee_shift_raw (date TEXT, line TEXT, machine TEXT, shift TEXT, planned_min INTEGER,
        run_min REAL, ideal_rate REAL, total_count INTEGER, good_count INTEGER);
    CREATE VIEW oee_shift_summary AS SELECT date, line, machine, shift,
        run_min / planned_min AS availability,
        total_count / (run_min * ideal_rate) AS performance,
        CAST(good_count AS REAL) / total_count AS quality,
        (run_min / planned_min) * (total_count / (run_min * ideal_rate)) * (CAST(good_count AS REAL) / total_count) AS oee,
        good_count, total_count - good_count AS reject_count, planned_min
        FROM oee_shift_raw;
    CREATE TABLE oee_downtime_events (date TEXT, line TEXT, machine TEXT, shift TEXT, reason TEXT, minutes REAL);
    CREATE TABLE oee_reject_events (date TEXT, line TEXT, machine TEXT, shift TEXT, defect TEXT, count INTEGER);
    CREATE TABLE ecm_meter_readings (date TEXT, meter TEXT, area TEXT, kwh REAL, peak_kw REAL);
    CREATE TABLE ecm_production (date TEXT, line TEXT, kwh REAL, units INTEGER);
    CREATE VIEW ecm_kwh_per_unit AS SELECT date, line, kwh, units, kwh / units AS kwh_per_unit FROM ecm_production;
    CREATE TABLE ecm_carbon_daily (date TEXT, area TEXT, kwh REAL, kg_co2e REAL);
    CREATE TABLE oee_assets (site TEXT, area TEXT, line TEXT, machine TEXT, model TEXT, installed TEXT, ideal_rate REAL);
    CREATE TABLE oee_operator_comments (date TEXT, line TEXT, shift TEXT, operator_name TEXT, comment TEXT);
    CREATE TABLE sys_login_audit (time TEXT, user_name TEXT, ip_address TEXT, ok INTEGER);
    """)
    start = dt.date.today() - dt.timedelta(days=DAYS - 1)
    base = {"L1": 0.80, "L2": 0.70, "L3": 0.76, "L4": 0.83}
    for d in range(DAYS):
        day = (start + dt.timedelta(days=d)).isoformat()
        weekend = (start + dt.timedelta(days=d)).weekday() == 6
        for line, machines in LINES.items():
            line_units = 0
            for m in machines:
                rate = 2.0 if line != "L4" else 2.6
                for sh in SHIFTS:
                    planned = 0 if weekend else 450
                    if not planned:
                        continue
                    avail = min(0.98, max(0.5, base[line] + 0.08 + rnd.gauss(0, 0.05) - (0.06 if sh == "A" else 0)))
                    run = planned * avail
                    perf = min(0.99, max(0.6, 0.9 + rnd.gauss(0, 0.04)))
                    total = int(run * rate * perf)
                    qual = min(0.995, max(0.85, 0.97 + rnd.gauss(0, 0.012) - (0.02 if sh == "C" else 0)))
                    good = int(total * qual)
                    line_units += good
                    db.execute("INSERT INTO oee_shift_raw VALUES (?,?,?,?,?,?,?,?,?)",
                               (day, line, m, sh, planned, round(run, 1), rate, total, good))
                    lost = planned - run
                    for _ in range(rnd.randint(1, 4)):
                        if lost <= 1:
                            break
                        mins = round(min(lost, rnd.uniform(5, 60)), 1)
                        lost -= mins
                        db.execute("INSERT INTO oee_downtime_events VALUES (?,?,?,?,?,?)",
                                   (day, line, m, sh, rnd.choices(REASONS, [5, 3, 2, 3, 2, 2, 1])[0], mins))
                    rej = total - good
                    for defect in rnd.sample(DEFECTS, 3):
                        part = rnd.randint(0, rej)
                        rej -= part
                        if part:
                            db.execute("INSERT INTO oee_reject_events VALUES (?,?,?,?,?,?)", (day, line, m, sh, defect, part))
            if line_units:
                kwh = line_units * (0.42 + 0.05 * math.sin(d / 9) + rnd.gauss(0, 0.02)) * (1.2 if line == "L2" else 1)
                db.execute("INSERT INTO ecm_production VALUES (?,?,?,?)", (day, line, round(kwh, 1), line_units))
        for meter, area in METERS:
            k = {"MDB-1": 5200, "MDB-2": 6100, "COMP-1": 1900, "CHILLER": 2300, "OFFICE": 650}[meter]
            kwh = k * (0.45 if weekend else 1) * (1 + rnd.gauss(0, 0.05))
            db.execute("INSERT INTO ecm_meter_readings VALUES (?,?,?,?,?)",
                       (day, meter, area, round(kwh, 1), round(kwh / 18 * (1.3 + rnd.random() * 0.2), 1)))
        for area in ("Assembly", "Molding", "Utilities", "Building"):
            kwh = sum(r[0] for r in db.execute("SELECT kwh FROM ecm_meter_readings WHERE date=? AND area=?", (day, area)))
            db.execute("INSERT INTO ecm_carbon_daily VALUES (?,?,?,?)", (day, area, round(kwh, 1), round(kwh * 0.4999, 1)))
        for sh in SHIFTS:
            db.execute("INSERT INTO oee_operator_comments VALUES (?,?,?,?,?)",
                       (day, rnd.choice(list(LINES)), sh, rnd.choice(["Somchai P.", "Anong K.", "Wichai T."]),
                        rnd.choice(["Die worn, asked maintenance", "Material late from store", "All good",
                                    "Sensor on P22 flickers"])))
        db.execute("INSERT INTO sys_login_audit VALUES (?,?,?,?)", (day + "T08:00:00", "admin", "10.0.0.5", 1))
    for line, machines in LINES.items():
        for m in machines:
            db.execute("INSERT INTO oee_assets VALUES (?,?,?,?,?,?,?)",
                       ("Bangkok", "Assembly", line, m, rnd.choice(["Amada HG-1303", "Komatsu E2W-200"]),
                        f"20{rnd.randint(12, 23)}-0{rnd.randint(1, 9)}-15", 2.6 if line == "L4" else 2.0))
    db.commit()
    db.close()
    return path


def build_excel(path: Path, seed: int = 11) -> Path | None:
    try:
        import openpyxl
    except ImportError:
        return None
    rnd = random.Random(seed)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Shift log"
    ws.append(["date", "line", "shift", "supervisor", "output", "scrap", "notes"])
    start = dt.date.today() - dt.timedelta(days=30)
    for d in range(30):
        for line in LINES:
            for sh in SHIFTS:
                ws.append([(start + dt.timedelta(days=d)).isoformat(), line, sh, rnd.choice(["K. Suda", "P. Arun"]),
                           rnd.randint(1500, 2200), rnd.randint(10, 80), ""])
    ws2 = wb.create_sheet("Targets")
    ws2.append(["line", "oee_target", "kwh_per_unit_target"])
    for line in LINES:
        ws2.append([line, 0.80, 0.45])
    wb.save(path)
    return path


def _P(name, type_="string", desc="", required=False, column="", op="=", values=None, default=None, group=None):
    return ToolParam(name, type_, desc, required, column, op, values or [], default, group or {})


def _dates(col="date"):
    return [_P("date_from", "date", "First day, plant time (YYYY-MM-DD)", True, col, ">="),
            _P("date_to", "date", "Last day, plant time (YYYY-MM-DD); max 31 days after date_from", True, col, "<=")]


LINE = _P("line", "string", "L1 ... L4 (empty = all lines)", column="line", values=list(LINES))


def demo_tools() -> list[ToolDef]:
    T = ToolDef
    return [
        T("get_oee", title="OEE by shift / day / machine",
          description="OEE, availability, performance and quality for a line or machine, by shift or day. "
                      "Use for 'how did line L2 do' questions. Not for live values (use read_tag).",
          source="smc_demo", object="oee_shift_summary", risk="R1", date_column="date", isa95="Bangkok/Assembly",
          params=[LINE, *_dates(), _P("group_by", "enum", "Group by", values=["shift", "day", "machine", "line"],
                                       default="shift", group={"shift": ["shift"], "day": ["date"],
                                                               "machine": ["line", "machine"], "line": ["line"]})],
          measures=[Measure("oee", "avg", "ratio"), Measure("availability", "avg", "ratio"),
                    Measure("performance", "avg", "ratio"), Measure("quality", "avg", "ratio"),
                    Measure("good_count", "sum", "pcs")], profiles=["eng", "qe", "mgr"]),
        T("get_downtime_pareto", title="Downtime Pareto",
          description="Downtime minutes and stop counts by reason (or machine), largest first - the Pareto "
                      "of what stops a line. Not for OEE totals (use get_oee).",
          source="smc_demo", object="oee_downtime_events", risk="R1", date_column="date", isa95="Bangkok/Assembly",
          params=[LINE, *_dates(), _P("group_by", "enum", "Group by", values=["reason", "machine"], default="reason",
                                       group={"reason": ["reason"], "machine": ["line", "machine"]})],
          measures=[Measure("minutes", "sum", "min"), Measure("minutes", "count", "", "stops")],
          order_by="-minutes", profiles=["eng", "qe"]),
        T("get_reject_pareto", title="Reject Pareto",
          description="Rejected parts by defect (or machine), largest first. For quality questions.",
          source="smc_demo", object="oee_reject_events", risk="R1", date_column="date", isa95="Bangkok/Assembly",
          params=[LINE, *_dates(), _P("group_by", "enum", "Group by", values=["defect", "machine"], default="defect",
                                       group={"defect": ["defect"], "machine": ["line", "machine"]})],
          measures=[Measure("count", "sum", "pcs")], order_by="-count", profiles=["qe"]),
        T("get_energy_kwh", title="Energy by meter / area / day",
          description="Energy (kWh) and peak demand (kW) per meter, area or day from the energy meters.",
          source="smc_demo", object="ecm_meter_readings", risk="R1", date_column="date", isa95="Bangkok",
          params=[_P("area", "string", "Assembly, Molding, Utilities, Building (empty = all)", column="area",
                     values=["Assembly", "Molding", "Utilities", "Building"]), *_dates(),
                  _P("group_by", "enum", "Group by", values=["day", "meter", "area"], default="area",
                     group={"day": ["date"], "meter": ["meter"], "area": ["area"]})],
          measures=[Measure("kwh", "sum", "kWh"), Measure("peak_kw", "max", "kW")], profiles=["eng", "mgr"]),
        T("get_kwh_per_unit", title="kWh per unit",
          description="Specific energy: kWh per good unit by line or day (lower is better).",
          source="smc_demo", object="ecm_kwh_per_unit", risk="R1", date_column="date", isa95="Bangkok/Assembly",
          params=[LINE, *_dates(), _P("group_by", "enum", "Group by", values=["line", "day"], default="line",
                                       group={"line": ["line"], "day": ["date"]})],
          measures=[Measure("kwh", "sum", "kWh"), Measure("units", "sum", "pcs"),
                    Measure("kwh_per_unit", "avg", "kWh/unit")], profiles=["eng", "qe", "mgr"]),
        T("get_carbon_summary", title="Carbon summary",
          description="Electricity and scope-2 emissions (kg CO2e, grid factor 0.4999) by area or day.",
          source="smc_demo", object="ecm_carbon_daily", risk="R1", date_column="date", isa95="Bangkok",
          params=[*_dates(), _P("group_by", "enum", "Group by", values=["area", "day"], default="area",
                                group={"area": ["area"], "day": ["date"]})],
          measures=[Measure("kwh", "sum", "kWh"), Measure("kg_co2e", "sum", "kg CO2e")], profiles=["mgr"]),
        T("list_plant_assets", title="Plant assets", risk="R0",
          description="Machines of the assembly area with model, install date and ideal rate (parts / min).",
          source="smc_demo", object="oee_assets", isa95="Bangkok/Assembly",
          params=[LINE], columns=["site", "area", "line", "machine", "model", "installed", "ideal_rate"],
          order_by="machine", profiles=["eng", "qe", "mgr"]),
        T("get_operator_comments", title="Operator comments", risk="R2",
          description="Operator shift comments. Contains personal data: held back until an admin decides.",
          source="smc_demo", object="oee_operator_comments", date_column="date",
          params=[LINE, *_dates()], columns=["date", "line", "shift", "comment"], order_by="-date", profiles=[]),
    ]


def build_project(folder: Path) -> tuple[Project, dict[str, str]]:
    db = build_database(folder / "smc_demo.db")
    xl = build_excel(folder / "shift_reports.xlsx")
    p = Project.new("Plant adapter")
    p.site, p.timezone = "Bangkok", "Asia/Bangkok"
    p.description = "Demo: SMC Bangkok plant - OEE, energy, live machines"
    p.licence.set_edition("Standard")
    p.licence.holder, p.licence.expires = "SPEX demo", (dt.date.today() + dt.timedelta(days=365)).isoformat()
    p.limits = Limits()
    p.profiles = [Profile("eng", "Engineering", "Process and maintenance engineers", "R3", ""),
                  Profile("qe", "Quality team", "Quality engineers", "R3", "Bangkok"),
                  Profile("mgr", "Management", "Plant managers - KPIs only", "R1", "")]
    p.tools = []
    p.ensure_generic_tools()
    for t in p.tools:                      # generic: managers get the model, not raw tags
        t.profiles = ["eng", "qe"] + (["mgr"] if t.risk == "R0" else [])
    p.sources = [
        SourceCfg("smc_demo", "smc_demo", "sql", settings={"dialect": "sqlite", "database": str(db), "user": "mcp_reader"},
                  isa95="Bangkok/Assembly", description="Mock-up of the SQL Server smc_demo database"),
        SourceCfg("plant_sim", "Plant simulator", "sim", settings={"site": "Bangkok", "areas": "Assembly: L1, L2\nMolding: M1",
                                                                   "machines": 2, "poll_s": 10},
                  isa95="Bangkok", description="Simulated presses and molds (live values and history)"),
        SourceCfg("power_meters", "Power meters", "modbus",
                  settings={"mode": "tcp", "host": "127.0.0.1", "port": "5020", "unit": 1,
                            "register_map": "factory_energy", "poll_s": 10},
                  isa95="Bangkok/Energy", description="python -m mcx_core.sim.factory_energy (11 meters)"),
    ]
    if xl:
        p.sources.append(SourceCfg("shift_reports", "Shift reports", "files", settings={"path": str(xl)},
                                   isa95="Bangkok/Assembly", description="Excel shift log"))
    # discover the offline sources now (Modbus discovery reads the register map, no server needed)
    from mcx_core.runtime import Runtime
    rt = Runtime(p, None)
    for s in p.sources:
        try:
            rt.apply_discovery(s.id, make_adapter(s, p.limits).discover())
        except Exception:
            pass
    src = p.source("smc_demo")
    p.tools += demo_tools()
    used = {t.object for t in p.tools if t.source == "smc_demo"}
    for o in src.objects:                   # allow-list: the objects behind the curated tools
        o.exposed = o.name in used and not o.blocked
    p.publish(errors=[])                    # get_operator_comments stays held back (personal data)
    live = {t.object for t in p.tools if t.source == "smc_demo" and t.published is not None}
    for o in src.objects:
        o.exposed = o.name in live          # a held-back tool's object stays off the allow-list
    keys = {}
    for prof, client in (("eng", "ContextForge"), ("qe", "Copilot Studio"), ("mgr", "Docker MCP Gateway")):
        k, secret = p.new_api_key(f"{prof}-key", prof, client)
        keys[prof] = secret
    return p, keys


def create(folder: str | Path | None = None) -> tuple[Path, dict[str, str]]:
    folder = Path(folder or demo_dir())
    folder.mkdir(parents=True, exist_ok=True)
    p, keys = build_project(folder)
    path = folder / "Plant adapter.mcx"
    p.save(path)
    (folder / "demo_keys.txt").write_text(
        "MCX Adapter demo API keys - shown once, only hashes are in the project. DEMO ONLY.\n" +
        "".join(f"{k}: {v}\n" for k, v in keys.items()), encoding="utf-8")
    return path, keys
