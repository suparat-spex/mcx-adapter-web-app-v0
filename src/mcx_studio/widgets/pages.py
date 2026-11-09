"""
pages.py - the smaller MCX Studio documents:

  ToolsPage      every tool: kind, source, risk, state, version, profiles, calls today
  ModelPage      the ISA-95 model the generic tools read (site > area > line > machine > tag)
  SafetyPage     limits, risk classes (as MCP annotations), allow-list and personal-data columns
  EndpointPage   MCP endpoint (Streamable HTTP / stdio), authentication, API keys, audit forwarding
  AuditPage      the hash-chained audit log: filter, verify, export
  LicencePage    edition, sources used, expiry
  AlarmsPage     conditions that need someone: sources down, held-back tools, chain broken ...
"""
from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLineEdit, QPushButton, QSpinBox,
                               QDoubleSpinBox, QTreeWidget, QTreeWidgetItem, QVBoxLayout)

from mcx_core.adapters import Health, family, type_label
from mcx_core.config import Licence
from mcx_core.safety import RISK_INFO, V1_MAX_RISK, risk_level
from mcx_studio.theme.ui_kit import primary_button
from mcx_studio.widgets.common import (RESULT_TOKEN, Columns, Kpi, KpiRow, Page, color, fit_height, flow, fmt_int, mono,
                                       muted, panel, risk_cell, row, soft, table)


def _calls_by_tool(rt) -> dict:
    out: dict[str, int] = {}
    for r in rt.audit.today():
        out[r.get("tool", "")] = out.get(r.get("tool", ""), 0) + 1
    return out


# ======================================================================= tools
class ToolsPage(Page):
    def __init__(self, host):
        super().__init__(host, "Tools", "Generic tools read the ISA-95 model · curated tools answer one business question")
        self.tool("New curated tool", "plus", host.new_curated_tool)
        self.tool("Edit parameters...", "pencil", lambda: self._cur() and host.edit_tool_params(self._cur()))
        self.tool("Test call", "play", lambda: self._cur() and host.test_call_dialog(self._cur()), right=True)
        self.b_pub = self.tool("Publish", "upload", lambda: host.publish(None), right=True)
        self.tool("", "list", [("Hide (unpublish)", lambda: self._cur() and host.unpublish(self._cur())),
                               ("Delete tool...", lambda: self._cur() and host.delete_tool(self._cur()))],
                  right=True, tip="More")
        f, b, _ = panel("All tools", "")
        self.note = f.note
        self.t = table(["Tool", "Kind", "Source · object", "Risk", "State", "Version", "Profiles", "Calls today"], 0,
                       {1: 70, 2: 230, 3: 50, 4: 90, 5: 70, 6: 120, 7: 90})
        self.t.currentItemChanged.connect(lambda cur, _p: cur and host.select(("tool", cur.data(0, 256))))
        self.t.itemDoubleClicked.connect(self._open)
        b.addWidget(self.t)
        self.v.addWidget(f)
        self.v.addStretch(1)

    def _cur(self):
        it = self.t.currentItem()
        return it.data(0, 256) if it else None

    def _open(self, it, _c):
        t = self.host.project.tool(it.data(0, 256))
        if t and t.kind == "curated":
            self.host.open_source(t.source, 2, t.name)

    def refresh(self):
        p, cur = self.host.project, self._cur()
        calls = _calls_by_tool(self.host.rt)
        self.t.clear()
        for t in p.tools:
            src = p.source(t.source)
            where = f"{src.name if src else t.source} · {t.object}" if t.kind == "curated" else "ISA-95 model"
            r = row(self.t, [t.name, t.kind, where, t.risk, t.state, f"v{t.version}" if t.version else "–",
                             ", ".join(t.profiles) or "–", fmt_int(calls.get(t.name, 0))], t.name)
            mono(r, 0); risk_cell(r, 3, t.risk)
            color(r, 4, {"published": "ok", "changed": "accent", "draft": "warn"}[t.state])
            if not t.enabled:
                color(r, 0, "muted")
            if t.name == cur:
                self.t.setCurrentItem(r)
        fit_height(self.t, max_rows=40)
        pub = sum(t.published is not None for t in p.tools)
        self.note.setText(f"{pub} published · {len(p.pending())} pending")
        self.b_pub.setText(f"Publish ({len(p.pending())})" if p.pending() else "Publish")
        self.b_pub.setEnabled(bool(p.pending()))


