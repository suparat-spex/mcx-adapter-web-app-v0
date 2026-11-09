"""
common.py - building blocks shared by the MCX Studio documents (colours only from theme tokens).

  Page            doc bar (left / right actions, menu) + scrolling body with a title row
  panel()         rounded panel with a muted title (left) and note (right)
  table()         flat list (QTreeWidget#table) with headers; cell helpers for risk / status colours
  Kpi             KPI card: muted title, big value, muted detail
  StepBar         1. Connect > 2. Discover > 3. Expose as MCP tools > 4. Review & publish
  soft()          soft status chip (Read-only sources, Max 500 rows ...)
  Worker          run a blocking call (discovery, test call, connection test) off the GUI thread
"""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QHeaderView, QLabel, QMenu, QPushButton, QScrollArea,
                               QSizePolicy, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from mcx_studio.theme.ui_kit import T, make_icon, set_prop

RISK_TOKEN = {"R0": "info", "R1": "ok", "R2": "warn", "R3": "info", "R4": "bad", "R5": "bad"}
STATE_TOKEN = {"online": "ok", "connecting": "warn", "error": "warn", "stopped": "muted"}
RESULT_TOKEN = {"ok": "ok", "blocked": "bad", "error": "bad", "truncated": "warn"}
PLATFORMS = ["Copilot Studio", "ContextForge", "Docker MCP Gateway", "Claude Desktop"]
CLIENT_KIND = {"ContextForge": "Gateway", "Docker MCP Gateway": "Gateway", "Copilot Studio": "AI platform",
               "Claude Desktop": "Direct (.mcpb)"}


# ======================================================================= worker
class _Bridge(QObject):
    done = Signal(object, object)          # (result, exception)


class Worker:
    """Worker.run(fn, on_done(result, error)) - fn runs in a thread, on_done on the GUI thread."""
    _alive: set = set()

    @classmethod
    def run(cls, fn, on_done):
        b = _Bridge()
        cls._alive.add(b)

        def finish(res, err):
            cls._alive.discard(b)
            on_done(res, err)
        b.done.connect(finish, Qt.ConnectionType.QueuedConnection)

        def go():
            try:
                res = fn()
            except Exception as e:                       # shown to the user, never raised in a thread
                b.done.emit(None, e)
                return
            b.done.emit(res, None)
        threading.Thread(target=go, daemon=True, name="studio-worker").start()


# ======================================================================= page scaffold
class _Body(QWidget):
    """Page body: any width (the scroll area makes it the viewport's width - columns reflow), height
    from its content (the page scrolls vertically)."""

    def minimumSizeHint(self):
        from PySide6.QtCore import QSize
        return QSize(0, super().minimumSizeHint().height())


class Page(QWidget):
    """Doc bar on top, then a scrolling body: title + subtitle row, then the page content."""

    def __init__(self, host, title: str = "", subtitle: str = "", scroll: bool = True):
        super().__init__()
        self.host = host
        self.bar = QFrame(); self.bar.setObjectName("docBar")
        self.bar_l = QHBoxLayout(self.bar); self.bar_l.setContentsMargins(8, 3, 8, 3); self.bar_l.setSpacing(2)
        self._bar_right = None
        self.body = _Body(); self.body.setObjectName("pageBody")
        self.v = QVBoxLayout(self.body); self.v.setContentsMargins(20, 12, 20, 16); self.v.setSpacing(12)
        self.v.setSizeConstraint(QVBoxLayout.SizeConstraint.SetNoConstraint)   # body = viewport width: columns reflow
        head = QHBoxLayout(); head.setSpacing(12)
        self.title = QLabel(title); self.title.setObjectName("pageTitle")
        self.subtitle = QLabel(subtitle); self.subtitle.setProperty("muted", True)
        self.subtitle.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)   # never widens the page
        self.subtitle.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        head.addWidget(self.title); head.addWidget(self.subtitle, 1, Qt.AlignmentFlag.AlignBottom)
        self.v.addLayout(head)
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)
        lay.addWidget(self.bar)
        if scroll:
            sc = QScrollArea(); sc.setObjectName("pageScroll"); sc.setWidgetResizable(True)
            sc.setFrameShape(QFrame.Shape.NoFrame); sc.setWidget(self.body)
            sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            lay.addWidget(sc, 1)
        else:
            lay.addWidget(self.body, 1)

    def tool(self, text, icon, slot, right=False, tip="") -> QToolButton:
        b = QToolButton(); b.setText(text); b.setToolTip(tip or text)
        b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon if text else Qt.ToolButtonStyle.ToolButtonIconOnly)
        if icon:
            self.host.reg_icon(b, icon)
        if isinstance(slot, list):
            m = QMenu(b)
            for t, s in slot:
                m.addSeparator() if t is None else m.addAction(t, s)
            b.setMenu(m); b.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        elif slot:
            b.clicked.connect(slot)
        if self._bar_right is None:
            self.bar_l.addStretch(1)                  # left buttons go before it, right ones after
            self._bar_right, self._nleft = True, 0
        if right:
            self.bar_l.addWidget(b)
        else:
            self.bar_l.insertWidget(self._nleft, b)
            self._nleft += 1
        return b

    def refresh(self):
        pass

    def tick(self):
        """Live state changed (calls, health): cheap update. Default: a full refresh."""
        self.refresh()

    def activate(self):
        """The tab came to the front: show its selection in Properties."""
        self.refresh()


