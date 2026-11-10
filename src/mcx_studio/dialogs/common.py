"""
common.py - MCX Studio pop-ups. Pop-ups are for actions only (add a source, show a secret once,
confirm a delete, edit a tool's parameters, a test call); settings live in documents and the
Properties panel. Header / footer bars use the theme's dlgHeader / dlgFooter styles.
"""
from __future__ import annotations

import json

import yaml
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)

from mcx_core.adapters import ADAPTERS, PLANNED
from mcx_core.config import ToolDef
from mcx_studio.theme.ui_kit import T, make_icon, primary_button


class ActionDialog(QDialog):
    """Title bar + body + footer (Cancel / OK)."""

    def __init__(self, parent, title: str, subtitle: str = "", ok: str = "OK", cancel: str | None = "Cancel"):
        super().__init__(parent)
        self.setWindowTitle(title)
        head = QFrame(); head.setObjectName("dlgHeader")
        hv = QVBoxLayout(head); hv.setContentsMargins(16, 12, 16, 12); hv.setSpacing(2)
        t = QLabel(title); t.setObjectName("dlgTitle"); hv.addWidget(t)
        if subtitle:
            s = QLabel(subtitle); s.setProperty("muted", True); s.setWordWrap(True); hv.addWidget(s)
        self.body = QWidget()
        self.bl = QVBoxLayout(self.body); self.bl.setContentsMargins(16, 12, 16, 12); self.bl.setSpacing(10)
        foot = QFrame(); foot.setObjectName("dlgFooter")
        fl = QHBoxLayout(foot); fl.setContentsMargins(16, 8, 16, 8)
        self.msg = QLabel(""); self.msg.setObjectName("statusMsg"); self.msg.setProperty("level", "bad")
        fl.addWidget(self.msg, 1)
        if cancel:
            c = QPushButton(cancel); c.clicked.connect(self.reject); fl.addWidget(c)
        self.ok = primary_button(ok); self.ok.clicked.connect(self._accept); self.ok.setDefault(True)
        fl.addWidget(self.ok)
        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)
        v.addWidget(head); v.addWidget(self.body, 1); v.addWidget(foot)

    def _accept(self):
        err = self.validate()
        if err:
            self.msg.setText(err)
            return
        self.accept()

    def validate(self) -> str:
        return ""


def confirm(parent, title: str, text: str, ok: str = "Delete") -> bool:
    d = ActionDialog(parent, title, "", ok)
    lbl = QLabel(text); lbl.setWordWrap(True); d.bl.addWidget(lbl)
    d.resize(440, 180)
    return d.exec() == QDialog.DialogCode.Accepted


def show_secret(parent, title: str, secret: str, note: str):
    d = ActionDialog(parent, title, "Shown once - only a hash is stored. Copy it now into the client / gateway.",
                     "Done", None)
    e = QLineEdit(secret); e.setReadOnly(True); e.setObjectName("codeEdit")
    row = QHBoxLayout(); row.addWidget(e, 1)
    cp = QPushButton("Copy"); cp.clicked.connect(lambda: (QApplication.clipboard().setText(secret), cp.setText("Copied")))
    row.addWidget(cp)
    d.bl.addLayout(row)
    n = QLabel(note); n.setProperty("muted", True); n.setWordWrap(True); d.bl.addWidget(n)
    d.resize(560, 220)
    d.exec()


def ask(parent, title: str, fields: list[tuple], subtitle: str = "", ok: str = "Create", check=None) -> dict | None:
    """Small form pop-up. fields: (key, label, default or [choices], placeholder)."""
    d = ActionDialog(parent, title, subtitle, ok)
    fl = QFormLayout(); fl.setSpacing(8)
    eds = {}
    for key, label, default, ph in fields:
        if isinstance(default, list):
            e = QComboBox()
            for c in default:
                e.addItem(*(c if isinstance(c, tuple) else (c, c)))
        else:
            e = QLineEdit(str(default or "")); e.setPlaceholderText(ph or "")
        eds[key] = e; fl.addRow(label, e)
    d.bl.addLayout(fl)
    vals = lambda: {k: (e.currentData() if isinstance(e, QComboBox) else e.text().strip()) for k, e in eds.items()}  # noqa
    if check:
        d.validate = lambda: check(vals())
    d.resize(460, 120 + 40 * len(fields))
    return vals() if d.exec() == QDialog.DialogCode.Accepted else None


