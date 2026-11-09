"""App-level styling on top of ui_kit: extra QSS, dense mode, Fusion control style."""
from __future__ import annotations

import re

from PySide6.QtGui import QColor

from mcx_studio.theme import ui_kit as ui


_FUSION_DROP = re.compile(r"^\s*(QLineEdit|QComboBox|QPlainTextEdit|QPushButton(?!\[primary)|QSlider|"
                          r"QScrollBar|QTableView|QHeaderView|QProgressBar|QDoubleSpinBox|QSpinBox|"
                          r"QTreeWidget \{)")
# check boxes keep the flat indicator in both styles: Fusion's outline is near-invisible in dark


def fusion_qss(qss: str) -> str:
    return "\n".join(line for line in qss.splitlines() if not _FUSION_DROP.match(line))


def fusion_palette(t: dict):
    """Theme palette with the extra roles Fusion uses for bevels and outlines."""
    from PySide6.QtGui import QPalette
    p = ui.build_palette(t)
    dark = t.get("mode") == "dark"
    btn = QColor(t["hover"] if dark else t["panel"])
    R = QPalette.ColorRole
    for role, c in ((R.Button, btn), (R.Light, btn.lighter(135 if dark else 110)),
                    (R.Midlight, btn.lighter(115 if dark else 105)), (R.Mid, btn.darker(130)),
                    (R.Dark, btn.darker(170 if dark else 150)), (R.Shadow, QColor("#000000")),
                    (R.Link, QColor(t["accent"]))):
        p.setColor(role, c)
    return p


def blend(c1: str, c2: str, a: float) -> str:
    """a*c1 + (1-a)*c2 for hex colours (derived token, e.g. a soft selection)."""
    x, y = QColor(c1), QColor(c2)
    return QColor(int(x.red() * a + y.red() * (1 - a)), int(x.green() * a + y.green() * (1 - a)),
                  int(x.blue() * a + y.blue() * (1 - a))).name()

