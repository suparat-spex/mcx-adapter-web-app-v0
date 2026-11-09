"""
profiles_page.py - Tool profiles: one endpoint per team (/mcp/eng, /mcp/qe ...), a gateway sees only
the tools in its profile. Left: tools x profiles (click a cell to add / remove - a draft change until
Publish). Right: Connect a client - settings and steps per platform for the selected profile.
"""
from __future__ import annotations

import json
import sys
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QButtonGroup, QHBoxLayout, QLabel, QPushButton

from mcx_core.safety import visible
from mcx_studio.theme.ui_kit import primary_button, set_prop
from mcx_studio.widgets.common import (PLATFORMS, Columns, Page, Worker, color, fit_height, mono, muted, panel, risk_cell,
                                       row, soft, table)

STEPS = {
    "Copilot Studio": [
        "In your agent, add a new tool of type <b>Model Context Protocol</b>.",
        "Paste the server URL and choose <b>API key</b> authentication (header <code>Authorization</code>, "
        "value <code>Bearer &lt;key&gt;</code>).",
        "Enter the key, then <b>Create</b>.",
        "Copilot lists the {n} tools of profile <code>{p}</code>; every call shows up in this audit log."],
    "ContextForge": [
        "In ContextForge Admin &gt; <b>Gateways / MCP Servers</b>, add a server.",
        "Transport <b>Streamable HTTP</b>, URL as below, authentication <b>Bearer token</b> with the key.",
        "Put the {n} tools of profile <code>{p}</code> into a virtual server for the team.",
        "ContextForge users and the adapter's own limits both apply; calls appear in this audit log."],
    "Docker MCP Gateway": [
        "Add a catalog entry for a remote server (Copy settings gives the YAML).",
        "Store the key as a gateway secret: <code>docker mcp secret set mcx.api_key=&lt;key&gt;</code>.",
        "Enable the server: <code>docker mcp server enable mcx-{p}</code>.",
        "Clients of the gateway see the {n} tools of profile <code>{p}</code>."],
    "Claude Desktop": [
        "Claude Desktop runs the adapter locally over <b>stdio</b> (no key needed on this PC).",
        "Copy settings and add them to <code>claude_desktop_config.json</code> (Settings &gt; Developer &gt; Edit config).",
        "Restart Claude Desktop; the {n} tools of profile <code>{p}</code> appear under the tools menu.",
        "Remote use (claude.ai): add a custom connector with the server URL over HTTPS."],
}