# ======================================================================= model
class ModelPage(Page):
    def __init__(self, host):
        super().__init__(host, "Plant model · ISA-95", "Site / area / line / machine - what browse_model, read_tag and "
                                                         "query_history see")
        self.tool("Refresh", "refresh", self.refresh)
        self.tool("Expand all", "expand", lambda: self.tree.expandAll())
        self.filter = QLineEdit(); self.filter.setPlaceholderText("Filter paths"); self.filter.setClearButtonEnabled(True)
        self.filter.setMaximumWidth(260)
        self.filter.textChanged.connect(self.refresh)
        self.bar_l.addWidget(self.filter)
        f, b, _ = panel("Model", "")
        self.note = f.note
        self.tree = QTreeWidget(); self.tree.setObjectName("table")
        self.tree.setHeaderLabels(["Name", "Level / unit", "Limits", "Source", "Address"])
        for i, w in enumerate((300, 110, 110, 130)):
            self.tree.header().resizeSection(i, w)
        self.tree.setMinimumHeight(520)
        self.tree.currentItemChanged.connect(self._sel)
        b.addWidget(self.tree)
        self.v.addWidget(f)

    def _sel(self, cur, _p):
        d = cur.data(0, 256) if cur else None
        if d:
            self.host.select(("item", d[0], d[1]))

    def refresh(self):
        m, flt = self.host.rt.model, self.filter.text().strip().lower()
        self.tree.clear()

        def add(parent, node):
            for c in sorted(node.children.values(), key=lambda n: n.name.lower()):
                it = QTreeWidgetItem([c.name, c.level, "", "", ""])
                color(it, 1, "muted")
                (parent.addChild(it) if parent else self.tree.addTopLevelItem(it))
                add(it, c)
                if flt and it.childCount() == 0 and flt not in c.path.lower():
                    (parent.removeChild(it) if parent else self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(it)))
            for sid, item in node.items:
                if flt and flt not in item.full_path.lower():
                    continue
                lim = "" if item.lo is None and item.hi is None else f"{item.lo if item.lo is not None else ''} … " \
                                                                     f"{item.hi if item.hi is not None else ''}"
                it = QTreeWidgetItem([item.name, item.unit, lim, sid, item.address])
                it.setData(0, 256, (sid, item.address))
                mono(it, 0); mono(it, 4); color(it, 4, "muted")
                (parent.addChild(it) if parent else self.tree.addTopLevelItem(it))
        add(None, m.root)
        self.tree.expandToDepth(2 if not flt else 6)
        self.note.setText(f"{fmt_int(len(m))} tags · {len(m.equipment())} machines")


