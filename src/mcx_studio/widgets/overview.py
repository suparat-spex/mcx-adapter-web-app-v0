"""overview.py - Overview document: is the adapter healthy, who calls it, what is blocked."""
from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout

from mcx_core.adapters import Health, type_label
from mcx_core.safety import RISK_INFO
from mcx_studio.widgets.common import (CLIENT_KIND, RESULT_TOKEN, STATE_TOKEN, Columns, Kpi, KpiRow, Page, color, fit_height, flow, fmt_int, mono,
                                       panel, row, soft, table)
from mcx_studio.theme.ui_kit import EmptyState


class OverviewPage(Page):
    def __init__(self, host):
        super().__init__(host, "MCX Adapter")
        self.tool("Refresh", "refresh", self.refresh)
        self.tool("Export report", "share", host.export_report, right=True)
        self.tool("", "list", [("Verify audit chain", host.verify_audit), ("Open project folder", host.open_folder)],
                  right=True, tip="More")
        self.empty = EmptyState("🔌", "No sources yet",
                                "Add a data source - SQL Server, OPC UA, Modbus, MQTT, Excel - then discover it and "
                                "expose it as MCP tools.", "Add source", host.add_source)
        self.v.addWidget(self.empty)
        self.k_src, self.k_tools, self.k_calls, self.k_block = (Kpi("Sources online"), Kpi("Tools published"),
                                                                Kpi("MCP calls today"), Kpi("Blocked by limits"))
        self.v.addWidget(KpiRow((self.k_src, self.k_tools, self.k_calls, self.k_block)))
        grid = Columns(900)
        left, right = QVBoxLayout(), QVBoxLayout()
        left.setSpacing(12); right.setSpacing(12)
        f, b, _ = panel("Connected clients", "any MCP gateway or AI platform")
        self.clients = table(["Client", "Via", "Profile", "Auth", "Calls today"], 0, {1: 110, 2: 70, 3: 90, 4: 90})
        b.addWidget(self.clients); left.addWidget(f)
        f, b, _ = panel("Sources", "read-only connections")
        self.sources = table(["Source", "Type", "Status", "Latency", "Discovered"], 0, {1: 100, 2: 110, 3: 70, 4: 110})
        self.sources.itemDoubleClicked.connect(lambda it, _c: host.open_source(it.data(0, 256)))
        b.addWidget(self.sources); left.addWidget(f)
        left.addStretch(1)
        f, b, _ = panel("Audit log · latest", "hash-chained")
        self.audit_note = f.note
        self.audit = table(["Time", "Client", "Tool", "Rows", "Result"], 2, {0: 60, 1: 120, 3: 50, 4: 80})
        self.audit.itemDoubleClicked.connect(lambda *_: host.open_doc("audit"))
        b.addWidget(self.audit); right.addWidget(f)
        f, b, _ = panel("Safety & limits", "applied to every client")
        self.safety_box = b; self.safety_w = None
        right.addWidget(f); right.addStretch(1)
        grid.add(left); grid.add(right)
        self.v.addWidget(grid)
        self.v.addStretch(1)

    def refresh(self):
        h, rt, p = self.host, self.host.rt, self.host.project
        self.title.setText(p.name or "MCX Adapter")
        lic = p.licence
        self.subtitle.setText(f"MCP server · {h.server_url()} · licence {lic.edition} "
                              f"({lic.max_sources or 'unlimited'} sources)")
        has = bool(p.sources)
        self.empty.setVisible(not has)
        s = rt.summary()
        down = [x for x in p.sources if x.enabled and rt.health.get(x.id, Health()).state != "online"]
        self.k_src.set(f"{s['sources_online']} / {s['sources']}",
                       f"{down[0].name}: {rt.health.get(down[0].id, Health()).message or 'offline'}"[:44] if down
                       else "all online", "")
        self.k_tools.set(s["tools"], f"{s['generic']} generic · {s['curated']} curated")
        self.k_calls.set(fmt_int(s["calls_today"]), f"{s['clients']} clients · 100 % cited", "accent")
        self.k_block.set(s["blocked_today"], "range, rows, rate, profile")

        self.clients.clear()
        for c in sorted(rt.clients.values(), key=lambda c: -c.calls):
            key = next((k for k in p.auth.api_keys if k.id == c.id), None)
            platform = key.client if key else ""
            name = " · ".join(x for x in (platform, c.name if c.name not in (c.id, platform) else "") if x) or c.id
            it = row(self.clients, [name, CLIENT_KIND.get(platform, c.via), c.profile or "all", c.auth,
                                    fmt_int(c.calls)])
            color(it, 0, "", True); mono(it, 2)
        for k in p.auth.api_keys:                   # keys that never connected yet
            if k.id not in rt.clients:
                it = row(self.clients, [k.client or k.name, CLIENT_KIND.get(k.client, "–"), k.profile or "all",
                                        "API key", "0"])
                color(it, 0, "muted"); mono(it, 2)
        fit_height(self.clients, max_rows=8)

        self.sources.clear()
        for src in p.sources:
            hl = rt.health.get(src.id, Health())
            st = hl.state if src.enabled else "stopped"
            n = len(src.objects) or len(src.items)
            what = "objects" if src.objects else ("tags" if src.type in ("opcua", "sim") else
                                                  "registers" if src.type == "modbus" else "topics" if src.type == "mqtt" else "items")
            label = {"error": hl.message.split(":")[-1].strip()[:22] or "error"}.get(st, st)
            it = row(self.sources, [src.name, type_label(src), ("● " if st == "online" else "▲ " if st == "error" else "○ ") + label,
                                    f"{hl.latency_ms:.0f} ms" if hl.latency_ms else "–", f"{fmt_int(n)} {what}" if n else "–"],
                     src.id)
            color(it, 0, "", True); color(it, 2, STATE_TOKEN.get(st, "muted"))
            it.setToolTip(2, hl.message or st)
        fit_height(self.sources, max_rows=10)

        self.audit.clear()
        recent = [r for r in rt.audit.recent if r.get("tool") != "publish"][-8:][::-1]
        for r in recent:
            it = row(self.audit, [str(r.get("time", ""))[11:16], self._client(r.get("client", "")), r.get("tool", ""),
                                  r.get("rows") if r.get("result") not in ("blocked", "error") else "–", r.get("result", "")])
            mono(it, 2); color(it, 4, RESULT_TOKEN.get(r.get("result"), "muted"))
            it.setToolTip(4, r.get("error", ""))
        if not recent:
            row(self.audit, ["", "No calls yet", "", "", ""])
        fit_height(self.audit, max_rows=8)
        self.audit_note.setText(f"hash-chained · {fmt_int(s['calls_today'])} today")

        if self.safety_w is not None:
            self.safety_box.removeWidget(self.safety_w); self.safety_w.deleteLater()
        lim = p.limits
        pii = sum(len(o.pii_columns()) for s_ in p.sources for o in s_.objects if o.exposed)
        chips = [soft("🛡 Read-only sources", "ok"), soft("✕ No free SQL", "ok"), soft("✕ No PLC writes", "ok"),
                 soft(f"Max {lim.max_rows} rows", "info"), soft(f"Max {lim.max_days} days", "info"),
                 soft(f"Timeout {lim.timeout_s:g} s", "info"), soft(f"{lim.rate_per_min} calls / min", "info"),
                 soft(f"{pii} personal-data columns hidden", "info"),
                 soft("R2–R5 tools off", "info")]
        held = [t for t in p.tools if t.published is None and t.risk == "R2"]
        if held:
            chips.append(soft(f"{len(held)} R2 tool held back", "warn"))
        self.safety_w = flow(chips)
        self.safety_box.addWidget(self.safety_w)
        self.safety_w.setToolTip("\n".join(f"{k}: {v[0]}" for k, v in RISK_INFO.items()))

    def _client(self, cid: str) -> str:
        k = next((k for k in self.host.project.auth.api_keys if k.id == cid), None)
        if k:
            return k.client or k.name
        c = self.host.rt.clients.get(cid)
        return (c.name if c and c.name else cid)[:24]
