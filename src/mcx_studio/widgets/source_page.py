"""
source_page.py - one document per source: 1. Connect > 2. Discover > 3. Expose as MCP tools >
4. Review & publish (the setup flow of MCX Adapter, no YAML needed).

Table sources (SQL, files, REST): discovered objects become curated tools (allow-list: new objects
start hidden, blocked / personal-data objects are marked). Tag sources (OPC UA, Modbus, MQTT,
simulator): discovered items get an ISA-95 path and are served by the generic tools.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QPushButton, QSpinBox, QStackedWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget)

from mcx_core.adapters import ADAPTERS, Health, family, type_label
from mcx_core.safety import annotations, risk_level
from mcx_core.tools import input_schema, new_curated, signature
from mcx_studio.theme.ui_kit import primary_button, set_prop
from mcx_studio.widgets.common import (Columns, Kpi, KpiRow, Page, StepBar, Worker, color, fmt_int, mono,
                                       muted, panel, risk_cell, row, soft, table)

STEPS = ["Connect", "Discover", "Expose as MCP tools", "Review & publish"]


def parse_args(text: str) -> dict:
    """'line="L2", date_from=2026-11-02' or a JSON object -> dict (values stay text; tools coerce)."""
    text = text.strip()
    if text.startswith("{"):
        return json.loads(text)
    out = {}
    for m in re.finditer(r'(\w+)\s*=\s*("([^"]*)"|\'([^\']*)\'|[^,]*)', text):
        v = m.group(3) if m.group(3) is not None else (m.group(4) if m.group(4) is not None else m.group(2).strip())
        out[m.group(1)] = v
    return out


def default_args(tool) -> str:
    today = dt.date.today()
    parts = []
    for p in tool.params:
        if p.type in ("date", "datetime") and p.required:
            d = today - dt.timedelta(days=6) if p.op in (">=", ">") or p.name in ("from", "date_from") else today
            v = d.isoformat() if p.type == "date" else dt.datetime.combine(d, dt.time()).isoformat()
            if p.name == "to":
                v = dt.datetime.now().replace(microsecond=0).isoformat()
            parts.append(f'{p.name}="{v}"')
        elif p.name == "path" and p.required:
            parts.append(f'{p.name}="{p.default or ""}"')
        elif p.values and p.name == "line":
            parts.append(f'{p.name}="{p.values[min(1, len(p.values) - 1)]}"')
    return ", ".join(parts)


class SourcePage(Page):
    def __init__(self, host, sid: str):
        super().__init__(host, "", "")
        self.sid = sid
        self.step = 0
        self.sel_tool: str | None = None
        self.tool("Re-discover", "refresh", self.discover, tip="Read the source's tables / tags again")
        self.b_test = self.tool("Test call", "play", self.run_test, right=True)
        self.b_pub = self.tool("Publish", "upload", self.publish, right=True)
        self.tool("", "list", [("Test connection", self.test_connection), ("Restart source", self.restart),
                               (None, None), ("Delete source...", lambda: host.delete_source(self.sid))],
                  right=True, tip="More")
        self.steps = StepBar(STEPS, self.go)
        self.v.addWidget(self.steps)
        self.stack = QStackedWidget()
        self.v.addWidget(self.stack, 1)
        for build in (self._build_connect, self._build_discover, self._build_expose, self._build_review):
            self.stack.addWidget(build())
        self.v.addStretch(0)

    @property
    def cfg(self):
        return self.host.project.source(self.sid)

    @property
    def tags(self) -> bool:
        return family(self.cfg) == "tags"

    # ================================================================ steps
    def go(self, k: int):
        self.step = k
        self.stack.setCurrentIndex(k)
        self.refresh()

    def _done(self) -> set:
        c, h = self.cfg, self.host.rt.health.get(self.sid, Health())
        done = set()
        if h.state == "online":
            done.add(0)
        if c.discovered or c.objects or c.items:
            done.add(1)
        if any(t.source == self.sid for t in self.host.project.tools) or (self.tags and any(i.exposed for i in c.items)):
            done.add(2)
        if self._mine() and not [t for t in self._mine() if t.changed]:
            done.add(3)
        return done

    def _mine(self):
        return [t for t in self.host.project.tools if t.kind == "curated" and t.source == self.sid]

    # ---- 1. connect
    def _build_connect(self):
        w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(12)
        f, b, _ = panel(heading="Connection", note="use a read-only account")
        self.form_box = b; self.form_w = None
        btns = QHBoxLayout()
        self.b_conn = primary_button("Test connection"); self.b_conn.clicked.connect(self.test_connection)
        self.b_apply = QPushButton("Apply and reconnect"); self.b_apply.clicked.connect(self.restart)
        self.conn_msg = muted("")
        btns.addWidget(self.b_conn); btns.addWidget(self.b_apply); btns.addWidget(self.conn_msg, 1)
        b.addLayout(btns)
        v.addWidget(f)
        f2, b2, _ = panel(heading="Read-only by design")
        b2.addWidget(muted("MCX Adapter never writes to a source: SQL logins should have db_datareader only, "
                           "tools take typed parameters (no free SQL, no protocol commands) and every answer is "
                           "capped and audited. Passwords can be ${ENV_VAR} references, so they stay out of the "
                           "project file."))
        v.addWidget(f2)
        v.addStretch(1)
        return w

    def _form(self):
        if self.form_w is not None:
            self.form_box.removeWidget(self.form_w); self.form_w.deleteLater()
        c = self.cfg
        cls = ADAPTERS.get(c.type)
        w = QWidget(); fl = QFormLayout(w); fl.setContentsMargins(0, 0, 0, 0); fl.setSpacing(8)
        fl.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        name = QLineEdit(c.name); name.editingFinished.connect(lambda: self._set("name", name.text().strip()))
        isa = QLineEdit(c.isa95); isa.setPlaceholderText("Site/Area - the ISA-95 context of this source")
        isa.editingFinished.connect(lambda: self._set("isa95", isa.text().strip().strip("/")))
        en = QCheckBox("Enabled"); en.setChecked(c.enabled); en.toggled.connect(lambda on: self._set("enabled", on))
        fl.addRow("Name", name); fl.addRow("ISA-95 context", isa); fl.addRow("", en)
        for f in (cls.SCHEMA if cls else []):
            fl.addRow(f["label"], self._editor(f, c.settings.get(f["key"], f["default"])))
        fl.addRow("Poll interval (s)", self._editor({"key": "poll_s", "type": "float", "extra": (1, 3600), "tip":
                                                    "Health check / live cache refresh"}, c.settings.get("poll_s", 10)))
        self.form_w = w
        self.form_box.insertWidget(1, w)

    def _editor(self, f, value):
        k, t = f["key"], f["type"]
        put = lambda v: self._setting(k, v)   # noqa: E731
        if t == "bool":
            e = QCheckBox(); e.setChecked(bool(value)); e.toggled.connect(put)
        elif t == "choice":
            e = QComboBox()
            for val, text in (f["extra"].items() if isinstance(f["extra"], dict) else [(x, x) for x in f["extra"]]):
                e.addItem(text, val)
            e.setCurrentIndex(max(0, e.findData(value)))
            e.activated.connect(lambda _i, e=e: put(e.currentData()))
        elif t == "int":
            lo, hi = f.get("extra") or (0, 1_000_000)
            e = QSpinBox(minimum=lo, maximum=hi); e.setValue(int(value or 0)); e.valueChanged.connect(put)
        elif t == "float":
            lo, hi = f.get("extra") or (0, 1e6)
            e = QDoubleSpinBox(minimum=lo, maximum=hi, decimals=2); e.setValue(float(value or 0))
            e.valueChanged.connect(put)
        elif t == "text":
            e = QPlainTextEdit(str(value or "")); e.setMaximumHeight(80)
            e.textChanged.connect(lambda e=e: put(e.toPlainText()))
        else:
            e = QLineEdit("" if value is None else str(value))
            if t == "password":
                e.setEchoMode(QLineEdit.EchoMode.Password)
            e.editingFinished.connect(lambda e=e: put(e.text()))
        if f.get("tip"):
            e.setToolTip(f["tip"])
        return e

    def _set(self, attr, value):
        if getattr(self.cfg, attr) != value:
            setattr(self.cfg, attr, value)
            self.host.mark_dirty(structure=True)

    def _setting(self, key, value):
        if self.cfg.settings.get(key) != value:
            self.cfg.settings[key] = value
            self.host.mark_dirty()

    def test_connection(self):
        from mcx_core.adapters import make_adapter
        self.conn_msg.setText("Connecting…")
        a = make_adapter(self.cfg, self.host.project.limits)

        def fn():
            try:
                return a.health()
            finally:
                a.close()
        Worker.run(fn, self._tested)

    def _tested(self, h, err):
        if err:
            self.conn_msg.setText(f"Failed: {err}")
            return
        self.conn_msg.setText(f"✓ Connected · {h.latency_ms:.0f} ms" if h.state == "online" else f"✕ {h.message}")
        self.host.set_status(f"{self.cfg.name}: {'connected' if h.state == 'online' else h.message}",
                             "ok" if h.state == "online" else "warn")

    def restart(self):
        self.host.rt.start_source(self.sid) if self.cfg.enabled else self.host.rt.stop_source(self.sid)
        self.host.set_status(f"{self.cfg.name}: reconnecting…", "info")

    # ---- 2. discover
    def _build_discover(self):
        w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(12)
        f, b, h = panel("Discovered", "")
        self.disc_note = f.note
        self.b_disc = primary_button("Discover"); self.b_disc.clicked.connect(self.discover)
        h.insertWidget(0, self.b_disc)
        self.disc = table(["Name", "Kind", "Columns / unit", "Personal data", "Status"], 0, {1: 70, 2: 120, 3: 160, 4: 140})
        self.disc.setMinimumHeight(360)
        b.addWidget(self.disc)
        v.addWidget(f)
        return w

    def discover(self):
        self.host.set_status(f"Discovering {self.cfg.name}…", "info")
        self.host.busy(True)
        try:
            self.b_disc.setEnabled(False)
        except RuntimeError:
            pass
        Worker.run(lambda: self.host.rt.discover(self.sid), self._discovered)

    def _discovered(self, res, err):
        self.host.busy(False)
        self.b_disc.setEnabled(True)
        if err:
            self.host.set_status(f"Discovery failed: {err}", "bad")
            self.host.banners.post(f"disc:{self.sid}", f"{self.cfg.name}: discovery failed - {err}", "bad")
            return
        self.host.rt.apply_discovery(self.sid, res)
        self.host.mark_dirty(structure=True)
        self.host.set_status(f"{self.cfg.name}: {res.summary}", "ok")
        if self.step < 1:
            self.go(1)
        else:
            self.refresh()

    # ---- 3. expose
    def _build_expose(self):
        w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(12)
        h = Columns(980)
        left = QVBoxLayout(); left.setSpacing(12)
        self.k_obj, self.k_exp, self.k_risk = Kpi("Objects discovered"), Kpi("Exposed as tools"), Kpi("Highest risk")
        left.addWidget(KpiRow((self.k_obj, self.k_exp, self.k_risk), 420))
        f, b, _ = panel("Discovered items → MCP tools", "")
        self.exp_note = f.note
        self.exp = table(["", "Source object", "MCP tool", "Risk", "Profiles"], 1, {0: 34, 2: 190, 3: 50, 4: 130})
        self.exp.setMinimumHeight(330)
        self.exp.itemChanged.connect(self._exp_toggled)
        self.exp.currentItemChanged.connect(lambda cur, _p: self._exp_selected(cur))
        b.addWidget(self.exp)
        left.addWidget(f, 1)
        h.add(left, 11)
        f, b, head = panel(heading="Tool preview", title="as the AI sees it")
        self.ann = soft("🛡 readOnlyHint", "ok"); head.addWidget(self.ann)
        self.sig = QLabel(); self.sig.setObjectName("codeAccent"); self.sig.setWordWrap(True)
        self.sig.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.desc = QLabel(); self.desc.setWordWrap(True)
        self.params = table(["Parameter", "Type", "Notes"], 2, {0: 120, 1: 70})
        trow = QHBoxLayout()
        tl = QLabel("Test call"); tl.setObjectName("panelHeading")
        self.args = QLineEdit(); self.args.setObjectName("codeEdit")
        self.args.setToolTip('name="value", ... or a JSON object')
        self.args.returnPressed.connect(self.run_test)
        self.b_run = primary_button("▶  Run"); self.b_run.clicked.connect(self.run_test)
        trow.addWidget(tl); trow.addWidget(self.args, 1); trow.addWidget(self.b_run)
        self.result = QPlainTextEdit(); self.result.setObjectName("code"); self.result.setReadOnly(True)
        self.result.setMinimumHeight(170)
        self.res_note = muted("", small=False)
        for x in (self.sig, self.desc, self.params):
            b.addWidget(x)
        b.addLayout(trow); b.addWidget(self.result, 1); b.addWidget(self.res_note)
        self.preview = f
        h.add(f, 10)
        v.addWidget(h)
        return w

    def _exp_rows(self):
        """(key, object or item, tool or None) rows of the expose table."""
        c, p = self.cfg, self.host.project
        if self.tags:
            return [(it.address, it, None) for it in c.items]
        out = []
        for o in c.objects:
            t = next((t for t in p.tools if t.source == self.sid and t.object.lower() == o.name.lower()), None)
            out.append((o.name, o, t))
        out.sort(key=lambda r: (r[1].blocked, not r[1].exposed and r[2] is None, r[1].name.lower()))
        return out

    def _fill_expose(self):
        c, p = self.cfg, self.host.project
        keep = self.exp.currentItem().data(1, 256) if self.exp.currentItem() else None
        self.exp.blockSignals(True)
        self.exp.clear()
        if self.tags:
            self.exp.setHeaderLabels(["", "ISA-95 path", "Address", "Unit", "Limits"])
            for it in c.items:
                r = row(self.exp, ["", it.full_path, it.address, it.unit, "" if it.lo is None and it.hi is None
                                   else f"{it.lo if it.lo is not None else ''} … {it.hi if it.hi is not None else ''}"])
                r.setData(1, 256, it.address)
                r.setFlags(r.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                r.setCheckState(0, Qt.CheckState.Checked if it.exposed else Qt.CheckState.Unchecked)
                mono(r, 2)
                if not it.exposed:
                    for col in range(1, 5):
                        color(r, col, "muted")
            n_exp = sum(i.exposed for i in c.items)
            self.k_obj.set(fmt_int(len(c.items)), "tags / registers / topics")
            self.k_exp.set(fmt_int(n_exp), "in the ISA-95 model", "accent")
            self.k_risk.set("R1", "read only · generic tools")
            self.exp_note.setText("served by browse_model · read_tag · query_history")
        else:
            self.exp.setHeaderLabels(["", "Source object", "MCP tool", "Risk", "Profiles"])
            rows = self._exp_rows()
            for key, o, t in rows:
                tool = t.name if t is not None and (o.exposed or t.published) else (
                    "blocked by rule" if o.blocked else "not exposed")
                prof = (" · ".join(x.upper() if len(x) <= 2 else x.capitalize() for x in t.profiles)
                        if t is not None and o.exposed else (o.note or "–"))
                if t is not None and o.exposed and len(t.profiles) == len(p.profiles) and len(p.profiles) > 1:
                    prof = "All"
                risk = t.risk if t is not None else o.risk
                r = row(self.exp, ["", o.name, tool, risk, prof])
                r.setData(1, 256, key)
                if not o.blocked:
                    r.setFlags(r.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    r.setCheckState(0, Qt.CheckState.Checked if (o.exposed and t is not None) else Qt.CheckState.Unchecked)
                else:
                    r.setFlags(r.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
                    r.setData(0, Qt.ItemDataRole.CheckStateRole, Qt.CheckState.Unchecked)
                risk_cell(r, 3, risk)
                if o.exposed and t is not None:
                    color(r, 2, "", True)
                    if t.changed:
                        r.setToolTip(2, f"{t.state} - publish to make it live")
                else:
                    for col in (1, 2, 4):
                        color(r, col, "muted")
            exposed = [t for _, o, t in rows if t is not None and o.exposed]
            held = sum(1 for _, o, t in rows if o.exposed and t is not None and t.published is None and t.risk == "R2")
            blocked = sum(o.blocked for _, o, _t in rows)
            views = sum(o.kind == "view" for o in c.objects)
            self.k_obj.set(fmt_int(len(c.objects)), f"{len(c.objects) - views} tables · {views} views")
            self.k_exp.set(len(exposed), f"{held} held back · {blocked} blocked", "accent")
            top = max((t.risk for t in exposed if t.published or t.risk != "R2"), key=risk_level, default="R0")
            self.k_risk.set(top, "read only · no free SQL")
            self.exp_note.setText(f"{len(rows)} objects")
        self.exp.blockSignals(False)
        target = None
        for i in range(self.exp.topLevelItemCount()):
            if self.exp.topLevelItem(i).data(1, 256) == keep:
                target = self.exp.topLevelItem(i)
        if target is None and self.exp.topLevelItemCount():
            target = self.exp.topLevelItem(0)
        if target is not None:
            self.exp.setCurrentItem(target)
        self._exp_selected(self.exp.currentItem())

    def _exp_toggled(self, it: QTreeWidgetItem, col: int):
        if col != 0:
            return
        on = it.checkState(0) == Qt.CheckState.Checked
        key = it.data(1, 256)
        c, p = self.cfg, self.host.project
        if self.tags:
            item = next((i for i in c.items if i.address == key), None)
            if item and item.exposed != on:
                item.exposed = on
                self.host.mark_dirty(structure=True, tools=True)
            return
        o = c.object(key)
        if o is None or o.blocked:
            return
        o.exposed = on
        t = next((t for t in p.tools if t.source == self.sid and t.object.lower() == o.name.lower()), None)
        if on and t is None:
            base = "get_" + re.sub(r"[^a-z0-9]+", "_", o.name.split(".")[-1].lower()).strip("_")
            t = new_curated(p.unique_id(base, {x.name for x in p.tools}), self.sid, o, [x.id for x in p.profiles])
            p.tools.append(t)
            self.sel_tool = t.name
            if o.pii_columns():
                self.host.set_status(f"{o.name} has personal-data columns ({', '.join(sorted(o.pii_columns()))}): "
                                     "they are never returned", "warn")
        if t is not None:
            t.enabled = on
        self.host.mark_dirty(structure=True, tools=True)
        self._fill_expose()

    def _exp_selected(self, it):
        if it is None:
            self.preview.setVisible(False)
            return
        self.preview.setVisible(True)
        key, p = it.data(1, 256), self.host.project
        if self.tags:
            item = next((i for i in self.cfg.items if i.address == key), None)
            tool = p.tool("read_tag")
            self.sel_tool = "read_tag"
            if item is not None:
                self.host.select(("item", self.sid, item.address))
            self._show_tool(tool, f'path="{item.full_path if item else ""}"')
            return
        o = self.cfg.object(key)
        t = next((t for t in p.tools if t.source == self.sid and t.object.lower() == key.lower()), None)
        if t is None:
            self.sel_tool = None
            self.sig.setText(o.name if o else "")
            self.desc.setText("Not exposed. Tick it to create a curated tool from this object (typed parameters, "
                              "server-side aggregation - never free SQL)." if o and not o.blocked else
                              "Blocked by rule: system / security objects are never exposed.")
            self.params.clear()
            for c_ in (o.columns if o else []):
                r = row(self.params, [c_.name, c_.type, "personal data - never returned" if c_.pii else ""])
                mono(r, 0)
                if c_.pii:
                    color(r, 2, "warn")
            self._fit_params()
            self.args.setText(""); self.result.setPlainText(""); self.res_note.setText("")
            self.ann.setVisible(False)
            self.host.select(("object", self.sid, key) if o else None)
            return
        self.sel_tool = t.name
        self._show_tool(t, default_args(t))
        self.host.select(("tool", t.name))

    def _show_tool(self, t, args: str):
        if t is None:
            return
        a = annotations(t)
        self.ann.setVisible(True)
        self.ann.setText("🛡 readOnlyHint" if a.get("readOnlyHint") else "⚠ write")
        set_prop(self.ann, "level", "ok" if a.get("readOnlyHint") else "bad")
        self.sig.setText(signature(t))
        self.desc.setText(t.description + ("" if t.kind == "generic" else
                                           f" Dates are plant local time ({self.host.project.timezone})."
                                           " Read-only; every answer carries its source rows."))
        self.params.clear()
        sch = input_schema(t)["properties"]
        for prm in t.params:
            s = sch[prm.name]
            typ = "enum" if "enum" in s and prm.group else ("date" if prm.type == "date" else prm.type)
            notes = " · ".join(s["enum"]) if prm.group else (prm.description or "")
            if prm.required and "required" not in notes:
                notes = "required" + (", " + notes if notes else "")
            if prm.values and not prm.group:
                notes = f"{' … '.join([prm.values[0], prm.values[-1]])}" + (", " + prm.description if prm.description else "")
            r = row(self.params, [prm.name, typ, notes])
            mono(r, 0)
        self._fit_params()
        if not self.args.text() or self.args.property("tool") != t.name:
            self.args.setText(args)
            self.args.setProperty("tool", t.name)
            self.result.setPlainText("")
            self.res_note.setText("Run a test call: it uses the draft and is audited as a test." if t.changed
                                  else "Run a test call to see the answer envelope.")

    def _fit_params(self):
        from mcx_studio.widgets.common import fit_height
        fit_height(self.params, max_rows=8)

    def run_test(self):
        if self.step != 2:
            self.go(2)
        name = self.sel_tool
        if not name:
            self.host.set_status("Select an exposed tool first", "warn")
            return
        try:
            args = parse_args(self.args.text())
        except ValueError as e:
            self.res_note.setText(f"Arguments: {e}")
            return
        self.b_run.setEnabled(False)
        self.res_note.setText("Running…")
        Worker.run(lambda: self.host.test_call(name, args), lambda res, err: self._tested_call(name, res, err))

    def _tested_call(self, name, res, err):
        self.b_run.setEnabled(True)
        if err:
            self.result.setPlainText(str(err)); return
        env, error, ms = res
        self.result.setPlainText(json.dumps(env, indent=2, ensure_ascii=False, default=str))
        user = self.host.user
        if error:
            self.res_note.setText(f"✕ {error} · {ms:.0f} ms · audited as test call by {user}")
        else:
            n = len(env.get("rows", []))
            self.res_note.setText(f"{n} rows · {ms:.0f} ms · audited as test call by {user}"
                                  + (" · truncated" if env.get("truncated") else ""))

    # ---- 4. review
    def _build_review(self):
        w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(12)
        f, b, h = panel("Changes to publish", "")
        self.rev_note = f.note
        self.rev = table(["Tool", "State", "Version", "Risk", "Profiles", "Check"], 0, {1: 90, 2: 70, 3: 50, 4: 130, 5: 260})
        self.rev.setMinimumHeight(260)
        b.addWidget(self.rev)
        btn = QHBoxLayout()
        self.b_pub2 = primary_button("Publish"); self.b_pub2.clicked.connect(self.publish)
        btn.addWidget(self.b_pub2)
        btn.addWidget(muted("Publishing makes the tools live for their profiles and sends tools/list_changed to "
                            "connected clients. Versions are kept in each tool's history."), 1)
        b.addLayout(btn)
        v.addWidget(f)
        f2, b2, _ = panel("History", "")
        self.hist = table(["Time", "Tool", "Version", "By", "Note"], 4, {0: 150, 1: 190, 2: 70, 3: 110})
        self.hist.setMinimumHeight(160)
        b2.addWidget(self.hist)
        v.addWidget(f2)
        v.addStretch(1)
        return w

    def _fill_review(self):
        from mcx_core.safety import SafetyError, check_publishable
        p = self.host.project
        mine = self._mine() if not self.tags else [t for t in p.tools if t.kind == "generic"]
        self.rev.clear()
        for t in mine:
            try:
                check_publishable(t, p)
                chk, tok = "ok", "ok"
            except SafetyError as e:
                chk, tok = str(e).split(": ", 1)[-1], "warn"
            r = row(self.rev, [t.name, t.state, f"v{t.version}" + (" + draft" if t.changed and t.published else ""),
                               t.risk, ", ".join(t.profiles) or "–", chk], t.name)
            mono(r, 0); risk_cell(r, 3, t.risk)
            color(r, 1, {"published": "ok", "changed": "accent", "draft": "warn"}[t.state])
            color(r, 5, tok)
        pend = [t for t in mine if t.changed]
        self.rev_note.setText(f"{len(pend)} pending" if pend else "everything published")
        self.b_pub2.setText(f"Publish {len(pend)} tool{'s' if len(pend) != 1 else ''}" if pend else "Nothing to publish")
        self.b_pub2.setEnabled(bool(pend))
        self.hist.clear()
        hist = sorted(((h, t.name) for t in mine for h in t.history), key=lambda x: x[0].get("time", ""), reverse=True)
        for h, name in hist[:50]:
            r = row(self.hist, [h.get("time", "")[:16].replace("T", " "), name, f"v{h.get('version')}", h.get("user", ""),
                                h.get("note", "")])
            mono(r, 1)

    def publish(self):
        names = [t.name for t in self._publishable() if t.changed]
        self.host.publish(names)
        self.refresh()

    # ================================================================ refresh
    def tick(self):
        """Live state changed (health, calls): header, steps and buttons only - never the editors."""
        c = self.cfg
        if c is None:
            return
        hl = self.host.rt.health.get(self.sid, Health())
        self.title.setText(f"{type_label(c)} · {c.name}")
        st = hl.state if c.enabled else "disabled"
        s = c.settings
        extra = []
        if c.type == "sql":
            remote = s.get("dialect") != "sqlite"
            extra = [f"login {s.get('user', '')} (db_datareader)" if remote else "read-only file",
                     (s.get("host", "") + (f":{s.get('port')}" if s.get("port") else "")) if remote
                     else Path(str(s.get("database", ""))).name]
        elif c.type in ("modbus", "mqtt"):
            extra = [f"{s.get('host', '')}:{s.get('port', '')}"]
        elif c.type == "opcua":
            extra = [s.get("endpoint", "")]
        elif c.type == "files":
            extra = [s.get("path", "")]
        self.subtitle.setText(" · ".join(x for x in ["Adapter", *extra, st + (f" ({hl.message})" if hl.message else "")] if x))
        self.steps.set_state(self.step, self._done())
        pend = [t for t in self._publishable() if t.changed]
        self.b_pub.setText(f"Publish {len(pend)} tool{'s' if len(pend) != 1 else ''}" if pend else "Publish")
        self.b_pub.setEnabled(bool(pend))

    def _publishable(self):
        return self._mine() if not self.tags else [t for t in self.host.project.tools if t.kind == "generic"]

    def refresh(self):
        if self.cfg is None:
            return
        self.tick()
        if self.step == 0:
            self._form()
        elif self.step == 1:
            self._fill_discover()
        elif self.step == 2:
            self._fill_expose()
        else:
            self._fill_review()
        if self.step < 2:
            self.host.select(("source", self.sid))

    def _fill_discover(self):
        c = self.cfg
        self.disc.clear()
        if self.tags:
            self.disc.setHeaderLabels(["ISA-95 path", "Address", "Unit", "Type", "Status"])
            for it in c.items:
                r = row(self.disc, [it.full_path, it.address, it.unit, it.datatype, "exposed" if it.exposed else "hidden"])
                mono(r, 1); color(r, 4, "ok" if it.exposed else "muted")
            self.disc_note.setText(f"{len(c.items)} items · discovered {c.discovered or 'never'}")
        else:
            self.disc.setHeaderLabels(["Name", "Kind", "Columns", "Personal data", "Status"])
            for o in c.objects:
                pii = ", ".join(sorted(o.pii_columns()))
                st = "blocked by rule" if o.blocked else ("exposed" if o.exposed else "hidden (allow-list)")
                r = row(self.disc, [o.name, o.kind, str(len(o.columns)), pii or "–", st], o.name)
                mono(r, 0); color(r, 3, "warn" if pii else "muted")
                color(r, 4, "bad" if o.blocked else ("ok" if o.exposed else "muted"))
            self.disc_note.setText(f"{len(c.objects)} objects · discovered {c.discovered or 'never'}")
        self.b_disc.setText("Discover again" if (c.objects or c.items) else "Discover")
        if not (c.objects or c.items):
            row(self.disc, ["Nothing discovered yet - press Discover", "", "", "", ""])