class AddSourceDialog(ActionDialog):
    """Pick a source type (grouped, planned ones shown greyed) and a name."""

    def __init__(self, parent, taken: set):
        super().__init__(parent, "Add source", "Connect a data source; MCX Adapter only reads from it.", "Add")
        self.taken = taken
        self.list = QListWidget()
        for cat in ("Databases", "Industrial protocols", "Files and APIs"):
            h = QListWidgetItem(cat); h.setFlags(Qt.ItemFlag.NoItemFlags)
            f = h.font(); f.setBold(True); h.setFont(f)
            self.list.addItem(h)
            for k, cls in ADAPTERS.items():
                if cls.CATEGORY == cat:
                    it = QListWidgetItem(make_icon(cls.ICON, T["text"]), "   " + cls.LABEL)
                    it.setData(Qt.ItemDataRole.UserRole, k)
                    self.list.addItem(it)
        h = QListWidgetItem("Later in the roadmap"); h.setFlags(Qt.ItemFlag.NoItemFlags)
        self.list.addItem(h)
        for name in PLANNED:
            it = QListWidgetItem("   " + name); it.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(it)
        self.list.setMinimumHeight(320)
        self.name = QLineEdit(); self.name.setPlaceholderText("e.g. smc_demo, GENESIS64, power meters")
        self.list.currentItemChanged.connect(self._pick)
        self.list.itemDoubleClicked.connect(lambda *_: self._accept())
        self.bl.addWidget(self.list, 1)
        fl = QFormLayout(); fl.addRow("Name", self.name); self.bl.addLayout(fl)
        self.list.setCurrentRow(1)
        self.resize(460, 560)

    def _pick(self, it):
        k = it.data(Qt.ItemDataRole.UserRole) if it else None
        if k and not self.name.isModified():
            self.name.setText({"sql": "sql_server", "opcua": "opc_ua", "modbus": "power_meters", "mqtt": "iot_sensors",
                               "files": "shift_reports", "rest": "mes_api", "sim": "plant_simulator"}.get(k, k))

    def validate(self):
        it = self.list.currentItem()
        if not it or not it.data(Qt.ItemDataRole.UserRole):
            return "Choose a source type"
        if not self.name.text().strip():
            return "Give the source a name"
        return ""

    def result_value(self):
        return self.list.currentItem().data(Qt.ItemDataRole.UserRole), self.name.text().strip()


class ParamsDialog(ActionDialog):
    """Edit a curated tool's parameters / measures / columns as YAML (typed - validated on OK)."""

    def __init__(self, parent, tool: ToolDef, columns: list[str]):
        super().__init__(parent, f"Parameters · {tool.name}",
                         "Typed parameters only: filters compare a column with a bound value; group_by picks columns "
                         "from a fixed list. Columns available: " + (", ".join(columns) or "-"), "Apply")
        from dataclasses import asdict
        d = asdict(tool)
        data = {k: d[k] for k in ("params", "measures", "columns", "order_by", "date_column")}
        self.ed = QPlainTextEdit(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
        self.ed.setObjectName("code")
        self.bl.addWidget(self.ed, 1)
        self.tool, self.columns, self.value = tool, {c.lower() for c in columns}, None
        self.resize(720, 640)

    def validate(self):
        try:
            data = yaml.safe_load(self.ed.toPlainText()) or {}
            t = ToolDef.from_dict({"name": self.tool.name, **data})
        except Exception as e:
            return f"Not valid: {e}"
        used = {p.column.lower() for p in t.params if p.column} | {m.column.lower() for m in t.measures} | \
               {c.lower() for c in t.columns}
        for p in t.params:
            for cols in p.group.values():
                used |= {c.lower() for c in cols}
            if p.op not in ("=", ">=", "<=", ">", "<", "in", "contains"):
                return f"{p.name}: operator {p.op!r} not allowed"
        bad = sorted(u for u in used if u not in self.columns and u != "*")
        if bad and self.columns:
            return "Unknown column(s): " + ", ".join(bad)
        self.value = t
        return ""


class TestCallDialog(ActionDialog):
    def __init__(self, parent, host, tools: list[str], current: str | None):
        super().__init__(parent, "Test call", "Runs the draft of the tool with the adapter's limits; audited as a test.",
                         "Close", None)
        from mcx_studio.widgets.source_page import default_args, parse_args
        self.host, self.parse = host, parse_args
        self.default_args = default_args
        top = QHBoxLayout()
        self.combo = QComboBox(); self.combo.addItems(tools)
        if current in tools:
            self.combo.setCurrentText(current)
        self.args = QLineEdit(); self.args.setObjectName("codeEdit")
        run = primary_button("▶  Run"); run.clicked.connect(self.run)
        self.args.returnPressed.connect(self.run)
        top.addWidget(self.combo); top.addWidget(self.args, 1); top.addWidget(run)
        self.out = QPlainTextEdit(); self.out.setObjectName("code"); self.out.setReadOnly(True)
        self.note = QLabel(""); self.note.setProperty("muted", True)
        self.bl.addLayout(top); self.bl.addWidget(self.out, 1); self.bl.addWidget(self.note)
        self.combo.currentTextChanged.connect(self._tool)
        self._tool(self.combo.currentText())
        self.resize(820, 620)

    def _tool(self, name):
        t = self.host.project.tool(name)
        self.args.setText(self.default_args(t) if t else "")

    def run(self):
        from mcx_studio.widgets.common import Worker
        try:
            args = self.parse(self.args.text())
        except ValueError as e:
            self.note.setText(str(e)); return
        name = self.combo.currentText()
        self.note.setText("Running…")
        Worker.run(lambda: self.host.test_call(name, args), self._done)

    def _done(self, res, err):
        if err:
            self.out.setPlainText(str(err)); return
        env, error, ms = res
        self.out.setPlainText(json.dumps(env, indent=2, ensure_ascii=False, default=str))
        self.note.setText((f"✕ {error}" if error else f"{len(env.get('rows', []))} rows") + f" · {ms:.0f} ms · audited")