class ProfilesPage(Page):
    def __init__(self, host):
        super().__init__(host, "Tool profiles", "One endpoint per team · a gateway sees only the tools in its profile")
        self.profile = None
        self.platform = PLATFORMS[0]
        self.last_test = ""
        self.tool("New profile", "plus", host.new_profile)
        self.b_pub = self.tool("Publish changes", "upload", lambda: host.publish(None), right=True)
        self.tool("", "list", [("Delete profile...", lambda: self.profile and host.delete_profile(self.profile))],
                  right=True, tip="More")
        h = Columns(1000)
        f, b, _ = panel("Tools in each profile", "")
        self.note = f.note
        self.grid = table(["Tool", "Risk"], 0, {1: 50})
        self.grid.itemClicked.connect(self._cell_clicked)
        self.grid.currentItemChanged.connect(lambda cur, _p: cur and self.host.select(("tool", cur.data(0, 256))))
        self.held = muted("")
        b.addWidget(self.grid); b.addWidget(self.held); b.addStretch(1)
        h.add(f, 9)
        # ---- connect a client
        f, b, head = panel(heading="⇄  Connect a client")
        self.cc_prof = QLabel(); self.cc_prof.setProperty("muted", True); head.insertWidget(1, self.cc_prof)
        self.cc_state = soft("", "ok"); head.addWidget(self.cc_state)
        seg = QHBoxLayout(); seg.setSpacing(6)
        self.seg = QButtonGroup(self)
        for i, name in enumerate(PLATFORMS):
            btn = QPushButton(name); btn.setObjectName("seg"); btn.setCheckable(True); btn.setChecked(i == 0)
            self.seg.addButton(btn, i); seg.addWidget(btn)
        seg.addStretch(1)
        self.seg.idClicked.connect(lambda i: (setattr(self, "platform", PLATFORMS[i]), self.refresh()))
        b.addLayout(seg)
        self.settings = table(["Setting", "Value"], 1, {0: 150})
        b.addWidget(self.settings)
        acts = QHBoxLayout(); acts.setSpacing(6)
        self.b_copy = primary_button("Copy settings"); self.b_copy.clicked.connect(self.copy_settings)
        self.b_rot = QPushButton("Rotate key"); self.b_rot.clicked.connect(self.rotate)
        self.b_testc = QPushButton("▷  Test connection"); self.b_testc.clicked.connect(self.test)
        for x in (self.b_copy, self.b_rot, self.b_testc):
            acts.addWidget(x)
        acts.addStretch(1)
        b.addLayout(acts)
        self.steps_title = QLabel(); self.steps_title.setObjectName("panelHeading")
        self.steps = QLabel(); self.steps.setWordWrap(True); self.steps.setTextFormat(Qt.TextFormat.RichText)
        self.test_note = muted("")
        for x in (self.steps_title, self.steps, self.test_note):
            b.addWidget(x)
        b.addStretch(1)
        h.add(f, 10)
        self.v.addWidget(h)
        self.v.addStretch(1)

    # ---------------------------------------------------------------- data
    def set_profile(self, pid):
        self.profile = pid
        self.refresh()

    def _key(self):
        return next((k for k in self.host.project.auth.api_keys if k.profile == self.profile), None)

    def refresh(self):
        p = self.host.project
        if self.profile is None or p.profile(self.profile) is None:
            self.profile = p.profiles[0].id if p.profiles else None
        profs = p.profiles
        self.grid.setColumnCount(2 + len(profs))
        self.grid.setHeaderLabels(["Tool", "Risk"] + [x.id for x in profs])
        for i in range(len(profs)):
            self.grid.header().resizeSection(2 + i, 64)
        self.grid.clear()
        pub = held = 0
        for t in p.tools:
            r = row(self.grid, [t.name, t.risk] + ["" for _ in profs], t.name)
            mono(r, 0); risk_cell(r, 1, t.risk)
            live = t.live()
            for i, pr in enumerate(profs):
                on = pr.id in t.profiles
                listed = live is not None and visible(live, pr, p.licence)
                mark = "✓" if on else "–"
                r.setText(2 + i, mark)
                r.setTextAlignment(2 + i, Qt.AlignmentFlag.AlignCenter)
                color(r, 2 + i, "accent" if on and (listed or t.changed) else "muted", on)
                tip = ("listed" if listed else "not listed: " + (
                    "not published yet" if live is None else f"risk above {pr.max_risk}" if on else "not in profile"))
                r.setToolTip(2 + i, f"{pr.id}: {tip}" + (" · click to change (draft until Publish)"))
            if t.published is None and t.risk == "R2":
                held += 1
                for c in range(2 + len(profs)):
                    color(r, c, "muted")
            elif t.published is not None:
                pub += 1
        fit_height(self.grid, max_rows=30)
        self.note.setText(f"{pub} published · {held} held back")
        hb = [t.name for t in p.tools if t.published is None and t.risk == "R2"]
        self.held.setText(f"Held back: {', '.join(hb)} (R2 personal data)" if hb else "")
        pend = p.pending()
        self.b_pub.setText(f"Publish changes ({len(pend)})" if pend else "Publish changes")
        self.b_pub.setEnabled(bool(pend))
        self._fill_connect()

    def _fill_connect(self):
        p, pid = self.host.project, self.profile
        self.cc_prof.setText(f"profile {pid}" if pid else "no profile")
        srv = self.host.server
        running = srv is not None and srv.running
        self.cc_state.setText("✓ Server running" if running else "Server stopped")
        set_prop(self.cc_state, "level", "ok" if running else "warn")
        url = self.host.server_url(pid or "")
        k = self._key()
        n = len(self.host.rt.live_tools(pid)) if pid else 0
        self.settings.clear()
        if self.platform == "Claude Desktop":
            rows = [("Transport", "stdio (local)"), ("Command", f"{_py()} -m mcx_core stdio"),
                    ("Project", self.host.project_path or "(save the project first)"), ("Profile", pid or "-"),
                    ("Remote (claude.ai)", url)]
        else:
            auth = {"api_key": "API key · Bearer token", "none": "None (local only)",
                    "oauth": "OAuth 2.1 (planned)"}[p.auth.mode]
            rows = [("Server URL", url), ("Transport", "Streamable HTTP"), ("Authentication", auth),
                    ("Key", f"{k.id}  ·  ••••{k.last4}" if k else "none - Rotate key creates one"),
                    ("Key secret", "•••••••••••• (shown once)" if k else "–")]
        for a, b in rows:
            r = row(self.settings, [a, b])
            color(r, 0, "muted")
            if a in ("Server URL", "Command", "Key", "Remote (claude.ai)"):
                mono(r, 1)
        fit_height(self.settings, max_rows=6)
        self.b_rot.setText("Rotate key" if k else "Create key")
        self.b_rot.setEnabled(self.platform != "Claude Desktop" and p.auth.mode == "api_key" and bool(pid))
        self.steps_title.setText(f"Steps in {self.platform}")
        self.steps.setText("<ol style='margin-left:-20px'>" + "".join(
            f"<li>{s.format(n=n, p=pid)}</li>" for s in STEPS[self.platform]) + "</ol>")
        self.test_note.setText(self.last_test or "Not tested yet · tools/list_changed is sent after every publish")

    # ---------------------------------------------------------------- actions
    def _cell_clicked(self, it, col):
        profs = self.host.project.profiles
        if col < 2 or col - 2 >= len(profs):
            return
        t = self.host.project.tool(it.data(0, 256))
        pid = profs[col - 2].id
        t.profiles = [x for x in t.profiles if x != pid] if pid in t.profiles else t.profiles + [pid]
        self.host.mark_dirty(tools=True)
        self.host.set_status(f"{t.name}: {'added to' if pid in t.profiles else 'removed from'} {pid} "
                             "- Publish to make it live", "info")
        self.refresh()

    def copy_settings(self):
        pid, url, k = self.profile or "", self.host.server_url(self.profile or ""), self._key()
        key = "<your API key>"
        if self.platform == "Claude Desktop":
            text = json.dumps({"mcpServers": {f"mcx-{pid}": {
                "command": _py(), "args": ["-m", "mcx_core", "stdio", self.host.project_path or "plant.mcx",
                                           "--profile", pid]}}}, indent=2)
        elif self.platform == "ContextForge":
            text = json.dumps({"name": f"mcx-{pid}", "url": url, "transport": "STREAMABLEHTTP",
                               "auth_type": "bearer", "auth_token": key,
                               "description": f"MCX Adapter - profile {pid}"}, indent=2)
        elif self.platform == "Docker MCP Gateway":
            text = (f"registry:\n  mcx-{pid}:\n    title: MCX Adapter ({pid})\n    type: remote\n"
                    f"    remote:\n      transport_type: streamable-http\n      url: {url}\n"
                    f"      headers:\n        Authorization: \"Bearer ${{secrets.mcx.api_key}}\"\n")
        else:
            text = f"Server URL: {url}\nTransport: Streamable HTTP\nAuthorization: Bearer {key}\n"
        if k:
            text += "" if self.platform != "Copilot Studio" else f"Key id: {k.id} (secret shown only when created / rotated)\n"
        QApplication.clipboard().setText(text)
        self.host.toast.popup(f"{self.platform} settings copied")

    def rotate(self):
        if not self.profile:
            return
        k = self._key()
        self.host.rotate_key(k.id if k else None, self.profile, self.platform)
        self.refresh()

    def test(self):
        pid = self.profile
        self.test_note.setText("Testing…")
        Worker.run(lambda: self.host.test_endpoint(pid), self._tested)

    def _tested(self, res, err):
        t = time.strftime("%H:%M")
        if err:
            self.last_test = f"Last test {t} · ✕ {err}"
        else:
            self.last_test = f"Last test {t} · {res}"
        self.test_note.setText(self.last_test)


def _py() -> str:
    return sys.executable.replace("\\", "/")