# ======================================================================= safety
class SafetyPage(Page):
    def __init__(self, host):
        super().__init__(host, "Safety & limits", "MCX Adapter enforces its own limits at the source - the gateway "
                                                    "can add its policy on top")
        self.tool("Refresh", "refresh", self.refresh)
        h = Columns(1000)
        left, right = QVBoxLayout(), QVBoxLayout()
        f, b, _ = panel("Limits", "applied to every client")
        fl = QFormLayout(); fl.setSpacing(8)
        self.e = {}
        for key, label, lo, hi, kind, tip in (
                ("max_rows", "Max rows per answer", 1, 100000, int, "More rows -> truncated: true and a hint to narrow"),
                ("max_days", "Max days per call", 1, 3660, int, "Date range of history / curated calls"),
                ("timeout_s", "Timeout (s)", 1, 300, float, "Per source call"),
                ("rate_per_min", "Calls per minute per client", 0, 100000, int, "0 = no limit"),
                ("cache_s", "Live value cache (s)", 0, 3600, float, "Short local cache for read_tag")):
            w = QSpinBox(minimum=lo, maximum=hi) if kind is int else QDoubleSpinBox(minimum=lo, maximum=hi, decimals=1)
            w.setToolTip(tip)
            w.valueChanged.connect(lambda v, k=key, kd=kind: self._set(k, kd(v)))
            self.e[key] = w
            fl.addRow(label, w)
        b.addLayout(fl)
        b.addWidget(flow([soft("🛡 Read-only sources", "ok"), soft("✕ No free SQL", "ok"), soft("✕ No PLC writes", "ok")]))
        left.addWidget(f)
        f, b, _ = panel("Risk classes", f"v1 serves R0–{V1_MAX_RISK[1]}")
        self.risk = table(["Class", "Meaning", "MCP annotations", "v1"], 1, {0: 60, 2: 200, 3: 160})
        b.addWidget(self.risk)
        left.addWidget(f); left.addStretch(1)
        f, b, _ = panel("Allow-list and personal data", "new objects start hidden")
        self.allow = QTreeWidget(); self.allow.setObjectName("table")
        self.allow.setHeaderLabels(["Object / column", "Status", "Personal data"])
        self.allow.header().resizeSection(0, 280); self.allow.header().resizeSection(1, 150)
        self.allow.setMinimumHeight(460)
        self.allow.itemChanged.connect(self._pii_toggled)
        b.addWidget(self.allow)
        b.addWidget(muted("Tick a column as personal data: it is never selected or returned, and a tool that uses it "
                          "cannot be published."))
        right.addWidget(f); right.addStretch(1)
        h.add(left); h.add(right)
        self.v.addWidget(h)

    def _set(self, k, v):
        lim = self.host.project.limits
        if getattr(lim, k) != v:
            setattr(lim, k, v)
            self.host.mark_dirty()

    def _pii_toggled(self, it, col):
        d = it.data(0, 256)
        if not d or col != 2:
            return
        sid, oname, cname = d
        o = self.host.project.source(sid).object(oname)
        c = o.column(cname)
        on = it.checkState(2) == Qt.CheckState.Checked
        if c and c.pii != on:
            c.pii = on
            self.host.mark_dirty(tools=True)

    def refresh(self):
        lim = self.host.project.limits
        for k, w in self.e.items():
            w.blockSignals(True); w.setValue(getattr(lim, k)); w.blockSignals(False)
        self.risk.clear()
        ann = {0: "readOnlyHint: true", 1: "readOnlyHint: true", 2: "readOnlyHint: true · sensitive",
               3: "readOnlyHint: true", 4: "readOnlyHint: false", 5: "destructiveHint: true"}
        v1 = {0: "On", 1: "On", 2: "Off until enabled per tool", 3: "On (built-in models)", 4: "Not in v1",
              5: "Not in v1"}
        for k, (name, meaning) in RISK_INFO.items():
            lv = risk_level(k)
            r = row(self.risk, [k, f"{name} - {meaning}", ann[lv], v1[lv]])
            risk_cell(r, 0, k); color(r, 3, "ok" if lv in (0, 1, 3) else ("warn" if lv == 2 else "muted"))
        fit_height(self.risk)
        self.allow.blockSignals(True)
        self.allow.clear()
        for s in self.host.project.sources:
            if family(s) != "table":
                continue
            top = QTreeWidgetItem([f"{s.name}  ({type_label(s)})", "", ""])
            color(top, 0, "", True)
            self.allow.addTopLevelItem(top)
            for o in s.objects:
                st = "blocked by rule" if o.blocked else ("exposed" if o.exposed else "hidden")
                oi = QTreeWidgetItem([o.name, st, ", ".join(sorted(o.pii_columns())) or ""])
                color(oi, 1, "bad" if o.blocked else ("ok" if o.exposed else "muted")); color(oi, 2, "warn")
                mono(oi, 0)
                top.addChild(oi)
                for c in o.columns:
                    ci = QTreeWidgetItem([c.name, c.type, ""])
                    ci.setData(0, 256, (s.id, o.name, c.name))
                    ci.setFlags(ci.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    ci.setCheckState(2, Qt.CheckState.Checked if c.pii else Qt.CheckState.Unchecked)
                    mono(ci, 0); color(ci, 1, "muted")
                    oi.addChild(ci)
            top.setExpanded(True)
        self.allow.blockSignals(False)


# ======================================================================= endpoint
class EndpointPage(Page):
    def __init__(self, host):
        super().__init__(host, "MCP endpoint", "Standard MCP server · works with any gateway or AI platform")
        self.b_srv = self.tool("Start server", "play", host.toggle_server)
        self.tool("Copy URL", "copy", lambda: host.copy_text(host.server_url()), right=True)
        h = Columns(1000)
        left, right = QVBoxLayout(), QVBoxLayout()
        f, b, _ = panel("Server", "changes apply when the server restarts")
        fl = QFormLayout(); fl.setSpacing(8)
        ep = host.project.endpoint
        self.host_e = QLineEdit(); self.port_e = QSpinBox(minimum=1, maximum=65535)
        self.path_e = QLineEdit(); self.pub_e = QLineEdit(); self.pub_e.setPlaceholderText("https://plant-mcx:8811/mcp (behind a proxy)")
        self.cert_e = QLineEdit(); self.key_e = QLineEdit()
        self.http_c = QCheckBox("Streamable HTTP"); self.stdio_c = QCheckBox("stdio (Claude Desktop, .mcpb)")
        for lbl, w in (("Listen address", self.host_e), ("Port", self.port_e), ("Path", self.path_e),
                       ("Public URL", self.pub_e), ("TLS certificate (PEM)", self.cert_e), ("TLS key (PEM)", self.key_e),
                       ("Transports", self.http_c), ("", self.stdio_c)):
            fl.addRow(lbl, w)
        for w, attr in ((self.host_e, "host"), (self.path_e, "path"), (self.pub_e, "public_url"),
                        (self.cert_e, "tls_cert"), (self.key_e, "tls_key")):
            w.editingFinished.connect(lambda w=w, a=attr: self._set(ep, a, w.text().strip()))
        self.port_e.valueChanged.connect(lambda v: self._set(ep, "port", v))
        self.http_c.toggled.connect(lambda on: self._set(ep, "http", on))
        self.stdio_c.toggled.connect(lambda on: self._set(ep, "stdio", on))
        b.addLayout(fl)
        self.srv_state = muted("")
        b.addWidget(self.srv_state)
        left.addWidget(f)
        f, b, _ = panel("Audit forwarding", "")
        fl2 = QFormLayout()
        self.sys_e = QLineEdit(); self.sys_e.setPlaceholderText("SIEM host (empty = off)")
        self.sys_e.editingFinished.connect(lambda: self._set(host.project.audit, "syslog_host", self.sys_e.text().strip()))
        fl2.addRow("Syslog (UDP)", self.sys_e)
        b.addLayout(fl2)
        b.addWidget(muted("Every audit record is also sent as RFC 5424 syslog; the local file stays the record."))
        left.addWidget(f); left.addStretch(1)
        f, b, _ = panel("Authentication", "")
        self.mode = QComboBox()
        for k, t in (("api_key", "API keys (Bearer token)"), ("none", "None - local clients only"),
                     ("oauth", "OAuth 2.1 bearer tokens (planned)")):
            self.mode.addItem(t, k)
        self.mode.activated.connect(lambda _i: self._set(host.project.auth, "mode", self.mode.currentData()))
        b.addWidget(self.mode)
        kb = QHBoxLayout()
        nb = primary_button("New key"); nb.clicked.connect(lambda: host.rotate_key(None, None, ""))
        rb = QPushButton("Rotate"); rb.clicked.connect(lambda: self._key() and host.rotate_key(self._key(), None, ""))
        db = QPushButton("Delete"); db.clicked.connect(lambda: self._key() and host.delete_key(self._key()))
        for x in (nb, rb, db):
            kb.addWidget(x)
        kb.addStretch(1)
        b.addLayout(kb)
        self.keys = table(["Key", "Profile", "Client", "Secret", "Created"], 2, {0: 110, 1: 70, 3: 80})
        b.addWidget(self.keys)
        b.addWidget(muted("Only a SHA-256 hash of each key is stored; the secret is shown once. A key bound to a "
                          "profile can use only that profile's endpoint."))
        right.addWidget(f); right.addStretch(1)
        h.add(left); h.add(right)
        self.v.addWidget(h)

    def _key(self):
        it = self.keys.currentItem()
        return it.data(0, 256) if it else None

    def _set(self, obj, attr, v):
        if getattr(obj, attr) != v:
            setattr(obj, attr, v)
            self.host.mark_dirty()

    def tick(self):
        srv = self.host.server
        on = srv is not None and srv.running
        self.b_srv.setText("Stop server" if on else "Start server")
        self.host.reg_icon(self.b_srv, "square" if on else "play")
        self.srv_state.setText(f"● Running · {self.host.server_url()} · {len(srv.sessions)} sessions" if on else
                               ("○ Stopped" + (f" · {srv.error}" if srv is not None and srv.error else "")))

    def refresh(self):
        p = self.host.project
        ep = p.endpoint
        for w, v in ((self.host_e, ep.host), (self.path_e, ep.path), (self.pub_e, ep.public_url),
                     (self.cert_e, ep.tls_cert), (self.key_e, ep.tls_key), (self.sys_e, p.audit.syslog_host)):
            w.setText(v)
        for w, v in ((self.port_e, ep.port), (self.http_c, ep.http), (self.stdio_c, ep.stdio)):
            w.blockSignals(True)
            w.setValue(v) if isinstance(w, QSpinBox) else w.setChecked(v)
            w.blockSignals(False)
        self.mode.setCurrentIndex(max(0, self.mode.findData(p.auth.mode)))
        self.keys.clear()
        for k in p.auth.api_keys:
            r = row(self.keys, [k.id, k.profile or "any", k.client or k.name, f"••••{k.last4}", k.created[:10]], k.id)
            mono(r, 0); mono(r, 3)
        fit_height(self.keys, max_rows=10)
        self.tick()


# ======================================================================= audit
class AuditPage(Page):
    def __init__(self, host):
        super().__init__(host, "Audit log", "")
        self.tool("Verify chain", "shield-check", host.verify_audit)
        self.tool("Export CSV...", "download", host.export_audit)
        self.tool("Open folder", "folder-open", lambda: host.open_folder(host.rt.audit.folder), right=True)
        self.res = QComboBox(); self.res.addItems(["All results", "ok", "blocked", "error", "truncated"])
        self.res.currentIndexChanged.connect(self.refresh)
        self.bar_l.insertWidget(3, self.res)
        self.k_calls, self.k_block, self.k_err, self.k_chain = Kpi("Calls today"), Kpi("Blocked"), Kpi("Errors"), Kpi("Hash chain")
        self.v.addWidget(KpiRow((self.k_calls, self.k_block, self.k_err, self.k_chain)))
        f, b, _ = panel("Records", "newest first · one JSON line per call")
        self.t = table(["Time", "Client", "Profile", "Via", "Tool", "Arguments", "Rows", "ms", "Result"], 5,
                       {0: 140, 1: 120, 2: 60, 3: 60, 4: 170, 6: 50, 7: 60, 8: 80})
        self.t.setMinimumHeight(480)
        b.addWidget(self.t)
        self.v.addWidget(f)
        self.chain = None

    def refresh(self):
        import json
        rt = self.host.rt
        self.subtitle.setText(f"{rt.audit.folder} · hash-chained, exportable to your SIEM")
        flt = self.res.currentText()
        recs = [r for r in rt.audit.recent if flt == "All results" or r.get("result") == flt][::-1][:500]
        self.t.clear()
        for r in recs:
            args = json.dumps(r.get("args", {}), ensure_ascii=False)
            it = row(self.t, [str(r.get("time", "")).replace("T", " ")[:19], r.get("client", ""), r.get("profile", ""),
                              r.get("via", ""), r.get("tool", ""), args, r.get("rows", ""), r.get("ms", ""),
                              r.get("result", "") + (" (test)" if r.get("test") else "")])
            mono(it, 4); mono(it, 5); color(it, 8, RESULT_TOKEN.get(r.get("result"), "muted"))
            it.setToolTip(5, args); it.setToolTip(8, r.get("error", ""))
        today = rt.audit.today()
        self.k_calls.set(fmt_int(sum(r.get("tool") != "publish" for r in today)), "", "accent")
        self.k_block.set(sum(r.get("result") == "blocked" for r in today), "range, rows, rate, profile")
        self.k_err.set(sum(r.get("result") == "error" for r in today), "source errors")
        if self.chain is None:
            self.chain = rt.audit.verify()
        ok, n, msg = self.chain
        self.k_chain.set("Verified" if ok else "Broken", msg, "ok" if ok else "bad")

    def set_chain(self, res):
        self.chain = res
        self.refresh()


# ======================================================================= licence
class LicencePage(Page):
    def __init__(self, host):
        super().__init__(host, "Licence", "Offline licence file bound to this machine - no internet needed in the plant")
        f, b, _ = panel("Edition", "")
        fl = QFormLayout(); fl.setSpacing(8)
        self.ed = QComboBox(); self.ed.addItems(list(Licence.EDITIONS))
        self.ed.activated.connect(self._edition)
        self.holder = QLineEdit(); self.holder.editingFinished.connect(
            lambda: self._set("holder", self.holder.text().strip()))
        self.exp = QLineEdit(); self.exp.setPlaceholderText("YYYY-MM-DD")
        self.exp.editingFinished.connect(lambda: self._set("expires", self.exp.text().strip()))
        fl.addRow("Edition", self.ed); fl.addRow("Licensed to", self.holder); fl.addRow("Expires", self.exp)
        b.addLayout(fl)
        self.used = muted("")
        b.addWidget(self.used)
        self.v.addWidget(f)
        f, b, _ = panel("Editions", "per data source connection")
        t = table(["Edition", "Sources", "Risk classes", "Tool profiles"], 0, {1: 100, 2: 120, 3: 120})
        for ed, (n, r, prof) in Licence.EDITIONS.items():
            row(t, [ed, str(n or "unlimited"), f"R0–{r}", "yes" if prof else "–"])
        fit_height(t)
        b.addWidget(t)
        b.addWidget(muted("An MCX Adapter licence credits toward MCX Gateway. Licence files are checked offline "
                          "(licence key import is part of the installer work)."))
        self.v.addWidget(f)
        self.v.addStretch(1)

    def _edition(self, _i):
        self.host.project.licence.set_edition(self.ed.currentText())
        self.host.mark_dirty(tools=True)
        self.refresh()

    def _set(self, k, v):
        lic = self.host.project.licence
        if getattr(lic, k) != v:
            setattr(lic, k, v)
            self.host.mark_dirty()

    def refresh(self):
        lic, p = self.host.project.licence, self.host.project
        self.ed.setCurrentText(lic.edition)
        self.holder.setText(lic.holder); self.exp.setText(lic.expires)
        n = len(p.sources)
        cap = lic.max_sources or "unlimited"
        self.used.setText(f"{n} of {cap} sources used · risk up to {lic.max_risk} · tool profiles "
                          f"{'included' if lic.profiles else 'not included'}")


# ======================================================================= alarms
class AlarmsPage(Page):
    def __init__(self, host):
        super().__init__(host, "Alarms", "Conditions that need someone - background events never open a pop-up")
        self.tool("Refresh", "refresh", self.refresh)
        f, b, _ = panel("Active", "")
        self.note = f.note
        self.t = table(["Level", "Where", "Condition", "Since"], 2, {0: 70, 1: 200, 3: 140})
        self.t.itemDoubleClicked.connect(self._open)
        b.addWidget(self.t)
        self.v.addWidget(f)
        self.v.addStretch(1)

    def _open(self, it, _c):
        d = it.data(0, 256)
        if d and d[0] == "source":
            self.host.open_source(d[1])
        elif d:
            self.host.open_doc(d[0])

    def items(self) -> list[tuple]:
        """(level, where, condition, since, target) - also used for the toolbar counter."""
        h, p, rt = self.host, self.host.project, self.host.rt
        out = []
        for s in p.sources:
            hl = rt.health.get(s.id, Health())
            if s.enabled and hl.state == "error":
                out.append(("warn", f"{type_label(s)} · {s.name}", hl.message or "offline",
                            time.strftime("%H:%M", time.localtime(hl.checked)) if hl.checked else "", ("source", s.id)))
        for t in p.tools:
            if t.published is None and t.risk == "R2":
                out.append(("info", t.name, "held back: R2 personal data (needs an admin decision)", "", ("tools",)))
        chain = getattr(h.pages.get("audit"), "chain", None)
        if chain and not chain[0]:
            out.append(("bad", "Audit log", chain[2], "", ("audit",)))
        srv = h.server
        if srv is not None and srv.error:
            out.append(("bad", "MCP endpoint", srv.error, "", ("endpoint",)))
        lic = p.licence
        if lic.max_sources and len(p.sources) > lic.max_sources:
            out.append(("warn", "Licence", f"{len(p.sources)} sources, {lic.edition} allows {lic.max_sources}", "",
                        ("licence",)))
        return out

    def refresh(self):
        self.t.clear()
        items = self.items()
        for lvl, where, cond, since, target in items:
            r = row(self.t, [{"bad": "▲ High", "warn": "▲ Warning", "info": "● Info"}[lvl], where, cond, since], target)
            color(r, 0, lvl, True)
        if not items:
            row(self.t, ["", "", "No active alarms", ""])
        fit_height(self.t, max_rows=20)
        self.note.setText(f"{len(items)} active")
