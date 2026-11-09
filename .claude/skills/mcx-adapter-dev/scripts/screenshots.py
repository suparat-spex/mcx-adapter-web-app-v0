import os, sys, time, json, datetime as dt
SP = sys.argv[1]
os.environ["MCX_DATA_DIR"] = SP + "/data"
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtCore import QCoreApplication, QSettings
from PySide6.QtWidgets import QApplication
QCoreApplication.setOrganizationName("SPEX-test"); QCoreApplication.setApplicationName("MCX Adapter shots")
app = QApplication(sys.argv)
QSettings().clear()
from mcx_studio.main_window import AdapterWindow
w = AdapterWindow(); w.resize(2000, 1125); w.show()
def pump(s=0.6):
    t = time.time()
    while time.time() - t < s:
        app.processEvents(); time.sleep(0.02)
pump(2.5)
# some traffic: through the real HTTP endpoint with the demo keys
keys = dict(l.split(": ") for l in open(SP + "/data/Demo/demo_keys.txt").read().splitlines()[1:])
import urllib.request
def mcp(profile, key, method, params=None, sid=None):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}).encode()
    h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", "Authorization": "Bearer " + key}
    if sid: h["Mcp-Session-Id"] = sid
    r = urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:8811/mcp/{profile}", body, h), timeout=10)
    return r.headers.get("Mcp-Session-Id"), json.loads(r.read() or b"null")
d1 = dt.date.today(); d0 = d1 - dt.timedelta(days=6)
for prof, name, calls in (("eng", "ContextForge", [("get_oee", {"line": "L2", "date_from": str(d0), "date_to": str(d1)}),
                                                   ("get_energy_kwh", {"date_from": str(d0), "date_to": str(d1)}),
                                                   ("get_downtime_pareto", {"date_from": str(d0), "date_to": str(d1)})]),
                          ("qe", "Copilot QE", [("get_reject_pareto", {"date_from": str(d0), "date_to": str(d1)}),
                                                ("query_history", {"path": "x", "from": "2026-01-01", "to": "2026-06-01"}),
                                                ("read_tag", {"path": "Bangkok/Assembly/L2/Press1/temperature"})])):
    sid, _ = mcp(prof, keys[prof], "initialize", {"protocolVersion": "2025-06-18", "clientInfo": {"name": name}})
    for t, a in calls:
        mcp(prof, keys[prof], "tools/call", {"name": t, "arguments": a}, sid)
pump(1.5)
def shot(name):
    pump(0.8); w.grab().save(f"{SP}/shots/{name}.png")
for mode in ("light", "dark"):
    w.set_theme(mode); pump(0.5)
    w.open_doc("overview"); shot(f"{mode}_overview")
    sp = w.open_source("smc_demo", 2, "get_oee"); pump(0.3)
    sp.run_test(); pump(1.5); shot(f"{mode}_source_expose")
    pr = w.open_doc("profiles"); pr.set_profile("qe"); w.select(("profile", "qe")); shot(f"{mode}_profiles")
    if mode == "light":
        for k in ("tools", "model", "safety", "endpoint", "audit", "alarms", "licence"):
            w.open_doc(k); shot(f"{mode}_{k}")
        w.open_source("smc_demo", 0); shot("light_source_connect")
        w.open_source("plant_sim", 2); shot("light_sim_expose")
w.resize(1280, 800); pump(0.8); w.open_doc("overview"); shot("laptop_overview")
w.dirty = False
w.close()
print("done")