# ======================================================================= panels / labels
def panel(title: str = "", note: str = "", heading: str = "", icon: str | None = None, host=None):
    """Rounded panel. Returns (frame, body layout, head layout)."""
    f = QFrame(); f.setObjectName("panel")
    v = QVBoxLayout(f); v.setContentsMargins(14, 10, 14, 12); v.setSpacing(8)
    head = QHBoxLayout(); head.setSpacing(8)
    if icon and host is not None:
        il = QLabel(); il.setFixedSize(16, 16)
        il.setPixmap(make_icon(icon, T["text"]).pixmap(16, 16))
        head.addWidget(il)
    if heading:
        h = QLabel(heading); h.setObjectName("panelHeading"); head.addWidget(h)
    if title:
        t = QLabel(title); t.setObjectName("panelTitle"); head.addWidget(t)
    head.addStretch(1)
    n = QLabel(note); n.setObjectName("panelNote"); head.addWidget(n)
    f.note = n                                  # pages update it (counts)
    v.addLayout(head)
    return f, v, head


def soft(text: str, level: str = "info") -> QLabel:
    l = QLabel(text); l.setObjectName("soft"); l.setProperty("level", level)
    l.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    return l


def muted(text: str, small: bool = False, wrap: bool = True) -> QLabel:
    l = QLabel(text); l.setProperty("muted", True); l.setWordWrap(wrap)
    if small:
        l.setProperty("small", True)
    return l


def flow(widgets, spacing=6) -> QWidget:
    """Simple wrapping row for chips: rows of up to 4 (keeps it readable on a laptop)."""
    w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(spacing)
    row = None
    for i, x in enumerate(widgets):
        if i % 4 == 0:
            row = QHBoxLayout(); row.setSpacing(spacing); v.addLayout(row)
        row.addWidget(x)
    if row is not None:
        row.addStretch(1)
    return w


class Columns(QWidget):
    """Two (or more) columns side by side that stack when the page is narrower than `min_w`."""

    def __init__(self, min_w: int = 1000, spacing: int = 12):
        super().__init__()
        from PySide6.QtWidgets import QBoxLayout
        self.min_w = min_w
        self.box = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self.box.setContentsMargins(0, 0, 0, 0); self.box.setSpacing(spacing)

    def add(self, w_or_layout, stretch: int = 1):
        (self.box.addLayout if not isinstance(w_or_layout, QWidget) else self.box.addWidget)(w_or_layout, stretch)

    def resizeEvent(self, e):
        from PySide6.QtWidgets import QBoxLayout
        d = QBoxLayout.Direction.LeftToRight if e.size().width() >= self.min_w else QBoxLayout.Direction.TopToBottom
        if self.box.direction() != d:
            self.box.setDirection(d)
        super().resizeEvent(e)


