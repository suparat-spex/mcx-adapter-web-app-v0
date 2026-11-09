"""
property_grid.py - Properties panel (right dock), like the property inspector of an
engineering tool: whatever is selected (source, tool, profile, endpoint ...) shows
its properties here, grouped in sections, and edits apply at once.

The panel knows nothing about the project. Callers hand it a PropTarget:
  title, icon, subtitle         header
  sections                      [(section name, [field])] - field dicts as core/tags.F():
                                key, label, type, default, extra, tip (+ optional "readonly")
  values                        {key: value}
  apply(key, value)             called on every edit (the caller changes the model)
  actions                       [(text, icon, callable)] icon buttons in the dock bar (text = tooltip),
                                between Refresh and Expand all / Collapse (like the Project dock)
  live()                        optional {key: value} refreshed twice a second (read-only fields)
  readonly                      every field shown as text (no editors)

Field types: str, password, text (multi-line), int, float, bool, choice (extra = list or
{value: label}), combo (editable choice), color, info (read-only text), button (extra =
callable, label = button text), channel / channels (extra = callable opening a picker,
value shown as text).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFrame, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QMenu, QPlainTextEdit, QPushButton, QSpinBox,
                               QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from mcx_studio.theme.app_style import blend
from mcx_studio.theme.ui_kit import T, label, make_icon


@dataclass
class PropTarget:
    title: str
    icon: str = "info"
    subtitle: str = ""
    sections: list = field(default_factory=list)
    values: dict = field(default_factory=dict)
    apply: object = None
    actions: list = field(default_factory=list)
    live: object = None
    key: tuple = ()                       # identity: the same object is not rebuilt on refresh
    readonly: bool = False                # information only (devices: edited in their pop-up)


class _TextEdit(QPlainTextEdit):
    finished = Signal(str)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self.finished.emit(self.toPlainText())


def _choices(extra) -> list[tuple]:
    if isinstance(extra, dict):
        return list(extra.items())
    return [(x, str(x)) for x in (extra or [])]


class PropertyGrid(QTreeWidget):
    edited = Signal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("propGrid")
        self.setColumnCount(2)
        self.setHeaderHidden(True)
        self.setIndentation(16)                  # room left of the section arrow
        self.setRootIsDecorated(True)
        self.setUniformRowHeights(False)
        self.setSelectionMode(QTreeWidget.SelectionMode.NoSelection)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.header().resizeSection(0, 150)          # labels sit 32 px in (two indent steps)
        self.editors: dict[str, QWidget] = {}
        self._fields: dict[str, dict] = {}
        self._quiet = False

    def drawRow(self, painter, option, index):
        """Section rows are a full-width band (arrow area included) with a border, like the
        channel groups of a measurement tool; neutral theme colours, read from T at paint time."""
        if not index.parent().isValid():
            r = option.rect
            painter.fillRect(r, QColor(blend(T["hover"], T["panel"], 0.7)))
            painter.setPen(QColor(T["border"]))
            painter.drawLine(r.left(), r.top(), r.right(), r.top())
            painter.drawLine(r.left(), r.bottom(), r.right(), r.bottom())
        super().drawRow(painter, option, index)

    def set_fields(self, sections, values: dict, readonly: bool = False):
        open_sections = {self.topLevelItem(i).text(0) for i in range(self.topLevelItemCount())
                         if not self.topLevelItem(i).isExpanded()}
        self.clear(); self.editors.clear(); self._fields.clear()
        semi = self.font(); semi.setWeight(semi.Weight.DemiBold)
        for name, fields in sections:
            if not fields:
                continue
            sec = QTreeWidgetItem([name])
            sec.setFirstColumnSpanned(True)
            sec.setFont(0, semi)
            sec.setSizeHint(0, QSize(0, 24))
            sec.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.addTopLevelItem(sec)
            for f in fields:
                it = QTreeWidgetItem([f["label"]])
                it.setToolTip(0, f.get("tip") or f["label"])
                it.setFlags(Qt.ItemFlag.ItemIsEnabled)
                sec.addChild(it)
                w = self._editor(f, values.get(f["key"], f.get("default")), readonly or f.get("readonly"))
                if f.get("tip"):
                    w.setToolTip(f["tip"])
                self.setItemWidget(it, 1, w)
                self.editors[f["key"]] = w
                self._fields[f["key"]] = f
            sec.setExpanded(name not in open_sections)

    # ---- editors
    def _editor(self, f: dict, value, readonly: bool) -> QWidget:
        kind, key, extra = f["type"], f["key"], f.get("extra")
        emit = lambda v, k=key: None if self._quiet else self.edited.emit(k, v)   # noqa: E731
        if kind == "info" or (readonly and kind not in ("button",)):
            w = QLabel(self._text(f, value)); w.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            w.setWordWrap(True); w.setContentsMargins(4, 2, 4, 2)
            if f.get("level_of"):
                w.setObjectName("quality"); w.setProperty("level", f["level_of"](value))
            return w
        if kind in ("str", "password"):
            w = QLineEdit("" if value is None else str(value))
            if kind == "password":
                w.setEchoMode(QLineEdit.EchoMode.Password)
            w.editingFinished.connect(lambda w=w: w.isModified() and (w.setModified(False), emit(w.text())))
            return w
        if kind == "text":
            w = _TextEdit("" if value is None else str(value)); w.setMaximumHeight(64)
            w.setTabChangesFocus(True)
            w.finished.connect(emit)
            return w
        if kind == "int":
            lo, hi = extra if isinstance(extra, tuple) else (-2_000_000_000, 2_000_000_000)
            w = QSpinBox(minimum=int(lo), maximum=int(hi)); w.setValue(int(value or 0))
            w.setKeyboardTracking(False); w.valueChanged.connect(emit)
            return w
        if kind == "float":
            lo, hi = extra if isinstance(extra, tuple) else (-1e12, 1e12)
            w = QDoubleSpinBox(minimum=float(lo), maximum=float(hi), decimals=f.get("decimals", 4))
            w.setValue(float(value or 0)); w.setKeyboardTracking(False); w.valueChanged.connect(emit)
            return w
        if kind == "bool":
            w = QCheckBox(); w.setChecked(bool(value)); w.toggled.connect(emit)
            return w
        if kind in ("choice", "combo"):
            w = QComboBox(); w.setEditable(kind == "combo")
            items = _choices(extra)
            for v, text in items:
                w.addItem(text, v)
            i = next((n for n, (v, _) in enumerate(items) if v == value or str(v) == str(value)), -1)
            if i >= 0:
                w.setCurrentIndex(i)
            elif kind == "combo":
                w.setEditText("" if value is None else str(value))
            if kind == "combo":
                w.lineEdit().editingFinished.connect(lambda w=w: emit(w.currentText().strip()))
                w.activated.connect(lambda _i, w=w: emit(w.currentData() if w.currentData() is not None
                                                         else w.currentText()))
            else:
                w.activated.connect(lambda _i, w=w: emit(w.currentData()))
            return w
        if kind in ("button", "channel", "channels"):
            box = QWidget(); h = QHBoxLayout(box); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(4)
            if kind != "button":
                lbl = QLabel(self._text(f, value)); lbl.setWordWrap(True)
                h.addWidget(lbl, 1)
                box.value_label = lbl
            b = QPushButton(f.get("button", "...") if kind != "button" else f.get("button", f["label"]))
            b.clicked.connect(lambda: extra and extra())
            b.setEnabled(not readonly)
            h.addWidget(b, 0 if kind != "button" else 1)
            return box
        return QLabel(str(value))

    @staticmethod
    def _text(f, value) -> str:
        if f["type"] == "password":
            return "••••••" if value else ""
        if f["type"] in ("choice", "combo"):
            return dict(_choices(f.get("extra"))).get(value, "" if value is None else str(value))
        if f["type"] == "bool":
            return "Yes" if value else "No"
        if isinstance(value, float):
            return f"{value:g}"
        return "" if value is None else str(value)

    def set_value(self, key: str, value):
        """Refresh one field from the model (no edited signal)."""
        w, f = self.editors.get(key), self._fields.get(key)
        if w is None:
            return
        self._quiet = True
        try:
            if isinstance(w, QLabel):
                text = self._text(f, value)
                if w.text() != text:
                    w.setText(text)
                if f.get("level_of"):
                    lvl = f["level_of"](value)
                    if w.property("level") != lvl:
                        w.setObjectName("quality"); w.setProperty("level", lvl)
                        w.style().unpolish(w); w.style().polish(w)
            elif hasattr(w, "value_label"):
                w.value_label.setText(self._text(f, value))
        finally:
            self._quiet = False


class PropertiesPanel(QWidget):
    """Header (icon, name, type) + action buttons + PropertyGrid + an optional footer widget."""

    def __init__(self, reg_icon, footer: QWidget | None = None, on_refresh=None, empty_hint=None):
        super().__init__()
        self.reg_icon = reg_icon
        self.empty_hint = empty_hint                 # () -> (title, text) for "nothing selected" here
        self.target: PropTarget | None = None
        self._act_btns: list[tuple[QToolButton, str]] = []
        # dock bar, the same pattern as the Project / Data docks: Refresh, item actions | Expand, Collapse
        self.bar = bar = QFrame(); bar.setObjectName("navBar")
        bl = QHBoxLayout(bar); bl.setContentsMargins(4, 2, 4, 2); bl.setSpacing(0)
        self.btn_refresh = self._bar_button("Refresh (read the item again)", "refresh",
                                            on_refresh or (lambda: self.show_target(self.target)))
        bl.addWidget(self.btn_refresh)
        self.actions_row = QHBoxLayout(); self.actions_row.setSpacing(0)
        bl.addLayout(self.actions_row)
        bl.addStretch(1)
        for tip, icon, slot in (("Expand all", "expand", lambda: self.grid.expandAll()),
                                ("Collapse", "collapse", lambda: self.grid.collapseAll())):
            bl.addWidget(self._bar_button(tip, icon, slot))
        self.head = head = QFrame(); head.setObjectName("propHead")
        hv = QVBoxLayout(head); hv.setContentsMargins(10, 8, 10, 8); hv.setSpacing(2)
        top = QHBoxLayout(); top.setSpacing(8)
        self.icon = QLabel(); self.icon.setFixedSize(20, 20)
        self.title = QLabel("Nothing selected"); self.title.setObjectName("propTitle")
        self.title.setWordWrap(True)
        top.addWidget(self.icon); top.addWidget(self.title, 1)
        hv.addLayout(top)
        self.subtitle = label("", muted=True, small=True); self.subtitle.setWordWrap(True)
        hv.addWidget(self.subtitle)
        self.grid = PropertyGrid()
        self.grid.edited.connect(self._edited)
        # nothing selected: only a centred message - no bar, no header, no lines
        self.empty = QWidget()
        ev = QVBoxLayout(self.empty); ev.setContentsMargins(16, 0, 16, 0); ev.setSpacing(2)
        ev.addStretch(1)
        self.empty_title = QLabel(); self.empty_title.setObjectName("emptyTitle")
        self.empty_text = label("", muted=True)
        for w in (self.empty_title, self.empty_text):
            w.setAlignment(Qt.AlignmentFlag.AlignHCenter); w.setWordWrap(True); ev.addWidget(w)
        ev.addStretch(4)                             # message in the upper part, like other tools
        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)
        v.addWidget(bar); v.addWidget(head); v.addWidget(self.empty, 1); v.addWidget(self.grid, 1)
        if footer is not None:
            v.addWidget(footer)
        self._timer = QTimer(self, interval=500, timeout=self._refresh_live)
        self._timer.start()
        self.show_target(None)

    def _bar_button(self, tip, icon, slot) -> QToolButton:
        b = QToolButton(); b.setToolTip(tip); b.setAutoRaise(True)
        self.reg_icon(b, icon)
        b.clicked.connect(slot)
        return b

    def show_target(self, t: PropTarget | None):
        self.target = t
        self._act_btns.clear()
        while self.actions_row.count():
            w = self.actions_row.takeAt(0).widget()
            if w is not None:                        # off the screen now, deleted later
                w.hide(); w.setParent(None); w.deleteLater()
        if t is None:
            title, text = self.empty_hint() if self.empty_hint else ("No selection", "Select an object to view its settings")
            self.empty_title.setText(title); self.empty_text.setText(text)
            self.grid.clear()
            for w in (self.bar, self.head, self.grid):
                w.hide()
            self.empty.show()
            return
        self.empty.hide()
        for w in (self.bar, self.head, self.grid):
            w.show()
        self.title.setText(t.title)
        self.subtitle.setText(t.subtitle); self.subtitle.setVisible(bool(t.subtitle))
        self.icon.setPixmap(make_icon(t.icon, T["accent"]).pixmap(18, 18))
        for text, icon, slot in t.actions:
            b = QToolButton(); b.setAutoRaise(True)
            b.setToolTip(text.rstrip(".") or {"trash": "Delete", "copy": "Duplicate"}.get(icon, icon))
            if icon:                                  # icon only, like the other dock bars
                b.setIcon(make_icon(icon, T["text"])); self._act_btns.append((b, icon))
            else:
                b.setText(text)
            if isinstance(slot, list):               # [(text, callable)] -> menu
                m = QMenu(b)
                for mt, ms in slot:
                    m.addAction(mt, ms)
                b.setMenu(m); b.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            else:
                b.clicked.connect(slot)
            self.actions_row.addWidget(b)
        self.grid.set_fields(t.sections, t.values, t.readonly)

    def refresh_icons(self):
        for b, icon in self._act_btns:
            b.setIcon(make_icon(icon, T["text"]))
        if self.target is not None:
            self.icon.setPixmap(make_icon(self.target.icon, T["accent"]).pixmap(18, 18))

    def _edited(self, key, value):
        t = self.target
        if t is not None and t.apply is not None:
            t.apply(key, value)

    def _refresh_live(self):
        t = self.target
        if t is None or t.live is None or not self.isVisible():
            return
        try:
            vals = t.live()
        except Exception:
            return
        for k, v in (vals or {}).items():
            self.grid.set_value(k, v)