EXTRA_QSS = """
QGroupBox { border: none; margin: 0; padding: 0; }
QLabel#led { border-radius: 6px; background: @muted@; }
QLabel#led[level="ok"] { background: @ok@; }
QLabel#led[level="warn"] { background: @warn@; }
QLabel#led[level="bad"] { background: @bad@; }
QDoubleSpinBox, QSpinBox { background: @input@; border: 1px solid @border@; border-radius: 4px; padding: 4px 6px; }
QDoubleSpinBox:focus, QSpinBox:focus { border: 1px solid @accent@; }
QLabel#recLabel { color: @muted@; font-weight: 600; padding: 0 8px; }
QLabel#recLabel[level="rec"] { color: @bad@; }
QTreeWidget { background: @input@; alternate-background-color: @panel2@; border: 1px solid @border@; }
QAbstractItemView { outline: 0; }
QTreeWidget::item { padding: 3px 0; }
QTreeWidget::item:selected { background: @sel@; color: @text@; }
QFrame#docBar { background: @panel@; border-bottom: 1px solid @border@; }
QTreeWidget#navTree { background: @panel2@; border: none; }
QTreeWidget#navTree::item { padding: 4px 0; }
QTreeWidget#navTree[plainSel="true"]::item:selected { background: @hover@; color: @text@; }  /* Tags list: no accent */
QFrame#navBar { background: transparent; border-bottom: 1px solid @border@; }
QFrame#navBar QToolButton { padding: 4px; }
QFrame#docBar QLabel { background: transparent; }
QToolBar::separator { background: @border@; width: 1px; margin: 6px 2px; }
QFrame#vsep { background: @border@; border: none; min-width: 1px; max-width: 1px; margin: 6px 2px; }
QMenu { icon-size: 0px; }  /* menus are text only: actions shared with the toolbar keep their icon there, no empty column here */
QMainWindow::separator { background: @hover@; width: 4px; height: 4px; border: none; }
QTabWidget#docTabs::pane { border: 1px solid @border@; top: -1px; background: @bg@; }
QTabWidget#docTabs QTabBar::tab:selected { border-bottom: 2px solid @accent@; }
QTabWidget#docTabs QTabBar::tab { padding: 4px 6px 4px 12px; margin-top: 0; }
QToolButton#tabClose { border: none; border-radius: 3px; padding: 0; margin: 0 0 0 4px; color: @muted@; font-size: 13px; }
QToolButton#tabClose:hover { background: @hover@; color: @text@; }
QTreeView::indicator, QListView::indicator, QCheckBox::indicator { width: 13px; height: 13px; border: 1px solid @muted@; border-radius: 3px; background: @input@; }
QTreeView::indicator:checked, QListView::indicator:checked, QCheckBox::indicator:checked { background: @accent@; border: 1px solid @accent@; image: url(@check@); }
QTreeView::indicator:disabled, QCheckBox::indicator:disabled { border: 1px solid @border@; }
QPushButton[primary="true"] { padding: 6px 14px; border-radius: 4px; }
QLabel#brand { font-size: 15px; font-weight: 600; padding: 0 12px 0 8px; }
QToolBar#mainToolbar { padding: 2px 6px; spacing: 2px; }               /* top nav: outer padding, gap */
QToolBar#mainToolbar QToolButton { padding: 3px 6px; }                /* top nav: button padding */
QLabel#chip { qproperty-alignment: AlignCenter; }
QWidget#viewCanvas { background: @bg@; }
QFrame#viewTile { background: transparent; border: none; }   /* card painted by Tile (user colours) */
QFrame#tileHeader { background: @panel2@; border: 1px solid @border@; border-radius: 4px; }
QFrame#tileHeader QLabel { background: transparent; }
QLabel#viewH1 { font-size: 26px; font-weight: 600; background: transparent; }
QLabel#viewH2 { font-size: 20px; font-weight: 600; background: transparent; }
QLabel#viewH3 { font-size: 16px; font-weight: 600; background: transparent; }
QLabel#viewBody { font-size: 13px; background: transparent; }
QLabel#viewValue { font-size: 30px; font-weight: 600; background: transparent; }
QFrame#subBar { background: @panel2@; border-bottom: 1px solid @border@; }
QFrame#subBar QLabel { background: transparent; }
QTabWidget#subTabs::pane { border: none; }
QToolButton#alarmStatus { border: none; padding: 0 8px; color: @muted@; }
QToolButton#alarmStatus[level="bad"] { color: @bad@; font-weight: 600; }
QToolButton#alarmStatus[level="warn"] { color: @warn@; font-weight: 600; }
QFrame#viewTile QTableWidget { border: none; }
QFrame#gridCell { background: transparent; border: none; }   /* dashed outline painted by _Cell */
QToolButton#gridHead { background: @panel2@; border: 1px solid @border@; border-radius: 3px; color: @muted@; font-size: 11px; padding: 0; }
QToolButton#gridHead:hover { color: @text@; border: 1px solid @muted@; }
QToolButton#gridHead[selected="true"] { color: @accent_text@; background: @accent@; border: 1px solid @accent@; }
QTreeWidget#propGrid { background: @panel@; alternate-background-color: @panel@; border: none; }
QTreeWidget#propGrid::item { padding: 1px 0; }
QTreeWidget#propGrid QLineEdit, QTreeWidget#propGrid QComboBox, QTreeWidget#propGrid QSpinBox,
QTreeWidget#propGrid QDoubleSpinBox, QTreeWidget#propGrid QPlainTextEdit { padding: 1px 4px; border-radius: 3px;
  border: 1px solid @border@; background: @input@; }
QLabel#pageSection { font-weight: 600; border: none; border-radius: 0; border-bottom: 1px solid @border@; padding: 0 0 3px 0; background: transparent; }
QLabel#statusMsg[level="warn"] { color: @warn@; }
QLabel#statusMsg[level="info"] { color: @info@; }
QFrame#dlgHeader { background: @panel2@; border: none; border-bottom: 1px solid @border@; }
QFrame#dlgHeader QLabel { background: transparent; }
QLabel#dlgTitle { font-size: 16px; font-weight: 600; }
QFrame#dlgFooter { background: @panel2@; border: none; border-top: 1px solid @border@; }
QFrame#dlgFooter QLabel { background: transparent; }
QLabel#emptyTitle { font-weight: 600; background: transparent; }
QLabel#propTitle { font-size: 14px; font-weight: 600; background: transparent; }
QFrame#propHead { background: @panel2@; border-bottom: 1px solid @border@; }
QScrollArea#startScroll, QWidget#startBody { background: @bg@; }
QFrame#startBanner { background: @panel2@; border: none; border-bottom: 2px solid @accent@; }
QFrame#startBanner QLabel { background: transparent; }
QLabel#startWordmark { font-size: 22px; font-weight: 600; }
QFrame#startCard { background: @panel@; border: 1px solid @border@; border-radius: 6px; }
QFrame#startCard QLabel, QFrame#startCard QCheckBox { background: transparent; }
QLabel#startHeading { font-size: 15px; font-weight: 600; color: @accent@; }
QFrame#startTile { background: @panel2@; border: 1px solid @border@; border-radius: 6px; }
QFrame#startTile:hover { border: 1px solid @accent@; }
QFrame#startTile QLabel { background: transparent; }
QLabel#startTileTitle { font-size: 14px; font-weight: 600; }
QFrame#startKpi { background: @bg@; border: 1px solid @border@; border-radius: 4px; }
QFrame#startKpi QLabel { background: transparent; }
QLabel#startKpiValue { font-size: 18px; font-weight: 600; }
QLabel#startKpiValue[level="muted"] { color: @muted@; }
QLabel#startKpiValue[level="bad"] { color: @bad@; }
QTreeWidget#startList { background: transparent; border: none; }
QTreeWidget#startList::item { padding: 3px 0; }
QTreeWidget#startList::item:hover { background: @hover@; }
QLabel#startLink { padding: 1px 0; }
QFrame#monitorBar { background: @panel2@; border: none; border-bottom: 1px solid @border@; }
QFrame#monitorBar QLabel { background: transparent; }
QLabel#monitorChip { border: 1px solid @border@; border-radius: 9px; padding: 1px 9px; color: @muted@; }
QLabel#monitorChip[level="ok"] { color: @ok@; border-color: @ok@; }
QLabel#monitorChip[level="warn"] { color: @warn@; border-color: @warn@; }
QLabel#monitorChip[level="bad"] { color: @bad@; border-color: @bad@; }
QLabel#monitorClock { font-weight: 600; padding: 0 4px; }
QFrame#propHead QLabel { background: transparent; }
QFrame#formHead { background: @accent@; border-bottom: 1px solid @border@; }
QFrame#formHead QLabel#formPath { color: @accent_text@; font-style: italic; background: transparent; }
QFrame#formHead QLabel#statusMsg { background: transparent; }
QFrame#formSection { border: 1px solid @border@; border-radius: 4px; background: @panel@; }
QFrame#formSectionBar { background: @panel2@; border-bottom: 1px solid @border@; border-top-left-radius: 4px; border-top-right-radius: 4px; }
QLabel#formSectionHead { font-weight: 600; background: transparent; }
QLabel#formSectionArrow { color: @muted@; background: transparent; }
QFrame#formSectionBody { background: transparent; border: none; }
QFrame#formBar { background: @panel@; border-top: 1px solid @border@; }
QLabel#quality[level="bad"] { color: @bad@; }
QLabel#quality[level="ok"] { color: @ok@; }

/* (QPushButton / QLineEdit / QPlainTextEdit rules are dropped by fusion_qss: use QAbstractButton /
   QWidget / QAbstractScrollArea selectors for page widgets) */
/* ---- MCX Adapter pages: panels, KPI cards, step pills, soft chips, tables, code */
QScrollArea#pageScroll, QWidget#pageBody { background: @bg@; }
QLabel#pageTitle { font-size: 22px; font-weight: 600; background: transparent; }
QFrame#panel { background: @panel@; border: 1px solid @border@; border-radius: 6px; }
QFrame#panel QLabel, QFrame#panel QCheckBox, QFrame#panel QWidget#panelBody { background: transparent; }
QLabel#panelTitle { color: @muted@; }
QLabel#panelNote { color: @muted@; }
QLabel#panelHeading { font-weight: 600; font-size: 14px; }
QFrame#kpi { background: @panel@; border: 1px solid @border@; border-radius: 6px; }
QFrame#kpi QLabel { background: transparent; }
QLabel#kpiValue { font-size: 26px; font-weight: 600; }
QLabel#kpiValue[level="accent"] { color: @accent@; }
QLabel#kpiValue[level="bad"] { color: @bad@; }
QLabel#kpiValue[level="ok"] { color: @ok@; }
QAbstractButton#step { border-radius: 3px; padding: 6px 14px; background: @panel@; border: 1px solid @border@; color: @muted@; }
QAbstractButton#step:hover { border: 1px solid @muted@; }
QAbstractButton#step[state="done"] { background: @okSoft@; color: @ok@; border: 1px solid @okSoft@; }
QAbstractButton#step[state="active"] { background: @accent@; color: @accent_text@; border: 1px solid @accent@; font-weight: 600; }
QLabel#stepArrow { color: @muted@; background: transparent; }
QLabel#soft { border-radius: 3px; padding: 2px 8px; background: @hover@; }
QLabel#soft[level="ok"] { background: @okSoft@; color: @ok@; }
QLabel#soft[level="info"] { background: @infoSoft@; color: @info@; }
QLabel#soft[level="warn"] { background: @warnSoft@; color: @warn@; }
QLabel#soft[level="bad"] { background: @badSoft@; color: @bad@; }
QAbstractButton#seg { background: @panel@; border: 1px solid @border@; border-radius: 3px; padding: 5px 14px; }
QAbstractButton#seg:hover { background: @hover@; }
QAbstractButton#seg:checked { background: @accent@; color: @accent_text@; border: 1px solid @accent@; font-weight: 600; }
QTreeWidget#table { background: @panel@; alternate-background-color: @panel@; border: none; }
QTreeWidget#table::item { padding: 5px 0; border-bottom: 1px solid @hover@; }
QTreeWidget#table::item:selected { background: @sel@; color: @text@; }
QTreeWidget#table QHeaderView::section { background: @panel2@; color: @text@; font-weight: 600; }
QAbstractScrollArea#code { background: @panel2@; border: 1px solid @border@; border-radius: 4px; font-family: Menlo, Consolas, "DejaVu Sans Mono", monospace; font-size: 12px; }
QLabel#code { font-family: Menlo, Consolas, "DejaVu Sans Mono", monospace; background: transparent; }
QLabel#codeAccent { font-family: Menlo, Consolas, "DejaVu Sans Mono", monospace; color: @accent@; background: transparent; }
QWidget#codeEdit { font-family: Menlo, Consolas, "DejaVu Sans Mono", monospace; background: @infoSoft@; color: @info@; border: 1px solid @infoSoft@; }
QLabel#chip[level="accent"] { background: @accent@; }
QToolButton#themeBtn { padding: 3px 8px; }
QFrame#formGrid QLabel[key="true"] { color: @muted@; }
"""

# Small screens: same tokens, tighter spacing (appended after EXTRA_QSS so it wins)
DENSE_QSS = """
QToolBar { spacing: 2px; padding: 2px; }
QToolButton { padding: 3px 6px; }
QTabBar::tab { padding: 6px 10px; }
QMenuBar::item { padding: 4px 8px; }
QDockWidget::title { padding: 5px 8px; }
QLineEdit, QComboBox, QPlainTextEdit { padding: 3px 6px; }
QPushButton { padding: 4px 10px; }
QTreeWidget::item { padding: 1px 0; }
QTreeWidget#navTree::item { padding: 2px 0; }
QHeaderView::section { padding: 3px 6px; }
"""
