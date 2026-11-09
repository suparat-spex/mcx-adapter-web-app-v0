"""Tag adapters against local servers: Modbus (factory energy simulator) and OPC UA (asyncua)."""
import socket
import time

import pytest

from mcx_core.config import Item, SourceCfg


def _port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
    return p


def test_modbus_power_meters():
    from mcx_core.adapters import make_adapter
    from mcx_core.sim.factory_energy import Factory, Server
    port = _port()
    fac, srv = Factory(1), Server("127.0.0.1", port)
    srv.update(fac.step(0, time.time()))
    srv.start()
    try:
        cfg = SourceCfg("pm", "Power meters", "modbus", settings={"host": "127.0.0.1", "port": str(port),
                                                                  "register_map": "factory_energy"}, isa95="Bangkok/Energy")
        a = make_adapter(cfg)
        items = a.discover().items
        assert len(items) == 99 and items[0].path == "Bangkok/Energy/Utilities/Incomer/Main"
        hz = next(i for i in items if i.path.endswith("/Line1") and i.name == "hz")
        for _ in range(20):
            if a.health().state == "online":
                break
            time.sleep(0.2)
        r = a.read([hz, Item("99:holding:0:float32")])
        assert r[0].quality == "good" and 49 < r[0].value < 51
        assert r[1].quality == "bad"                         # unit 99 does not exist
        a.close()
    finally:
        srv.stop()


def test_opcua_browse_and_read():
    try:
        from asyncua.sync import Server
    except Exception as e:                       # asyncua's server needs a working pyOpenSSL / cryptography pair
        pytest.skip(f"asyncua server not available here: {e}")
    from mcx_core.adapters import make_adapter
    port = _port()
    srv = Server()
    srv.set_endpoint(f"opc.tcp://127.0.0.1:{port}/mcx/")
    ns = srv.register_namespace("urn:mcx:test")
    line = srv.nodes.objects.add_object(ns, "Line2")
    press = line.add_object(ns, "Press1")
    press.add_variable(ns, "Temperature", 61.5)
    press.add_variable(ns, "Speed", 1450)
    srv.start()
    try:
        cfg = SourceCfg("ua", "GENESIS64", "opcua", settings={"endpoint": f"opc.tcp://127.0.0.1:{port}/mcx/"},
                        isa95="Bangkok/Assembly")
        a = make_adapter(cfg)
        assert a.health().state == "online"
        items = a.discover().items
        temp = next(i for i in items if i.name == "temperature")
        assert temp.path == "Bangkok/Assembly/Line2/Press1"
        assert a.read([temp])[0].value == 61.5
        a.close()
    finally:
        srv.stop()
