"""
main_window.py - MCX Studio: the setup and monitoring tool of MCX Adapter.

Layout (the Spex shell, same as NX Logger):
  Left     Project dock - tree: Overview, Sources (status dots), Plant model, Tools, Tool profiles,
           MCP endpoint, Safety & limits, Audit log, Licence
  Centre   closable document tabs: Overview, one per source (Connect > Discover > Expose > Review &
           publish), Tools, Tool profiles, Plant model, Safety & limits, MCP endpoint, Audit log,
           Licence, Alarms
  Right    Properties dock (whatever is selected) + Runtime dock (sources / server chips, KPIs)
  Bottom   Log dock; status bar: server state · alarms · clients / calls / audit

The engine runs in-process (mcx_core.runtime.Runtime + mcx_core.mcp.McpServer): what Studio shows is
what MCP clients get. Source I/O runs in threads; the GUI polls `runtime.rev` once a second.
"""
from __future__ import annotations

import getpass
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence
from PySide6.QtWidgets import (QApplication, QDockWidget, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPlainTextEdit, QSizePolicy, QTabWidget, QToolButton,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from mcx_core import APP_NAME, __version__, paths
from mcx_core.adapters import ADAPTERS, Health, type_label
from mcx_core.config import PROJECT_EXT, RISKS, Profile, Project, SourceCfg
from mcx_core.mcp import McpServer, Session
from mcx_core.runtime import Runtime
from mcx_core.safety import RISK_INFO, annotations
from mcx_core.tools import new_curated
from mcx_studio.dialogs.common import (AddSourceDialog, ParamsDialog, TestCallDialog, ask, confirm, show_secret)
from mcx_studio.icons import check_icon_file, dot, grip_icon_file
from mcx_studio.theme import ui_kit as ui
from mcx_studio.theme.app_style import DENSE_QSS, EXTRA_QSS, blend, fusion_palette, fusion_qss
from mcx_studio.theme.ui_kit import AppWindow, Card, Chip, T, fixed_width, make_icon, set_prop
from mcx_studio.widgets.common import STATE_TOKEN, fmt_int
from mcx_studio.widgets.overview import OverviewPage
from mcx_studio.widgets.pages import (AlarmsPage, AuditPage, EndpointPage, LicencePage, ModelPage, SafetyPage,
                                      ToolsPage)
from mcx_studio.widgets.profiles_page import ProfilesPage
from mcx_studio.widgets.property_grid import PropertiesPanel, PropTarget
from mcx_studio.widgets.source_page import SourcePage

log = logging.getLogger(__name__)
CHIP_W = 150
DOCS = {   # key: (title, icon)
    "overview": ("Overview", "house"), "tools": ("Tools", "wrench"), "profiles": ("Tool profiles", "list"),
    "model": ("Plant model", "layers"), "safety": ("Safety & limits", "shield"),
    "endpoint": ("MCP endpoint", "arrow-left-right"), "audit": ("Audit log", "file-text"),
    "licence": ("Licence", "key-round"), "alarms": ("Alarms", "bell"),
}


class _LogBridge(QObject, logging.Handler):
    line = Signal(str)

    def __init__(self):
        QObject.__init__(self)
        logging.Handler.__init__(self, logging.INFO)
        self.setFormatter(logging.Formatter("%(asctime)s  %(levelname)s  %(name)s: %(message)s", "%H:%M:%S"))

    def emit(self, record):
        try:
            self.line.emit(self.format(record))
        except RuntimeError:
            pass


class AdapterWindow(AppWindow):
    TITLE = APP_NAME
    LEFT_TITLE, RIGHT_TITLE = "Project", "Properties"
    LEFT_W, RIGHT_W = 300, 360

    def __init__(self):
        self.user = getpass.getuser()
        self.project = Project.new()
        self.project_path: str | None = None
        self.rt = Runtime(self.project, None, self.user)
        self.server: McpServer | None = None
        self.pages: dict[str, QWidget] = {}
        self.dirty = False
        self._seen_rev = -1
        self._stale: set[str] = set()
        self._sim_proc = None
        self._dense = self._screen_is_small()
        QApplication.instance().setAttribute(Qt.ApplicationAttribute.AA_DontShowIconsInMenus, True)
        super().__init__()
        self.left_dock.setMinimumWidth(220)
        self.left_dock.widget().setMinimumWidth(210)
        self.right_dock.setMinimumWidth(280)
        self.setMinimumSize(1000, 640)
        self.banners.lay.setContentsMargins(8, 0, 8, 0)
        self.theme_listeners += [self._refresh_chrome, self._rebuild_tree, self._refresh_current, self.props.refresh_icons]
        self.doc_tabs.currentChanged.connect(self._doc_changed)
        self._build_status()
        self._fit_to_screen()
        self.bridge = _LogBridge()
        self.bridge.line.connect(lambda s: self.log.appendPlainText(s))
        logging.getLogger().addHandler(self.bridge)
        self._refresh_chrome()

        last = self.qs.value("last_project", "")
        try:
            if last and os.path.isfile(last):
                self.load(last)
            else:
                self.open_demo(first_run=True)
        except Exception as e:
            log.error("could not open %s: %s", last, e)
            self._set_project(Project.new(), None)
        self.timer = QTimer(self, interval=1000, timeout=self._tick)
        self.timer.start()

    # ================================================================ theme
    def apply_theme(self):
        t = ui.THEMES[ui.resolve_theme(self.theme_mode)]
        qss = (EXTRA_QSS + (DENSE_QSS if self._dense else "")) \
            .replace("@sel@", blend(t["accent"], t["panel2"], 0.28)) \
            .replace("@check@", check_icon_file(t["accent_text"])) \
            .replace("@grip_v@", grip_icon_file(t["muted"], True)) \
            .replace("@grip_h@", grip_icon_file(t["muted"], False))
        for k in ("ok", "info", "warn", "bad"):
            qss = qss.replace(f"@{k}Soft@", blend(t[k], t["panel"], 0.16 if t["mode"] == "dark" else 0.12))
        qss += 'QLabel#cardValue[level="accent"] { color: @accent@; }\n'
        T.clear(); T.update(t)
        app = QApplication.instance()
        app.setStyle("Fusion")
        app.setPalette(fusion_palette(t))
        raw = fusion_qss(ui.QSS + qss)
        for k, v in t.items():
            raw = raw.replace(f"@{k}@", str(v))
        app.setStyleSheet(raw)
        self.refresh_icons()
        for fn in self.theme_listeners:
            fn()

    def _refresh_chrome(self):
        self.brand.setText(f"<span style='color:{T['accent']}'>MCX</span> Adapter")
        dark = T["mode"] == "dark"
        self.act_theme.setText("Light" if dark else "Dark")
        for i in range(self.doc_tabs.count()):
            key = self.doc_tabs.widget(i).property("docKey")
            self.doc_tabs.setTabIcon(i, make_icon(self._doc_icon(key), T["muted"]))

    def _toggle_theme(self):
        self.set_theme("light" if T["mode"] == "dark" else "dark")

    # ================================================================ toolbar / menus
    def build_extra_menus(self, mb):
        self.act_add = self.act("Add source", self.add_source, "Ctrl+Shift+A", "plus")
        self.act_disc = self.act("Discover", self.discover_current, "Ctrl+D", "scan-search")
        self.act_test = self.act("Test call", self.test_call_dialog, "Ctrl+T", "play")
        self.act_pub = self.act("Publish", lambda: self.publish(None), "Ctrl+P", "upload")
        self.act_prof = self.act("Profiles", lambda: self.open_doc("profiles"), "Ctrl+3", "list")
        self.act_conn = self.act("Connect client", lambda: self.open_doc("profiles"), "Ctrl+Shift+C", "arrow-left-right")
        self.act_lim = self.act("Limits", lambda: self.open_doc("safety"), "Ctrl+4", "shield")
        self.act_audit = self.act("Audit log", lambda: self.open_doc("audit"), "Ctrl+5", "file-text")
        self.act_alarms = self.act("Alarms", lambda: self.open_doc("alarms"), "Ctrl+6", None)
        self.reg_icon(self.act_alarms, "bell", "warn")
        self.act_theme = self.act("Dark", self._toggle_theme, None, "contrast")
        f = self.file_menu
        first = f.actions()[0]
        for a in (self.act("New project", self.new_project, "Ctrl+N"), self.act("Open project...", self.open_project, "Ctrl+O"),
                  self.act("Open demo project", self.open_demo), None,
                  self.act("Save", self.save, "Ctrl+S"), self.act("Save as...", self.save_as, "Ctrl+Shift+S"), None,
                  self.act("Open project folder", self.open_folder), None):
            f.insertSeparator(first) if a is None else f.insertAction(first, a)
        src = QMenu_("&Source", self, mb, self.view_menu)
        for a in (self.act_add, self.act_disc, self.act("Test connection", self._test_conn_current),
                  None, self.act("Delete source...", lambda: self._cur_source() and self.delete_source(self._cur_source()))):
            src.addSeparator() if a is None else src.addAction(a)
        tools = QMenu_("&Tools", self, mb, self.view_menu)
        for a in (self.act_test, self.act_pub, None, self.act("All tools", lambda: self.open_doc("tools"), "Ctrl+2"),
                  self.act("New curated tool...", self.new_curated_tool), None, self.act_prof,
                  self.act("New profile...", self.new_profile), None, self.act_lim, self.act_audit,
                  self.act("Verify audit chain", self.verify_audit), None,
                  self.act("MCP endpoint", lambda: self.open_doc("endpoint")),
                  self.act("Start / stop MCP server", self.toggle_server)):
            tools.addSeparator() if a is None else tools.addAction(a)
        self.addAction(self.act("Close tab", self._close_current_tab, "Ctrl+W"))

    def build_toolbar_items(self, tb):
        self.brand = QLabel(); self.brand.setObjectName("brand")
        self.brand.setToolTip(f"{APP_NAME} {__version__} - plant data as safe, standard MCP tools")
        tb.addWidget(self.brand)
        self._icon_only = []
        for text, slot, key, icon in (("Overview", lambda: self.open_doc("overview"), "Ctrl+0", "house"),
                                      ("New project", self.new_project, "Ctrl+N", "file-plus"),
                                      ("Open project...", self.open_project, "Ctrl+O", "folder-open"),
                                      ("Save project", self.save, "Ctrl+S", "save")):
            a = self.act(text, slot, None, icon)
            a.setToolTip(f"{text}  ({key})")
            tb.addAction(a)
            tb.widgetForAction(a).setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            self._icon_only.append(a)
        self.addAction(self.act("Overview", lambda: self.open_doc("overview"), "Ctrl+0"))
        tb.addSeparator()
        for a in (self.act_add, self.act_disc, self.act_test, self.act_pub, None, self.act_prof, self.act_conn,
                  self.act_lim, None, self.act_audit, self.act_alarms):
            tb.addSeparator() if a is None else tb.addAction(a)
        self.add_spacer(tb)
        tb.addAction(self.act_theme)
        self.act_left = self.left_dock.toggleViewAction(); self.act_left.setText("Project panel")
        self.reg_icon(self.act_left, "panel-left")
        self.act_logp = self.log_dock.toggleViewAction(); self.act_logp.setText("Log panel")
        self.reg_icon(self.act_logp, "panel-bottom")
        self.act_right = self.act("Properties panel", self._toggle_right, "Ctrl+Shift+P", "panel-right")
        for a in (self.act_left, self.act_logp, self.act_right):
            tb.addAction(a)
            tb.widgetForAction(a).setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        tb.setIconSize(QSize(16, 16) if self._dense else QSize(18, 18))
        fixed_width(tb.widgetForAction(self.act_pub), "Publish", "Publish (99)")
        fixed_width(tb.widgetForAction(self.act_alarms), "Alarms", "Alarms (99)")
        fixed_width(tb.widgetForAction(self.act_theme), "Dark", "Light")
        for a in tb.actions():
            sc = a.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
            if a.toolTip() == a.text() or not a.toolTip():
                a.setToolTip(f"{a.text()}  ({sc})" if sc else a.text())

    def _toggle_right(self):
        show = self.right_dock.isHidden()
        for d in (self.right_dock, self.runtime_dock):
            d.setVisible(show)

    # ================================================================ left: project tree
    def build_left(self):
        bar = QFrame(); bar.setObjectName("navBar")
        bl = QHBoxLayout(bar); bl.setContentsMargins(4, 2, 4, 2); bl.setSpacing(0)
        for tip, icon, slot in (("Add source", "database", self.add_source),
                                ("New curated tool", "wrench", self.new_curated_tool),
                                ("New profile", "list", self.new_profile), (None, None, None),
                                ("Expand / collapse", "expand", self._toggle_expand),
                                ("Close panel", "x", lambda: self.left_dock.hide())):
            if tip is None:
                bl.addStretch(1); continue
            b = QToolButton(); b.setToolTip(tip); b.setAutoRaise(True)
            self.reg_icon(b, icon); b.clicked.connect(slot); bl.addWidget(b)
        self.nav_filter = QLineEdit(); self.nav_filter.setPlaceholderText("Filter"); self.nav_filter.setClearButtonEnabled(True)
        fa = self.nav_filter.addAction(make_icon("search", T["muted"]), QLineEdit.ActionPosition.LeadingPosition)
        self.reg_icon(fa, "search", "muted")
        self.nav_filter.textChanged.connect(self._filter_tree)
        self.tree = QTreeWidget(); self.tree.setObjectName("navTree"); self.tree.setHeaderHidden(True)
        self.tree.setIndentation(16)
        self.tree.itemClicked.connect(lambda it, _c: self._nav(it))
        self.tree.itemActivated.connect(lambda it, _c: self._nav(it))
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(6)
        v.addWidget(bar)
        fl = QHBoxLayout(); fl.setContentsMargins(6, 0, 6, 0); fl.addWidget(self.nav_filter); v.addLayout(fl)
        v.addWidget(self.tree, 1)
        return w

    def _rebuild_tree(self, *_):
        if not hasattr(self, "tree"):
            return
        p, rt = self.project, self.rt
        open_ = self._expanded()
        sel = self.tree.currentItem().data(0, 256) if self.tree.currentItem() else None
        self.tree.clear()

        def node(parent, text, icon, data, tip=""):
            it = QTreeWidgetItem([text]); it.setData(0, 256, data)
            if isinstance(icon, str):
                it.setIcon(0, make_icon(icon, T["text"]))
            elif icon is not None:
                it.setIcon(0, icon)
            if tip:
                it.setToolTip(0, tip)
            (parent.addChild(it) if parent else self.tree.addTopLevelItem(it))
            return it
        name = Path(self.project_path).name if self.project_path else f"{p.name}{PROJECT_EXT} (not saved)"
        root = node(None, name, "folder", ("project",))
        node(root, "Overview", "house", ("doc", "overview"))
        on, n = rt.sources_online()
        srcs = node(root, f"Sources  · {on} of {n} online", "plug", ("doc", "overview"))
        for s in p.sources:
            hl = rt.health.get(s.id, Health())
            st = hl.state if s.enabled else "stopped"
            node(srcs, f"{type_label(s)} · {s.name}", dot(T[STATE_TOKEN.get(st, 'muted')]), ("source", s.id),
                 hl.message or st)
        mdl = node(root, "Plant model · ISA-95", "layers", ("doc", "model"), f"{len(rt.model)} tags")
        for site in rt.model.root.children.values():
            node(mdl, site.name, None, ("doc", "model"))
        pub = sum(t.published is not None for t in p.tools)
        tl = node(root, f"Tools  · {pub} published", "wrench", ("doc", "tools"))
        for t in p.tools:
            it = node(tl, t.name, None, ("tool", t.name), t.state)
            if t.state != "published":
                it.setForeground(0, ui_color("muted"))
        pr = node(root, "Tool profiles", "list", ("doc", "profiles"))
        for x in p.profiles:
            node(pr, f"{x.id} · {p.endpoint.path.rstrip('/')}/{x.id}", None, ("profile", x.id), x.title)
        node(root, f"MCP endpoint  · :{p.endpoint.port}", "arrow-left-right", ("doc", "endpoint"))
        node(root, "Safety & limits", "shield", ("doc", "safety"))
        node(root, "Audit log", "file-text", ("doc", "audit"))
        node(root, f"Licence  · {p.licence.edition}", "key-round", ("doc", "licence"))
        root.setExpanded(True)
        for i in range(root.childCount()):
            c = root.child(i)
            key = c.data(0, 256)
            if open_ is None:
                c.setExpanded(key in (("doc", "overview"), ("doc", "profiles")) and c.childCount() > 0)
            else:
                c.setExpanded(c.text(0).split("  ·")[0].split(" ·")[0] in open_)
            for j in range(c.childCount()):
                if c.child(j).data(0, 256) == sel:
                    self.tree.setCurrentItem(c.child(j))
        if open_ is None:
            srcs.setExpanded(True); pr.setExpanded(True)
        self._filter_tree(self.nav_filter.text())

    def _update_tree_status(self):
        """Live state only: source dots and counts (no rebuild - selection and scroll stay)."""
        root = self.tree.topLevelItem(0) if hasattr(self, "tree") else None
        if root is None:
            return
        on, n = self.rt.sources_online()
        for i in range(root.childCount()):
            c = root.child(i)
            if c.text(0).startswith("Sources"):
                c.setText(0, f"Sources  · {on} of {n} online")
                for j in range(c.childCount()):
                    it = c.child(j)
                    s = self.project.source(it.data(0, 256)[1])
                    if s is None:
                        continue
                    hl = self.rt.health.get(s.id, Health())
                    st = hl.state if s.enabled else "stopped"
                    it.setIcon(0, dot(T[STATE_TOKEN.get(st, "muted")]))
                    it.setToolTip(0, hl.message or st)
            elif c.text(0).startswith("Tools"):
                c.setText(0, f"Tools  · {sum(t.published is not None for t in self.project.tools)} published")

    def _expanded(self):
        if self.tree.topLevelItemCount() == 0:
            return None
        root = self.tree.topLevelItem(0)
        return {root.child(i).text(0).split("  ·")[0].split(" ·")[0] for i in range(root.childCount())
                if root.child(i).isExpanded()}

    def _toggle_expand(self):
        root = self.tree.topLevelItem(0)
        if root is None:
            return
        any_open = any(root.child(i).isExpanded() for i in range(root.childCount()))
        for i in range(root.childCount()):
            root.child(i).setExpanded(not any_open)

    def _filter_tree(self, text):
        t = text.strip().lower()

        def walk(it) -> bool:
            hit = not t or t in it.text(0).lower()
            kids = any([walk(it.child(i)) for i in range(it.childCount())])
            it.setHidden(not (hit or kids))
            return hit or kids
        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))

    def _nav(self, it):
        d = it.data(0, 256)
        if not d:
            return
        kind = d[0]
        if kind == "project":
            self.select(("project",))
        elif kind == "doc":
            self.open_doc(d[1])
        elif kind == "source":
            self.open_source(d[1])
        elif kind == "tool":
            self.open_doc("tools")
            self.select(("tool", d[1]))
        elif kind == "profile":
            self.open_doc("profiles")
            self.pages["profiles"].set_profile(d[1])
            self.select(("profile", d[1]))

    def _tree_menu(self, pos):
        it = self.tree.itemAt(pos)
        d = it.data(0, 256) if it else None
        if not d:
            return
        from PySide6.QtWidgets import QMenu
        m = QMenu(self)
        if d[0] == "source":
            m.addAction("Open", lambda: self.open_source(d[1]))
            m.addAction("Discover", lambda: self.open_source(d[1], 1) or self.pages[f"source:{d[1]}"].discover())
            m.addAction("Restart", lambda: self.rt.start_source(d[1]))
            m.addSeparator(); m.addAction("Delete...", lambda: self.delete_source(d[1]))
        elif d[0] == "tool":
            m.addAction("Test call...", lambda: self.test_call_dialog(d[1]))
            m.addAction("Edit parameters...", lambda: self.edit_tool_params(d[1]))
            m.addAction("Hide (unpublish)", lambda: self.unpublish(d[1]))
            m.addSeparator(); m.addAction("Delete...", lambda: self.delete_tool(d[1]))
        elif d[0] == "profile":
            m.addAction("Connect a client", lambda: self._nav(it))
            m.addAction("Delete...", lambda: self.delete_profile(d[1]))
        elif d == ("doc", "overview") and it.parent() and it.text(0).startswith("Sources"):
            m.addAction("Add source...", self.add_source)
        else:
            return
        m.exec(self.tree.viewport().mapToGlobal(pos))

    # ================================================================ centre: documents
    def build_center(self):
        self.doc_tabs = QTabWidget(); self.doc_tabs.setObjectName("docTabs")
        self.doc_tabs.setMovable(True); self.doc_tabs.setElideMode(Qt.TextElideMode.ElideRight)
        self.doc_tabs.tabBar().setDrawBase(False)
        return self.doc_tabs

    def _doc_icon(self, key):
        if key and key.startswith("source:"):
            s = self.project.source(key[7:])
            return ADAPTERS[s.type].ICON if s and s.type in ADAPTERS else "database"
        return DOCS.get(key, ("", "info"))[1]

    def _page(self, key):
        if key in self.pages:
            return self.pages[key]
        if key.startswith("source:"):
            w = SourcePage(self, key[7:])
        else:
            w = {"overview": OverviewPage, "tools": ToolsPage, "profiles": ProfilesPage, "model": ModelPage,
                 "safety": SafetyPage, "endpoint": EndpointPage, "audit": AuditPage, "licence": LicencePage,
                 "alarms": AlarmsPage}[key](self)
        w.setProperty("docKey", key)
        self.pages[key] = w
        return w

    def _title(self, key):
        if key.startswith("source:"):
            s = self.project.source(key[7:])
            return f"{type_label(s)} · {s.name}" if s else key
        return DOCS[key][0]

    def open_doc(self, key: str):
        w = self._page(key)
        if self.doc_tabs.indexOf(w) < 0:
            i = self.doc_tabs.addTab(w, make_icon(self._doc_icon(key), T["muted"]), self._title(key).replace("&", "&&"))
            close = QToolButton(); close.setObjectName("tabClose"); close.setText("×")
            close.setToolTip("Close tab (reopen from the Project tree)")
            close.clicked.connect(lambda _=False, wid=w: self._close_tab(wid))
            self.doc_tabs.tabBar().setTabButton(i, self.doc_tabs.tabBar().ButtonPosition.RightSide, close)
            w.refresh()
            self._stale.discard(key)
        self.doc_tabs.setCurrentWidget(w)
        return w

    def open_source(self, sid: str, step: int | None = None, tool: str | None = None):
        if self.project.source(sid) is None:
            return
        w = self.open_doc(f"source:{sid}")
        if tool:
            w.sel_tool = tool
        if step is not None and step != w.step:
            w.go(step)
        return w

    def _close_tab(self, w):
        i = self.doc_tabs.indexOf(w)
        if i >= 0:
            self.doc_tabs.removeTab(i)

    def _close_current_tab(self):
        w = self.doc_tabs.currentWidget()
        if w is not None:
            self._close_tab(w)

    def _doc_changed(self, _i):
        w = self.doc_tabs.currentWidget()
        if w is None:
            self.select(None)
            return
        key = w.property("docKey")
        if key in self._stale:
            self._stale.discard(key)
            w.refresh()
        else:
            w.tick()
        if key in ("overview", "safety", "licence", "audit", "alarms", "endpoint"):
            self.select(("endpoint",))
        elif key == "profiles":
            self.select(("profile", w.profile) if w.profile else None)
        elif key.startswith("source:") and w.step < 2:
            self.select(("source", key[7:]))

    def _refresh_current(self):
        """Full refresh of the page in front; the others refresh when they come to the front."""
        w = self.doc_tabs.currentWidget() if hasattr(self, "doc_tabs") else None
        for k, p in self.pages.items():
            if p is w:
                p.refresh()
            elif self.doc_tabs.indexOf(p) >= 0:
                self._stale.add(k)
        for i in range(self.doc_tabs.count()):
            key = self.doc_tabs.widget(i).property("docKey")
            if key.startswith("source:"):
                self.doc_tabs.setTabText(i, self._title(key).replace("&", "&&"))

    # ================================================================ right: Properties + Runtime
    def build_docks(self):
        super().build_docks()
        self.runtime_dock = QDockWidget("Runtime", self); self.runtime_dock.setObjectName("dockRuntime")
        self.runtime_dock.setWidget(self._runtime)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.runtime_dock)
        self.splitDockWidget(self.right_dock, self.runtime_dock, Qt.Orientation.Vertical)
        self.resizeDocks([self.right_dock, self.runtime_dock], [600, 230], Qt.Orientation.Vertical)
        self.log = QPlainTextEdit(readOnly=True, maximumBlockCount=2000); self.log.setFrameShape(QFrame.Shape.NoFrame)
        self.log_dock = QDockWidget("Log", self); self.log_dock.setObjectName("dockLog"); self.log_dock.setWidget(self.log)
        self.setCorner(Qt.Corner.BottomLeftCorner, Qt.DockWidgetArea.LeftDockWidgetArea)
        self.setCorner(Qt.Corner.BottomRightCorner, Qt.DockWidgetArea.RightDockWidgetArea)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.log_dock)
        self.resizeDocks([self.log_dock], [120], Qt.Orientation.Vertical)
        self.log_dock.hide()

    def build_right(self):
        rt = QFrame(); rt.setObjectName("runtimePanel")
        v = QVBoxLayout(rt); v.setContentsMargins(10, 8, 10, 8); v.setSpacing(8)
        chips = QHBoxLayout(); chips.setSpacing(6)
        self.chip_src, self.chip_mcp = Chip(), Chip()
        for c in (self.chip_src, self.chip_mcp):
            c.setMinimumWidth(CHIP_W); c.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            chips.addWidget(c)
        v.addLayout(chips)
        g = QGridLayout(); g.setSpacing(6)
        self.cards = {}
        for i, (k, t) in enumerate((("tools", "Tools published"), ("calls", "Calls today"),
                                    ("blocked", "Blocked by limits"), ("clients", "Clients"))):
            self.cards[k] = Card(t); g.addWidget(self.cards[k], i // 2, i % 2)
        v.addLayout(g); v.addStretch(1)
        self._runtime = rt
        self.props = PropertiesPanel(self.reg_icon, on_refresh=lambda: self.select(self._sel),
                                     empty_hint=lambda: ("No selection", "Select a source, tool or profile to see its settings"))
        self._sel = None
        return self.props

    def _build_status(self):
        self.st_alarm = QToolButton(); self.st_alarm.setObjectName("alarmStatus")
        self.st_alarm.clicked.connect(lambda: self.open_doc("alarms"))
        self.st_right = self.add_status_label("")
        self.statusBar().insertPermanentWidget(1, self.st_alarm)

    # ---- Properties targets
    def select(self, ref):
        self._sel = ref
        t = None
        try:
            if ref:
                t = getattr(self, f"_prop_{ref[0]}")(*ref[1:])
        except Exception as e:                    # an object just deleted / renamed
            log.debug("properties %s: %s", ref, e)
        self.props.show_target(t)

    def _prop_project(self):
        p = self.project
        return PropTarget(p.name, "folder", self.project_path or "not saved", [
            ("Project", [F("name", "Name"), F("site", "Site"), F("timezone", "Plant time zone", tip="IANA, e.g. Asia/Bangkok"),
                         F("description", "Description", "text")]),
            ("File", [F("file", "File", "info"), F("audit", "Audit folder", "info")])],
            {"name": p.name, "site": p.site, "timezone": p.timezone, "description": p.description,
             "file": self.project_path or "-", "audit": str(self.rt.audit.folder)},
            apply=lambda k, v: self._apply(p, k, v, structure=k == "name"), key=("project",))

    def _prop_endpoint(self):
        p = self.project
        ok, n, msg = getattr(self.pages.get("audit"), "chain", None) or (True, 0, "not verified yet")
        return PropTarget("MCP endpoint", "arrow-left-right", "Standard MCP server · works with any gateway or AI platform", [
            ("Server", [F("url", "URL", "info"), F("tr", "Transports", "info"), F("tls", "TLS", "info"),
                        F("state", "State", "info")]),
            ("Authentication", [F("mode", "Mode", "info"), F("keys", "API keys", "info"), F("oauth", "OAuth 2.1", "info")]),
            ("Audit", [F("store", "Store", "info"), F("chain", "Hash chain", "info"), F("export", "Export", "info")]),
            ("Licence", [F("ed", "Edition", "info"), F("src", "Sources", "info"), F("exp", "Expires", "info")])],
            {"url": self.server_url(), "tr": ", ".join(x for x, on in (("Streamable HTTP", p.endpoint.http),
                                                                       ("stdio", p.endpoint.stdio)) if on),
             "tls": "On" if p.endpoint.tls_cert else "Off (use a TLS proxy or set a certificate)",
             "state": "running" if self.server and self.server.running else "stopped",
             "mode": {"api_key": "API keys", "none": "None (local only)", "oauth": "OAuth (planned)"}[p.auth.mode],
             "keys": f"{len(p.auth.api_keys)} active", "oauth": "planned",
             "store": f"audit/{time.strftime('%Y-%m')}.jsonl", "chain": "Verified" if ok else f"BROKEN - {msg}",
             "export": f"syslog → {p.audit.syslog_host}" if p.audit.syslog_host else "CSV export",
             "ed": p.licence.edition, "src": f"{len(p.sources)} of {p.licence.max_sources or 'unlimited'} used",
             "exp": p.licence.expires or "-"}, key=("endpoint",), readonly=True,
            actions=[("Open endpoint settings", "pencil", lambda: self.open_doc("endpoint"))],
            live=lambda: {"state": "running" if self.server and self.server.running else "stopped"})

    def _prop_source(self, sid):
        s = self.project.source(sid)
        cls = ADAPTERS.get(s.type)
        hide = {f["key"] for f in (cls.SCHEMA if cls else []) if f["type"] == "password"}
        conn = [F(f["key"], f["label"], "info") for f in (cls.SCHEMA if cls else []) if f["key"] not in hide]
        vals = {f["key"]: s.settings.get(f["key"], f["default"]) for f in (cls.SCHEMA if cls else [])}

        def live():
            h = self.rt.health.get(sid, Health())
            return {"state": h.state if s.enabled else "disabled", "msg": h.message or "-",
                    "lat": f"{h.latency_ms:.0f} ms" if h.latency_ms else "-"}
        return PropTarget(s.name, cls.ICON if cls else "database", f"{type_label(s)} source · read-only", [
            ("Source", [F("type", "Type", "info"), F("isa95", "ISA-95 context", "info"), F("enabled", "Enabled", "info"),
                        F("disc", "Discovered", "info")]),
            ("Connection", conn),
            ("Status", [F("state", "State", "info"), F("lat", "Latency", "info"), F("msg", "Message", "info")])],
            {**vals, "type": type_label(s), "isa95": s.isa95 or "-", "enabled": "Yes" if s.enabled else "No",
             "disc": f"{len(s.objects)} objects · {len(s.items)} items · {s.discovered or 'never'}", **live()},
            key=("source", sid), readonly=True, live=live,
            actions=[("Edit connection", "pencil", lambda: self.open_source(sid, 0)),
                     ("Discover", "scan-search", lambda: self.open_source(sid, 1).discover()),
                     ("Delete source", "trash", lambda: self.delete_source(sid))])

    def _prop_tool(self, name):
        t = self.project.tool(name)
        if t is None:
            return None
        lim = self.project.limits
        a = annotations(t)
        src = self.project.source(t.source)
        st = {"published": f"Yes (v{t.version})", "changed": f"v{t.version} live · draft changed",
              "draft": f"Not yet (draft v{t.version + 1})"}[t.state]
        sub = (f"Curated tool from source {type_label(src)} · {src.name}" if t.kind == "curated" and src
               else "Generic tool over the ISA-95 model")
        return PropTarget(t.name, "wrench", sub, [
            ("Tool", [F("name", "Name", "info"), F("title", "Title"), F("description", "Description", "text"),
                      F("object", "Source object", "info"),
                      F("risk", "Risk class", "choice", extra={r: f"{r} · {RISK_INFO[r][0]}" for r in RISKS[:4]}),
                      F("ann", "Annotation", "info")]),
            ("Exposure", [F("pub", "Published", "info"), F("profiles", "Profiles", tip="comma separated profile ids"),
                          F("isa95", "ISA-95 scope"), F("enabled", "Enabled", "bool"),
                          F("sensitive_ok", "R2 allowed", "bool", tip="Sensitive reads stay off until enabled")]),
            ("Limits", [F("rows", "Max rows", "info"), F("days", "Max date range", "info"), F("to", "Timeout", "info"),
                        F("cite", "Citation", "info"), F("audit", "Audit", "info")])],
            {"name": t.name, "title": t.title, "description": t.description,
             "object": f"{t.object}" if t.kind == "curated" else "ISA-95 model", "risk": t.risk,
             "ann": ", ".join(f"{k}: {str(v).lower()}" for k, v in a.items() if k.endswith("Hint") and v is not None),
             "pub": st, "profiles": ", ".join(t.profiles), "isa95": t.isa95 or (src.isa95 if src else "") or "-",
             "enabled": t.enabled, "sensitive_ok": t.sensitive_ok, "rows": str(lim.max_rows),
             "days": f"{lim.max_days} days" if t.date_column or t.name == "query_history" else "-",
             "to": f"{lim.timeout_s:g} s", "cite": "☑ source in every answer", "audit": "☑ every call"},
            apply=lambda k, v: self._apply_tool(t, k, v), key=("tool", name),
            actions=[("Test call", "play", lambda: self.test_call_dialog(name)),
                     ("Edit parameters", "pencil", lambda: self.edit_tool_params(name))] +
                    ([("Open source", "database", lambda: self.open_source(t.source, 2, name))] if t.kind == "curated" else []))

    def _prop_profile(self, pid):
        p = self.project
        x = p.profile(pid)
        keys = [k for k in p.auth.api_keys if k.profile == pid]
        n = len(self.rt.live_tools(pid))
        clients = [F(f"k_{k.id}", k.client or k.name, "info") for k in keys] or [F("k_none", "Clients", "info")]
        vals = {f"k_{k.id}": f"API key ••••{k.last4}" for k in keys}
        vals["k_none"] = "no key yet"
        return PropTarget(f"Profile {pid}", "list", f"{x.title} · endpoint {p.endpoint.path.rstrip('/')}/{pid}", [
            ("Profile", [F("ep", "Endpoint", "info"), F("tools", "Tools", "info"), F("title", "Title"),
                         F("max_risk", "Max risk", "choice", extra={r: f"{r} · {RISK_INFO[r][0]}" for r in RISKS[:4]}),
                         F("scope", "ISA-95 scope", tip="Only paths under this one (empty = all)"),
                         F("description", "Description", "text")]),
            ("Clients", clients),
            ("Limits", [F("rate", "Rate limit", "info"), F("rows", "Max rows", "info"), F("days", "Max date range", "info")])],
            {"ep": f"{p.endpoint.path.rstrip('/')}/{pid}", "tools": str(n), "title": x.title, "max_risk": x.max_risk,
             "scope": x.scope, "description": x.description, "rate": f"{p.limits.rate_per_min} calls / min",
             "rows": str(p.limits.max_rows), "days": f"{p.limits.max_days} days", **vals},
            apply=lambda k, v: self._apply(x, k, v, tools=True), key=("profile", pid),
            actions=[("Copy URL", "copy", lambda: self.copy_text(self.server_url(pid))),
                     ("Delete profile", "trash", lambda: self.delete_profile(pid))])

    def _prop_item(self, sid, address):
        s = self.project.source(sid)
        it = next((i for i in s.items if i.address == address), None)
        if it is None:
            return None
        return PropTarget(it.name or it.address, "tag", f"{type_label(s)} · {s.name} · {it.address}", [
            ("ISA-95", [F("path", "Equipment path", tip="Site/Area/Line/Machine"), F("name", "Name"),
                        F("description", "Description")]),
            ("Value", [F("unit", "Unit"), F("lo", "Low limit"), F("hi", "High limit"), F("datatype", "Type", "info"),
                       F("exposed", "Exposed", "bool")])],
            {"path": it.path, "name": it.name, "description": it.description, "unit": it.unit,
             "lo": "" if it.lo is None else it.lo, "hi": "" if it.hi is None else it.hi, "datatype": it.datatype,
             "exposed": it.exposed},
            apply=lambda k, v: self._apply_item(it, k, v), key=("item", sid, address))

    def _prop_object(self, sid, name):
        o = self.project.source(sid).object(name)
        return PropTarget(o.name, "table", f"{o.kind} · {len(o.columns)} columns", [
            ("Object", [F("kind", "Kind", "info"), F("status", "Status", "info"), F("pii", "Personal data", "info")]),
            ("Columns", [F(f"c_{c.name}", c.name, "info") for c in o.columns])],
            {"kind": o.kind, "status": "blocked by rule" if o.blocked else ("exposed" if o.exposed else "hidden"),
             "pii": ", ".join(sorted(o.pii_columns())) or "none",
             **{f"c_{c.name}": c.type + (" · personal data" if c.pii else "") for c in o.columns}},
            key=("object", sid, name), readonly=True)

    def _apply(self, obj, k, v, structure=False, tools=False):
        if getattr(obj, k, None) != v:
            setattr(obj, k, v)
            self.mark_dirty(structure=structure, tools=tools)

    def _apply_tool(self, t, k, v):
        if k == "profiles":
            v = [x.strip() for x in str(v).split(",") if x.strip()]
            unknown = [x for x in v if not self.project.profile(x) and x != "*"]
            if unknown:
                self.set_status(f"Unknown profile(s): {', '.join(unknown)}", "bad")
                return
        if k == "isa95" and v == "-":
            v = ""
        self._apply(t, k, v, tools=True)
        self._refresh_current()

    def _apply_item(self, it, k, v):
        if k in ("lo", "hi"):
            try:
                v = None if str(v).strip() == "" else float(v)
            except ValueError:
                self.set_status("Limits must be numbers", "bad")
                return
        if k == "path":
            v = str(v).strip().strip("/")
        self._apply(it, k, v, structure=True, tools=True)

    # ================================================================ project
    def _set_project(self, p: Project, path: str | None):
        if self.server is not None:
            self.server.stop()
        self.rt.stop()
        self.project, self.project_path = p, path
        self.rt = Runtime(p, path, self.user)
        self.rt.start()
        self.server = McpServer(self.rt)
        for k in list(self.pages):
            w = self.pages.pop(k)
            i = self.doc_tabs.indexOf(w)
            if i >= 0:
                self.doc_tabs.removeTab(i)
            w.deleteLater()
        self._stale.clear()
        self.dirty = False
        self._update_title()
        if p.endpoint.http and self.qs.value("auto_start_server", True, type=bool):
            self.start_server(quiet=True)
        self._rebuild_tree()
        self.open_doc("overview")
        self._seen_rev = -1
        self._tick()
        self.select(("endpoint",))

    def load(self, path: str):
        p = Project.load(path)
        self._set_project(p, path)
        self.qs.setValue("last_project", path)
        self.set_status(f"Opened {path}", "ok")

    def _ask_save(self) -> bool:
        if not self.dirty:
            return True
        r = QMessageBox.question(self, APP_NAME, "Save changes to the current project?",
                                 QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard |
                                 QMessageBox.StandardButton.Cancel)
        if r == QMessageBox.StandardButton.Cancel:
            return False
        return self.save() if r == QMessageBox.StandardButton.Save else True

    def new_project(self):
        if self._ask_save():
            self._set_project(Project.new(), None)

    def open_project(self):
        if not self._ask_save():
            return
        f, _ = QFileDialog.getOpenFileName(self, "Open project", str(paths.projects_dir()),
                                           f"MCX Adapter project (*{PROJECT_EXT} *.yaml *.yml)")
        if f:
            try:
                self.load(f)
            except Exception as e:
                self.banners.post("open", f"Could not open {f}: {e}", "bad")

    def open_demo(self, first_run: bool = False):
        if not first_run and not self._ask_save():
            return
        from mcx_core import demo
        folder = paths.demo_dir()
        f = folder / "Plant adapter.mcx"
        keys = None
        if not f.exists():
            f, keys = demo.create(folder)
        self.load(str(f))
        if keys:
            self.banners.post("demo", f"Demo plant created in {folder}. Its API keys (eng / qe / mgr) are in "
                                      "demo_keys.txt - shown once, the project keeps only hashes. Start "
                                      "Help > Factory energy simulator for the Modbus power meters.", "info",
                              "Open folder", lambda: self.open_folder(folder))

    def save(self) -> bool:
        if not self.project_path:
            return self.save_as()
        try:
            self.project.save(self.project_path)
        except Exception as e:
            self.banners.post("save", f"Could not save: {e}", "bad")
            return False
        self.dirty = False
        self._update_title()
        self.set_status(f"Saved {self.project_path}", "ok")
        return True

    def save_as(self) -> bool:
        paths.projects_dir().mkdir(parents=True, exist_ok=True)
        f, _ = QFileDialog.getSaveFileName(self, "Save project", str(paths.projects_dir() / f"{self.project.name}{PROJECT_EXT}"),
                                           f"MCX Adapter project (*{PROJECT_EXT})")
        if not f:
            return False
        if not f.endswith(PROJECT_EXT):
            f += PROJECT_EXT
        self.project_path = f
        self.rt.path = f
        self.qs.setValue("last_project", f)
        ok = self.save()
        self._rebuild_tree()
        return ok

    def mark_dirty(self, structure: bool = False, tools: bool = False):
        self.dirty = True
        self._update_title()
        if structure or tools:
            self.rt.notify_tools_changed() if tools else self.rt._bump()
            from mcx_core.model import Model
            self.rt.model = Model.build(self.project)
        if structure:
            self._rebuild_tree()

    def _update_title(self):
        name = Path(self.project_path).name if self.project_path else "not saved"
        self.setWindowTitle(f"{'* ' if self.dirty else ''}{name} - {APP_NAME}")

    def open_folder(self, folder=None):
        folder = folder or (Path(self.project_path).parent if self.project_path else paths.data_dir())
        Path(folder).mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    # ================================================================ sources
    def add_source(self):
        d = AddSourceDialog(self, {s.id for s in self.project.sources})
        if d.exec() != d.DialogCode.Accepted:
            return
        kind, name = d.result_value()
        lic = self.project.licence
        if lic.max_sources and len(self.project.sources) >= lic.max_sources:
            self.banners.post("lic", f"Licence {lic.edition} allows {lic.max_sources} sources - this one is added "
                                     "but counts over the limit.", "warn", "Licence", lambda: self.open_doc("licence"))
        sid = self.project.unique_id(name, {s.id for s in self.project.sources})
        cls = ADAPTERS[kind]
        s = SourceCfg(sid, name, kind, settings={f["key"]: f["default"] for f in cls.SCHEMA if f["default"] not in (None, "")},
                      isa95=self.project.site or "")
        self.project.sources.append(s)
        self.mark_dirty(structure=True)
        self.rt.start_source(sid)
        self.open_source(sid, 0)
        self.set_status(f"Source {name} added - set the connection, Test connection, then Discover", "info")

    def _cur_source(self):
        w = self.doc_tabs.currentWidget()
        key = w.property("docKey") if w else ""
        return key[7:] if key and key.startswith("source:") else None

    def discover_current(self):
        sid = self._cur_source() or (self.project.sources[0].id if self.project.sources else None)
        if sid is None:
            self.add_source(); return
        w = self.open_source(sid, 1)
        w.discover()

    def _test_conn_current(self):
        sid = self._cur_source()
        if sid:
            self.open_source(sid, 0).test_connection()

    def delete_source(self, sid):
        s = self.project.source(sid)
        tools = [t.name for t in self.project.tools if t.source == sid]
        if not s or not confirm(self, "Delete source", f"Delete {s.name}?" + (
                f" Its {len(tools)} tools ({', '.join(tools)}) are deleted too and disappear from every client "
                "after you publish." if tools else "")):
            return
        self.rt.stop_source(sid)
        self.project.sources.remove(s)
        self.project.tools = [t for t in self.project.tools if t.source != sid]
        w = self.pages.pop(f"source:{sid}", None)
        if w is not None:
            self._close_tab(w); w.deleteLater()
        self.mark_dirty(structure=True, tools=True)
        self.rt.notify_tools_changed()

    # ================================================================ tools
    def publish(self, names=None):
        pend = [t.name for t in self.project.pending()] if names is None else names
        if not pend:
            self.set_status("Nothing to publish", "muted")
            return
        errors = []
        done = self.rt.publish(pend, errors=errors)
        if done:
            self.mark_dirty(structure=True)
            self.toast.popup(f"Published {len(done)} tool{'s' if len(done) != 1 else ''} · tools/list_changed sent")
            log.info("published %s", ", ".join(done))
        if errors:
            self.banners.post("publish", "Held back: " + "; ".join(f"{n} ({e.split(': ', 1)[-1]})" for n, e in errors),
                              "warn")
        self._refresh_current()

    def unpublish(self, name):
        self.project.unpublish(name, self.user)
        self.mark_dirty(structure=True)
        self.rt.notify_tools_changed()
        self._refresh_current()
        self.set_status(f"{name} hidden - clients were told (tools/list_changed)", "info")

    def delete_tool(self, name):
        t = self.project.tool(name)
        if t is None or t.kind == "generic":
            self.set_status("Generic tools cannot be deleted - remove them from profiles instead", "warn")
            return
        if confirm(self, "Delete tool", f"Delete {name}? Clients stop seeing it at once."):
            self.project.tools.remove(t)
            self.mark_dirty(structure=True, tools=True)
            self.rt.notify_tools_changed()
            self._refresh_current()

    def new_curated_tool(self):
        tabs = [s for s in self.project.sources if ADAPTERS.get(s.type) and ADAPTERS[s.type].FAMILY == "table"]
        objs = [(f"{s.name} · {o.name}", (s.id, o.name)) for s in tabs for o in s.objects if not o.blocked]
        if not objs:
            self.set_status("Discover a SQL / Excel / REST source first", "warn")
            return
        r = ask(self, "New curated tool", [("obj", "From object", objs, ""), ("name", "Tool name", "get_", "get_oee")],
                "A tool answers one business question with typed parameters - never free SQL.",
                check=lambda v: "" if __import__("re").match(r"^[a-z][a-z0-9_]{1,63}$", v["name"]) and
                not self.project.tool(v["name"]) else "Lower snake case, unique (e.g. get_scrap_by_shift)")
        if not r:
            return
        sid, oname = r["obj"]
        o = self.project.source(sid).object(oname)
        o.exposed = True
        self.project.tools.append(new_curated(r["name"], sid, o, [x.id for x in self.project.profiles]))
        self.mark_dirty(structure=True, tools=True)
        self.open_source(sid, 2, r["name"])

    def edit_tool_params(self, name):
        t = self.project.tool(name)
        if t is None:
            return
        if t.kind == "generic":
            self.set_status("Generic tools have fixed parameters", "muted")
            return
        src = self.project.source(t.source)
        o = src.object(t.object) if src else None
        d = ParamsDialog(self, t, [c.name for c in o.columns if not c.pii] if o else [])
        if d.exec() == d.DialogCode.Accepted and d.value is not None:
            for k in ("params", "measures", "columns", "order_by", "date_column"):
                setattr(t, k, getattr(d.value, k))
            self.mark_dirty(tools=True)
            self._refresh_current()
            self.select(("tool", name))

    def test_call(self, name, args):
        """Worker thread: run a tool's draft as a Studio test call. -> (envelope, error, ms)."""
        t0 = time.perf_counter()
        self.rt.track_client(f"studio:{self.user}", f"MCX Studio ({self.user})", "Studio", "", "local")
        env, err = self.rt.call_tool(name, args, client=f"studio:{self.user}", via="Studio", draft=True)
        return env, err, (time.perf_counter() - t0) * 1000

    def test_call_dialog(self, name=None):
        names = [t.name for t in self.project.tools]
        if not names:
            return
        if name is None:
            w = self.doc_tabs.currentWidget()
            name = getattr(w, "sel_tool", None) or (self._sel[1] if self._sel and self._sel[0] == "tool" else None)
        TestCallDialog(self, self, names, name).exec()

    # ================================================================ profiles / keys
    def new_profile(self):
        r = ask(self, "New profile", [("id", "Id (endpoint)", "", "eng, qe, mgr"), ("title", "Title", "", "Engineering"),
                                      ("max_risk", "Max risk", [("R1", "R1 · read"), ("R0", "R0 · metadata"),
                                                                ("R3", "R3 · compute")], "")],
                "One endpoint per team: /mcp/<id>. A gateway sees only the tools in its profile.",
                check=lambda v: "" if __import__("re").match(r"^[a-z][a-z0-9_-]{0,30}$", v["id"]) and
                not self.project.profile(v["id"]) else "Lower case id, unique")
        if not r:
            return
        self.project.profiles.append(Profile(r["id"], r["title"] or r["id"], "", r["max_risk"]))
        self.mark_dirty(structure=True, tools=True)
        self.open_doc("profiles").set_profile(r["id"])
        self.select(("profile", r["id"]))

    def delete_profile(self, pid):
        if not confirm(self, "Delete profile", f"Delete profile {pid}? Its endpoint stops answering and its API keys "
                                               "are deleted."):
            return
        self.project.profiles = [x for x in self.project.profiles if x.id != pid]
        self.project.auth.api_keys = [k for k in self.project.auth.api_keys if k.profile != pid]
        for t in self.project.tools:
            t.profiles = [x for x in t.profiles if x != pid]
        self.mark_dirty(structure=True, tools=True)
        self._refresh_current()

    def rotate_key(self, kid, profile, client):
        p = self.project
        if kid is None:
            prof = profile
            if prof is None:
                r = ask(self, "New API key", [("profile", "Profile", [(x.id, x.id) for x in p.profiles] + [("", "any")], ""),
                                              ("client", "Client / platform", "", "Copilot Studio, ContextForge ...")])
                if not r:
                    return
                prof, client = r["profile"], r["client"]
            k, secret = p.new_api_key(f"{prof or 'key'}-key", prof or "", client)
            title = f"API key {k.id}"
        else:
            if not confirm(self, "Rotate key", f"Rotate {kid}? The old secret stops working at once.", "Rotate"):
                return
            secret = p.rotate_api_key(kid)
            title = f"API key {kid} (rotated)"
        self.mark_dirty()
        if self.project_path:
            self.save()                          # the old hash must not stay valid on disk
        show_secret(self, title, secret, "Use it as  Authorization: Bearer <key>  (or the X-API-Key header).")
        self._refresh_current()

    def delete_key(self, kid):
        if confirm(self, "Delete key", f"Delete API key {kid}? Clients using it are refused at once."):
            self.project.auth.api_keys = [k for k in self.project.auth.api_keys if k.id != kid]
            self.mark_dirty()
            self._refresh_current()

    # ================================================================ MCP server
    def server_url(self, profile: str = "") -> str:
        base = self.project.endpoint.base_url()
        return f"{base}/{profile}" if profile else base

    def start_server(self, quiet=False):
        if self.server is None:
            self.server = McpServer(self.rt)
        try:
            self.server.start()
            self.banners.dismiss("server")
            log.info("MCP server on %s", self.server_url())
        except Exception as e:
            self.server.httpd = None
            self.server.error = str(e)
            self.banners.post("server", f"MCP server not started: {e}", "warn", "Endpoint settings",
                              lambda: self.open_doc("endpoint"))
        self._tick(force=True)

    def toggle_server(self):
        if self.server is not None and self.server.running:
            self.server.stop()
            log.info("MCP server stopped")
            self._tick(force=True)
        else:
            self.start_server()

    def test_endpoint(self, pid) -> str:
        """Worker thread: the endpoint answers and the profile lists its tools."""
        import urllib.request
        if self.server is None or not self.server.running:
            raise RuntimeError("the MCP server is stopped")
        ep = self.project.endpoint
        host = "127.0.0.1" if ep.host in ("0.0.0.0", "") else ep.host
        scheme = "https" if ep.tls_cert else "http"
        with urllib.request.urlopen(f"{scheme}://{host}:{ep.port}/health", timeout=5) as r:
            json.loads(r.read())
        s = Session(self.rt, f"studio:{self.user}", pid, "Studio", "local")
        s.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18",
                                                                               "clientInfo": {"name": "MCX Studio test"}}})
        res = s.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        n = len(res["result"]["tools"])
        return f"endpoint reachable · {n} tools listed · tools/list_changed sent after publish"

    def copy_text(self, text):
        QApplication.clipboard().setText(text)
        self.toast.popup("Copied")

    # ================================================================ audit
    def verify_audit(self):
        res = self.rt.audit.verify()
        page = self._page("audit")
        page.set_chain(res)
        ok, n, msg = res
        self.set_status(("Audit chain OK: " if ok else "AUDIT CHAIN BROKEN: ") + msg, "ok" if ok else "bad")
        if not ok:
            self.banners.post("audit", f"Audit log: {msg}", "bad", "Open audit log", lambda: self.open_doc("audit"))

    def export_audit(self):
        f, _ = QFileDialog.getSaveFileName(self, "Export audit log", f"audit_{time.strftime('%Y%m%d')}.csv", "CSV (*.csv)")
        if f:
            self.rt.audit.export_csv(f)
            self.set_status(f"Audit exported to {f}", "ok")

    def export_report(self):
        f, _ = QFileDialog.getSaveFileName(self, "Export report", f"mcx_report_{time.strftime('%Y%m%d')}.json", "JSON (*.json)")
        if not f:
            return
        rt = self.rt
        data = {"adapter": self.project.name, "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "summary": rt.summary(),
                "sources": {s.id: {"type": type_label(s), "state": rt.health.get(s.id, Health()).state}
                            for s in self.project.sources},
                "tools": [{"name": t.name, "risk": t.risk, "state": t.state, "version": t.version,
                           "profiles": t.profiles} for t in self.project.tools],
                "audit": dict(zip(("ok", "records", "message"), rt.audit.verify()))}
        Path(f).write_text(json.dumps(data, indent=2), encoding="utf-8")
        self.set_status(f"Report exported to {f}", "ok")

    # ================================================================ periodic state
    def _tick(self, force=False):
        rt = self.rt
        if not force and rt.rev == self._seen_rev:
            return
        self._seen_rev = rt.rev
        s = rt.summary()
        srv_on = self.server is not None and self.server.running
        self.chip_src.set(f"Sources {s['sources_online']}/{s['sources']}",
                          "ok" if s["sources_online"] == s["sources"] and s["sources"] else "warn")
        self.chip_mcp.set("MCP ready" if srv_on else "MCP stopped", "accent" if srv_on else "bad")
        self.cards["tools"].set_value(s["tools"])
        self.cards["calls"].set_value(fmt_int(s["calls_today"]))
        self.cards["blocked"].set_value(s["blocked_today"])
        if self.cards["blocked"].value.property("level") != "accent":
            set_prop(self.cards["blocked"].value, "level", "accent")
        self.cards["clients"].set_value(s["clients"])
        alarms = self._page("alarms").items()
        down = [a for a in alarms if a[0] in ("warn", "bad")]
        self.act_alarms.setText(f"Alarms ({len(down)})" if down else "Alarms")
        self.st_alarm.setText(f"▲ {down[0][1]}: {down[0][2]}"[:60] if down else "")
        set_prop(self.st_alarm, "level", "warn" if down else "")
        ok = getattr(self.pages.get("audit"), "chain", None)
        self.st_right.setText(f"{s['clients']} clients · {fmt_int(s['calls_today'])} calls today · "
                              f"audit {'OK' if not ok or ok[0] else 'BROKEN'}")
        msg = (f"MCP server {'ready' if srv_on else 'stopped'} · {s['sources_online']}/{s['sources']} sources · "
               f"{s['tools']} tools · {s['profiles']} profiles · read-only")
        if not self.status_msg.property("sticky"):
            self.set_status(msg, "ok" if srv_on else "warn")
        pend = len(self.project.pending())
        self.act_pub.setText(f"Publish ({pend})" if pend else "Publish")
        # tree labels / dots and the page in front (light update)
        self._update_tree_status()
        w = self.doc_tabs.currentWidget()
        if w is not None:
            w.tick()

    def set_status(self, text, level="muted"):
        super().set_status(text, level)
        if level in ("bad", "warn", "info") or text.startswith(("Saved", "Opened", "Audit", "Report")):
            self.status_msg.setProperty("sticky", True)
            QTimer.singleShot(6000, lambda: self.status_msg.setProperty("sticky", False))
        if level in ("bad", "warn"):
            log.warning(text)

    # ================================================================ misc
    def build_menus(self):
        super().build_menus()
        h = self.help_menu
        h.addAction(self.act("Factory energy simulator (Modbus 127.0.0.1:5020)", self.run_energy_sim))
        h.addAction(self.act("Documentation folder", lambda: self.open_folder(paths.docs_dir() or paths.data_dir())))
        h.addSeparator()
        h.addAction(self.act(f"About {APP_NAME}", lambda: QMessageBox.about(
            self, APP_NAME, f"<b>{APP_NAME}</b> {__version__}<br>Plant data as safe, standard MCP tools - works "
                            "behind any MCP gateway or directly with Claude.<br><br>SPEX")))

    def run_energy_sim(self):
        if self._sim_proc and self._sim_proc.poll() is None:
            self.set_status("Factory energy simulator already running", "muted")
            return
        env = dict(os.environ)
        src = str(Path(__file__).resolve().parents[1])
        env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
        self._sim_proc = subprocess.Popen([sys.executable, "-m", "mcx_core.sim.factory_energy"], env=env,
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.set_status("Factory energy simulator started on 127.0.0.1:5020", "ok")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, "toolbar"):
            self._responsive()

    def _responsive(self):
        """Wide (>= 1500 px): toolbar text; narrower: icons only (names in tooltips), Publish keeps its text."""
        wide = self.width() >= 1500
        if getattr(self, "_wide", None) == wide:
            return
        self._wide = wide
        for a in self.toolbar.actions():
            b = self.toolbar.widgetForAction(a)
            if isinstance(b, QToolButton) and a not in (*self._icon_only, self.act_left, self.act_logp, self.act_right):
                b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon if wide or a is self.act_pub
                                     else Qt.ToolButtonStyle.ToolButtonIconOnly)
                ui.refit(b)

    @staticmethod
    def _screen_is_small() -> bool:
        scr = QApplication.primaryScreen()
        if scr is None:
            return False
        g = scr.availableGeometry()
        return g.width() < 1440 or g.height() < 860

    def _fit_to_screen(self):
        scr = (self.screen() or QApplication.primaryScreen()).availableGeometry()
        if self.qs.value("geometry") is not None and scr.contains(self.frameGeometry()):
            return
        w = min(scr.width(), max(self.minimumWidth(), min(1640, int(scr.width() * 0.94))))
        h = min(scr.height(), max(self.minimumHeight(), min(940, int(scr.height() * 0.92))))
        self.resize(w, h)
        self.move(scr.x() + (scr.width() - w) // 2, scr.y() + (scr.height() - h) // 2)

    def closeEvent(self, e):
        if not self._ask_save():
            e.ignore()
            return
        self.timer.stop()
        if self.server is not None:
            self.server.stop()
        self.rt.stop()
        if self._sim_proc and self._sim_proc.poll() is None:
            self._sim_proc.terminate()
        logging.getLogger().removeHandler(self.bridge)
        super().closeEvent(e)


# ----------------------------------------------------------------------- helpers
def F(key, label, type="str", extra=None, tip=""):
    return {"key": key, "label": label, "type": type, "default": None, "extra": extra, "tip": tip}


def ui_color(token):
    from PySide6.QtGui import QBrush, QColor
    return QBrush(QColor(T[token]))


def QMenu_(title, parent, mb, before):
    from PySide6.QtWidgets import QMenu
    m = QMenu(title, parent)
    mb.insertMenu(before.menuAction(), m)
    return m

