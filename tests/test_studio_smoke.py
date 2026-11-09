"""MCX Studio opens the demo, every document builds, light / dark switch, publish from a page."""
import pytest

pytest.importorskip("PySide6")


@pytest.fixture()
def win(tmp_path, monkeypatch):
    monkeypatch.setenv("MCX_DATA_DIR", str(tmp_path / "data"))
    from PySide6.QtCore import QCoreApplication, QSettings
    from PySide6.QtWidgets import QApplication
    QCoreApplication.setOrganizationName("SPEX-test"); QCoreApplication.setApplicationName("MCX Studio pytest")
    app = QApplication.instance() or QApplication([])
    QSettings().clear()
    QSettings().setValue("auto_start_server", False)
    from mcx_studio.main_window import AdapterWindow
    w = AdapterWindow()
    w.show(); app.processEvents()
    yield w
    w.dirty = False
    w.close()


def test_open_every_document(win):
    for key in ("overview", "tools", "profiles", "model", "safety", "endpoint", "audit", "licence", "alarms"):
        win.open_doc(key)
    for s in win.project.sources:
        for step in range(4):
            win.open_source(s.id, step)
    for mode in ("light", "dark"):
        win.set_theme(mode)
    assert win.doc_tabs.count() == 9 + len(win.project.sources)


def test_expose_and_publish(win):
    p = win.project
    page = win.open_source("smc_demo", 2)
    n = len(p.tools)
    for i in range(page.exp.topLevelItemCount()):
        it = page.exp.topLevelItem(i)
        if it.data(1, 256) == "ecm_production":
            from PySide6.QtCore import Qt
            it.setCheckState(0, Qt.CheckState.Checked)
    assert len(p.tools) == n + 1 and p.tool("get_ecm_production").state == "draft"
    win.publish(["get_ecm_production"])
    assert p.tool("get_ecm_production").state == "published"
    assert "get_ecm_production" in {t["name"] for t in win.rt.mcp_tools("eng")}


def test_properties_targets(win):
    for ref in (("project",), ("endpoint",), ("source", "smc_demo"), ("tool", "get_oee"), ("profile", "qe"),
                ("object", "smc_demo", "oee_assets")):
        win.select(ref)
        assert win.props.target is not None, ref