class KpiRow(QWidget):
    """KPI cards in one row, 2 x 2 when narrow."""

    def __init__(self, cards, min_w: int = 760):
        super().__init__()
        from PySide6.QtWidgets import QGridLayout
        self.cards, self.min_w, self._cols = list(cards), min_w, 0
        self.g = QGridLayout(self); self.g.setContentsMargins(0, 0, 0, 0); self.g.setSpacing(12)
        self._place(len(self.cards))

    def _place(self, cols):
        if cols == self._cols:
            return
        self._cols = cols
        for c in self.cards:
            self.g.removeWidget(c)
        for i, c in enumerate(self.cards):
            self.g.addWidget(c, i // cols, i % cols)

    def resizeEvent(self, e):
        n = len(self.cards)
        self._place(n if e.size().width() >= self.min_w or n <= 2 else (n + 1) // 2)
        super().resizeEvent(e)


class Kpi(QFrame):
    def __init__(self, title: str):
        super().__init__()
        self.setObjectName("kpi")
        v = QVBoxLayout(self); v.setContentsMargins(14, 10, 14, 10); v.setSpacing(0)
        self.t = QLabel(title); self.t.setProperty("muted", True)
        self.value = QLabel("-"); self.value.setObjectName("kpiValue")
        self.detail = QLabel(""); self.detail.setProperty("muted", True)
        self.detail.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        for w in (self.t, self.value, self.detail):
            v.addWidget(w)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set(self, value, detail: str = "", level: str = ""):
        self.value.setText(str(value))
        self.detail.setText(detail)
        self.detail.setVisible(bool(detail))
        if self.value.property("level") != level:
            set_prop(self.value, "level", level)


class StepBar(QWidget):
    """Wizard steps; click one to go there. states: done | active | todo."""

    def __init__(self, steps: list[str], on_click):
        super().__init__()
        h = QHBoxLayout(self); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(8)
        self.buttons = []
        for i, s in enumerate(steps):
            if i:
                a = QLabel("›"); a.setObjectName("stepArrow"); h.addWidget(a)
            b = QPushButton(f"{i + 1}. {s}".replace("&", "&&")); b.setObjectName("step"); b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=i: on_click(k))
            h.addWidget(b); self.buttons.append(b)
        h.addStretch(1)
        self.labels = steps

    def set_state(self, active: int, done: set):
        for i, b in enumerate(self.buttons):
            st = "active" if i == active else ("done" if i in done else "todo")
            b.setText(("✓ " if st == "done" else "") + f"{i + 1}. {self.labels[i]}".replace("&", "&&"))
            if b.property("state") != st:
                set_prop(b, "state", st)


# ======================================================================= tables
def table(headers: list[str], stretch: int = 0, widths: dict | None = None) -> QTreeWidget:
    t = QTreeWidget(); t.setObjectName("table")
    t.setHeaderLabels(headers)
    t.setRootIsDecorated(False); t.setUniformRowHeights(True); t.setAlternatingRowColors(False)
    t.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    hd = t.header(); hd.setStretchLastSection(False); hd.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft)
    for i in range(len(headers)):
        hd.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch if i == stretch else QHeaderView.ResizeMode.Interactive)
    for i, w in (widths or {}).items():
        hd.resizeSection(i, w)
    return t


def fit_height(t: QTreeWidget, rows: int | None = None, max_rows: int = 14):
    """Size a list to its rows (panels on a scrolling page instead of nested scroll bars)."""
    n = t.topLevelItemCount() if rows is None else rows
    rh = max(t.sizeHintForRow(0) if n else 0, t.fontMetrics().height() + 12)
    h = t.header().sizeHint().height() + max(1, min(n, max_rows)) * rh + 6 + 12   # + room for a scroll bar
    t.setFixedHeight(max(h, 60))


def row(t: QTreeWidget, values: list, data=None) -> QTreeWidgetItem:
    it = QTreeWidgetItem(["" if v is None else str(v) for v in values])
    if data is not None:
        it.setData(0, Qt.ItemDataRole.UserRole, data)
    t.addTopLevelItem(it)
    return it


def color(it: QTreeWidgetItem, col: int, token: str, bold: bool = False):
    if token and token in T:
        it.setForeground(col, QColor(T[token]))
    if bold:
        f = it.font(col); f.setWeight(QFont.Weight.DemiBold); it.setFont(col, f)


def mono(it: QTreeWidgetItem, col: int):
    f = QFont("Menlo"); f.setStyleHint(QFont.StyleHint.Monospace)
    f.setPointSizeF(it.font(col).pointSizeF() if it.font(col).pointSizeF() > 0 else 11)
    it.setFont(col, f)


def risk_cell(it: QTreeWidgetItem, col: int, risk: str):
    color(it, col, RISK_TOKEN.get(risk, "muted"), bold=True)


def fmt_int(n) -> str:
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return str(n)
