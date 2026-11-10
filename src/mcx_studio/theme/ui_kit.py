"""
ui_kit.py - reusable theme + layout kit (PySide6), the "Spex Explorer" design kit
(master copy: the spex-ui skill; this copy loads icons through mcx_studio.icons)

What you get
- Design tokens (DARK / LIGHT) -> one QSS template + QPalette, Dark / Light / Follow-system
- Live token dict `T` (mutated in place, so custom-painted widgets / matplotlib read current colours)
- Lucide-style SVG icons tinted from a token, re-tinted automatically on theme change
- Widgets: Accordion, Card, Chip, Banner + BannerHost, Toast, EmptyState
- AppWindow: the standard shell (menu bar, toolbar, left dock, central area with banners,
  right dock with tabs, status bar with progress, theme menu, layout saved in QSettings)

Usage in a new project
    from mcx_studio.theme.ui_kit import AppWindow, Card, T, ...
    class MyWindow(AppWindow): ...           # override build_left / build_center / build_right
    run(MyWindow, org="MyOrg", app="MyApp")

Run directly to see a demo:   python -m mcx_studio.theme.ui_kit
pip install PySide6   (optional: matplotlib, for style_axes)
"""
import sys

from PySide6.QtCore import Qt, QEvent, QObject, QTimer, QSettings, QCoreApplication
from PySide6.QtGui import (
    QAction, QActionGroup, QColor, QPalette, QKeySequence, QGuiApplication
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLineEdit,
    QPushButton, QLabel, QToolButton, QSizePolicy, QFrame, QScrollArea, QTabWidget, QDockWidget,
    QStatusBar, QProgressBar, QToolBar, QCheckBox, QSlider, QComboBox
)


# ===================================================================== design tokens
DARK = dict(
    mode="dark", bg="#232323", panel="#2d2d2d", panel2="#262626", input="#191919",
    border="#444444", hover="#3a3a3a", text="#e6e6e6", muted="#b0b0b0",
    accent="#f78e01", accent_hover="#f9a43a", accent_text="#1a1a1a",
    ok="#4ade80", warn="#fbbf24", bad="#f87171", info="#60a5fa",
    toast_bg="#e6e6e6", toast_text="#1a1a1a",
)
LIGHT = dict(
    mode="light", bg="#f3f4f6", panel="#ffffff", panel2="#f9fafb", input="#ffffff",
    border="#d1d5db", hover="#e5e7eb", text="#1f2937", muted="#4b5563",
    accent="#f78e01", accent_hover="#e07f00", accent_text="#1a1a1a",
    ok="#15803d", warn="#b45309", bad="#b91c1c", info="#1d4ed8",
    toast_bg="#1f2937", toast_text="#ffffff",
)
THEMES = {"dark": DARK, "light": LIGHT}
T = dict(DARK)   # live tokens (mutated in place so custom-painted widgets see changes)

FONT_FAMILIES = ["Tahoma", "Leelawadee UI", "Noto Sans Thai", "Segoe UI", "DejaVu Sans"]   # Thai-safe stack

QSS = """
QMainWindow, QDialog { background: @bg@; }
QWidget { color: @text@; font-size: 13px; }
QToolTip { background: @panel@; color: @text@; border: 1px solid @border@; padding: 4px; }
QMenuBar { background: @panel@; border-bottom: 1px solid @border@; }
QMenuBar::item { padding: 5px 10px; background: transparent; }
QMenuBar::item:selected { background: @hover@; }
QMenu { background: @panel@; border: 1px solid @border@; padding: 4px; }
QMenu::item { padding: 5px 26px; border-radius: 3px; }
QMenu::item:selected { background: @accent@; color: @accent_text@; }
QMenu::item:disabled { color: @muted@; }
QMenu::separator { height: 1px; background: @border@; margin: 4px 8px; }
QToolBar { background: @panel@; border: none; border-bottom: 1px solid @border@; spacing: 6px; padding: 4px; }
QToolButton { background: transparent; border: 1px solid transparent; border-radius: 4px; padding: 5px 8px; }
QToolButton:hover { background: @hover@; }
QToolButton:pressed, QToolButton:checked { background: @border@; }
QToolButton:disabled { color: @muted@; }
QToolButton::menu-indicator { image: none; }
QToolButton#accHeader { background: @panel2@; border: 1px solid @border@; padding: 7px 10px; text-align: left; font-weight: 600; }
QToolButton#accHeader:hover { background: @hover@; }
QDockWidget::title { background: @panel@; padding: 7px 10px; border-bottom: 1px solid @border@; font-weight: 600; }
QTabWidget::pane { border: none; border-top: 1px solid @border@; background: @panel@; }
QTabBar::tab { background: transparent; padding: 8px 14px; border-bottom: 2px solid transparent; color: @muted@; }
QTabBar::tab:selected { color: @text@; border-bottom: 2px solid @accent@; }
QTabBar::tab:hover { color: @text@; }
QLineEdit, QComboBox, QPlainTextEdit { background: @input@; border: 1px solid @border@; border-radius: 4px; padding: 5px 8px; selection-background-color: @accent@; selection-color: @accent_text@; }
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus { border: 1px solid @accent@; }
QLineEdit[invalid="true"] { border: 1px solid @bad@; }
QComboBox QAbstractItemView { background: @panel@; border: 1px solid @border@; selection-background-color: @accent@; selection-color: @accent_text@; }
QPushButton { background: @panel2@; border: 1px solid @border@; border-radius: 4px; padding: 6px 12px; }
QPushButton:hover { background: @hover@; }
QPushButton:disabled { color: @muted@; }
QPushButton[primary="true"] { background: @accent@; color: @accent_text@; border: 1px solid @accent@; font-weight: 600; }
QPushButton[primary="true"]:hover { background: @accent_hover@; }
QPushButton[primary="true"]:disabled { background: @border@; color: @muted@; border: 1px solid @border@; }
QCheckBox { spacing: 8px; }
QSlider::groove:horizontal { height: 4px; background: @border@; border-radius: 2px; }
QSlider::sub-page:horizontal { background: @accent@; border-radius: 2px; }
QSlider::handle:horizontal { background: @accent@; width: 14px; margin: -5px 0; border-radius: 7px; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: @border@; border-radius: 5px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 0; }
QScrollBar::handle:horizontal { background: @border@; border-radius: 5px; min-width: 24px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QTableView { background: @input@; alternate-background-color: @panel2@; gridline-color: @border@; border: 1px solid @border@; selection-background-color: @accent@; selection-color: @accent_text@; }
QHeaderView::section { background: @panel2@; color: @muted@; border: none; border-bottom: 1px solid @border@; padding: 5px 8px; font-weight: 600; }
QProgressBar { background: @border@; border: none; border-radius: 3px; max-height: 6px; min-height: 6px; text-align: center; }
QProgressBar::chunk { background: @accent@; border-radius: 3px; }
QStatusBar { background: @panel@; border-top: 1px solid @border@; }
QStatusBar::item { border: none; }
QSplitter::handle { background: @border@; }
QLabel[muted="true"] { color: @muted@; }
QLabel[small="true"] { font-size: 11px; }
QLabel#h1 { font-size: 16px; font-weight: 600; }
QLabel#sectionTitle { font-size: 13px; font-weight: 600; }
QLabel#statusMsg[level="ok"] { color: @ok@; }
QLabel#statusMsg[level="bad"] { color: @bad@; }
QLabel#statusMsg[level="muted"] { color: @muted@; }
QFrame#card { background: @panel2@; border: 1px solid @border@; border-radius: 6px; }
QFrame#card QLabel { background: transparent; }
QLabel#cardTitle { color: @muted@; font-size: 11px; }
QLabel#cardValue { font-size: 17px; font-weight: 600; }
QLabel#chip { border-radius: 9px; padding: 2px 10px; font-size: 11px; font-weight: 600; color: @accent_text@; }
QLabel#chip[level="ok"] { background: @ok@; }
QLabel#chip[level="warn"] { background: @warn@; }
QLabel#chip[level="bad"] { background: @bad@; }
QLabel#chip[level="info"] { background: @info@; }
QFrame#tile { background: @panel2@; border: 1px solid @border@; border-radius: 8px; }
QFrame#tile QLabel { background: transparent; }
QFrame#bottomBar { background: @panel@; border-top: 1px solid @border@; }
QFrame#bottomBar QLabel { background: transparent; }
QFrame#banner { background: @panel2@; border: 1px solid @border@; border-left: 4px solid @info@; border-radius: 4px; }
QFrame#banner[level="bad"] { border-left: 4px solid @bad@; }
QFrame#banner[level="warn"] { border-left: 4px solid @warn@; }
QFrame#banner[level="ok"] { border-left: 4px solid @ok@; }
QFrame#banner QLabel { background: transparent; }
QLabel#toast { background: @toast_bg@; color: @toast_text@; border-radius: 6px; padding: 9px 16px; font-weight: 600; }
QLabel#emptyTitle { font-size: 16px; font-weight: 600; }
QLabel#emptyIcon { font-size: 34px; }
"""


def build_qss(t, extra=""):
    out = QSS + extra
    for k, v in t.items():
        out = out.replace(f"@{k}@", str(v))
    return out


def build_palette(t):
    p = QPalette()
    C = QColor
    p.setColor(QPalette.ColorRole.Window, C(t["bg"]))
    p.setColor(QPalette.ColorRole.WindowText, C(t["text"]))
    p.setColor(QPalette.ColorRole.Base, C(t["input"]))
    p.setColor(QPalette.ColorRole.AlternateBase, C(t["panel2"]))
    p.setColor(QPalette.ColorRole.ToolTipBase, C(t["panel"]))
    p.setColor(QPalette.ColorRole.ToolTipText, C(t["text"]))
    p.setColor(QPalette.ColorRole.Text, C(t["text"]))
    p.setColor(QPalette.ColorRole.Button, C(t["panel2"]))
    p.setColor(QPalette.ColorRole.ButtonText, C(t["text"]))
    p.setColor(QPalette.ColorRole.PlaceholderText, C(t["muted"]))
    p.setColor(QPalette.ColorRole.Highlight, C(t["accent"]))
    p.setColor(QPalette.ColorRole.HighlightedText, C(t["accent_text"]))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, C(t["muted"]))
    return p


def resolve_theme(mode):
    if mode == "system":
        try:
            scheme = QGuiApplication.styleHints().colorScheme()
            return "light" if scheme == Qt.ColorScheme.Light else "dark"
        except Exception:
            return "dark"
    return mode if mode in THEMES else "dark"


def apply_theme(mode, extra_qss=""):
    """Swap the live tokens and restyle the whole app. Returns the token dict now in use."""
    t = THEMES[resolve_theme(mode)]
    T.clear()
    T.update(t)
    app = QApplication.instance()
    app.setStyle("Fusion")
    app.setPalette(build_palette(t))
    app.setStyleSheet(build_qss(t, extra_qss))
    return t


def repolish(w):
    w.style().unpolish(w)
    w.style().polish(w)
    w.update()


def set_prop(w, name, value):
    """Change a dynamic property used by a QSS selector (e.g. level="bad") and restyle the widget."""
    w.setProperty(name, value)
    repolish(w)


def style_axes(ax):
    """Theme a matplotlib Axes from the live tokens (call again after a theme change)."""
    ax.set_facecolor(T["panel"])
    ax.tick_params(colors=T["text"])
    for sp in ax.spines.values():
        sp.set_edgecolor(T["border"])


def setup_matplotlib_fonts():
    import matplotlib
    matplotlib.rcParams["font.family"] = "sans-serif"
    matplotlib.rcParams["font.sans-serif"] = FONT_FAMILIES


# ===================================================================== icons
def make_icon(name, color):
    """Lucide icon tinted with `color` (files in resources/icons/ui, see mcx_studio.icons)."""
    from mcx_studio.icons import icon
    return icon(name, color)


# ===================================================================== small reusable widgets
def clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w:
            w.deleteLater()
        elif item.layout():
            clear_layout(item.layout())


def label(text, muted=False, small=False, name=None):
    """QLabel with the kit's text roles: muted (secondary), small (11px), name='h1' / 'sectionTitle'."""
    l = QLabel(text)
    if muted:
        l.setProperty("muted", True)
    if small:
        l.setProperty("small", True)
    if name:
        l.setObjectName(name)
    return l


class _Refit(QObject):
    """Re-measures fixed_width() widgets when their style or font changes (theme switch)."""

    def eventFilter(self, obj, ev):
        if ev.type() in (QEvent.Type.StyleChange, QEvent.Type.FontChange, QEvent.Type.Show):
            QTimer.singleShot(0, lambda o=obj: refit(o))
        return False


_REFIT = None


def fixed_width(w, *texts):
    """Give a button (or label) whose text changes - Connect / Disconnect, Edit / Done,
    "Alarms (3)" - the width of its longest text, so the toolbar around it never moves.
    `w` may be a QAction in a toolbar: pass the widget (toolbar.widgetForAction)."""
    global _REFIT
    if _REFIT is None:
        _REFIT = _Refit(QApplication.instance())
    w.setProperty("fixedTexts", [t for t in texts if t])
    w.installEventFilter(_REFIT)
    refit(w)


def refit(w):
    """Measure again (after a theme change or an icon-only / text toolbar switch)."""
    texts = w.property("fixedTexts")
    if not texts or getattr(w, "_refitting", False):
        return
    w.setMinimumWidth(0)
    if isinstance(w, QToolButton) and w.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly:
        return
    w._refitting = True                      # setText below must not re-enter through events
    w.ensurePolished()
    cur, best = w.text(), 0
    for t in texts:
        w.setText(t)
        best = max(best, w.sizeHint().width())
    w.setText(cur)
    w._refitting = False
    w.setMinimumWidth(best)


def vsep():
    """Separator for a hand-built bar (QFrame#docBar ...): drawn like QToolBar::separator
    (same QSS: 1 px @border@ line, same margins), so every toolbar looks the same."""
    f = QFrame(); f.setObjectName("vsep"); f.setFrameShape(QFrame.Shape.NoFrame)
    return f


def primary_button(text):
    b = QPushButton(text)
    b.setProperty("primary", True)
    return b


class AccordionSection(QWidget):
    def __init__(self, title, content, expanded=True):
        super().__init__()
        self.title, self.content = title, content
        self.header = QToolButton()
        self.header.setObjectName("accHeader")
        self.header.setCheckable(True)
        self.header.setChecked(expanded)
        self.header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        content.setVisible(expanded)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(self.header)
        lay.addWidget(content)
        self.header.toggled.connect(self._toggled)
        self._refresh()

    def _refresh(self):
        self.header.setText(f"{'▼' if self.header.isChecked() else '▶'}  {self.title}")

    def _toggled(self, on):
        self.content.setVisible(on)
        self._refresh()


class Accordion(QWidget):
    def __init__(self):
        super().__init__()
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(10, 10, 10, 10)
        self.lay.setSpacing(10)
        self.lay.addStretch(1)

    def add(self, title, content, expanded=True):
        self.lay.insertWidget(self.lay.count() - 1, AccordionSection(title, content, expanded))


class Card(QFrame):
    """KPI card: small muted title over a 17px value."""
    def __init__(self, title):
        super().__init__()
        self.setObjectName("card")
        self.title = QLabel(title)
        self.title.setObjectName("cardTitle")
        self.value = QLabel("-")
        self.value.setObjectName("cardValue")
        self.value.setWordWrap(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 10)
        lay.setSpacing(2)
        lay.addWidget(self.title)
        lay.addWidget(self.value)

    def set_value(self, v):
        self.value.setText("-" if v in (None, "") else str(v))


class Chip(QLabel):
    """Status pill. level: ok | warn | bad | info."""
    def __init__(self, text="", level="ok"):
        super().__init__()
        self.setObjectName("chip")
        self.set(text, level)

    def set(self, text, level):
        self.setText(text)
        set_prop(self, "level", level)


def tile():
    """Rounded panel for repeated items (e.g. day cards). Returns (frame, vbox layout)."""
    f = QFrame()
    f.setObjectName("tile")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(18, 14, 18, 14)
    lay.setSpacing(6)
    return f, lay


class EmptyState(QWidget):
    def __init__(self, icon, title, text, button_text=None, callback=None):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(8)
        i = QLabel(icon)
        i.setObjectName("emptyIcon")
        i.setAlignment(Qt.AlignmentFlag.AlignCenter)
        t = QLabel(title)
        t.setObjectName("emptyTitle")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        d = QLabel(text)
        d.setWordWrap(True)
        d.setAlignment(Qt.AlignmentFlag.AlignCenter)
        d.setProperty("muted", True)
        d.setMaximumWidth(360)
        lay.addWidget(i)
        lay.addWidget(t)
        lay.addWidget(d, alignment=Qt.AlignmentFlag.AlignCenter)
        if button_text:
            b = primary_button(button_text)
            if callback:
                b.clicked.connect(callback)
            lay.addWidget(b, alignment=Qt.AlignmentFlag.AlignCenter)


class Banner(QFrame):
    def __init__(self, text, level, action_text, action, on_close):
        super().__init__()
        self.setObjectName("banner")
        self.setProperty("level", level)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 6, 8, 6)
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lay.addWidget(lbl, stretch=1)
        if action_text and action:
            b = QPushButton(action_text)
            b.clicked.connect(action)
            lay.addWidget(b)
        close = QToolButton()
        close.setText("✕")
        close.clicked.connect(on_close)
        lay.addWidget(close)


class BannerHost(QWidget):
    """Stack of dismissible banners, one per key (posting the same key replaces it)."""
    def __init__(self):
        super().__init__()
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(8, 8, 8, 0)
        self.lay.setSpacing(6)
        self.items = {}

    def post(self, key, text, level="info", action_text=None, action=None):
        self.dismiss(key)
        b = Banner(text, level, action_text, action, lambda: self.dismiss(key))
        self.items[key] = b
        self.lay.addWidget(b)

    def dismiss(self, key):
        b = self.items.pop(key, None)
        if b:
            self.lay.removeWidget(b)
            b.deleteLater()


class Toast(QLabel):
    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("toast")
        self.hide()
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.hide)

    def popup(self, text, ms=2600):
        self.setText(text)
        self.adjustSize()
        p = self.parentWidget()
        self.move((p.width() - self.width()) // 2, p.height() - self.height() - 56)
        self.show()
        self.raise_()
        self.timer.start(ms)


# ===================================================================== application shell
class AppWindow(QMainWindow):
    """
    Standard layout:
        menu bar  (File | View > Theme, panels, Reset layout | Help)
        toolbar   (actions ... search ... spacer ... settings)
        left dock  | central: banners + main view (+ optional bottom bar) | right dock: tabs
        status bar (message ......... progress, cancel, permanent labels)
    Subclasses override build_left / build_center / build_right / build_toolbar_items.
    """
    TITLE = "App"
    LEFT_TITLE, RIGHT_TITLE = "Panel", "Results"
    LEFT_W, RIGHT_W = 330, 680

    def __init__(self):
        super().__init__()
        self.setWindowTitle(self.TITLE)
        self.resize(1640, 940)
        self.qs = QSettings()
        self.theme_mode = str(self.qs.value("theme", "dark"))
        self._icons = []           # (target, icon name, token) - re-tinted on theme change
        self.theme_listeners = []  # callables run after every theme change (redraw charts etc.)
        self._busy = 0

        self.toast = Toast(self)
        self.banners = BannerHost()
        self.build_statusbar()
        self.build_central()
        self.build_docks()
        self.build_menus()
        self.build_toolbar()

        self._default_state = self.saveState()
        self.apply_theme()
        self.restore_ui()
        try:
            QGuiApplication.styleHints().colorSchemeChanged.connect(
                lambda *_: self.theme_mode == "system" and self.apply_theme())
        except Exception:
            pass

    # ------------------------------------------------------------ override points
    def build_left(self):
        return Accordion()

    def build_center(self):
        return EmptyState("🗺️", "Nothing here yet", "Override build_center() to put your main view here.")

    def build_bottom_bar(self):
        return None                # optional QFrame with objectName "bottomBar"

    def build_right(self):
        return QTabWidget()

    def build_toolbar_items(self, tb):
        pass

    def build_extra_menus(self, mb):
        pass

    # ------------------------------------------------------------ building blocks
    def act(self, text, slot, shortcut=None, icon=None):
        a = QAction(text, self)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        a.triggered.connect(slot)
        if icon:
            self.reg_icon(a, icon)
        return a

    def reg_icon(self, target, name, token="text"):
        self._icons.append((target, name, token))
        target.setIcon(make_icon(name, T[token]))

    def refresh_icons(self):
        alive = []
        for target, name, token in self._icons:
            try:
                target.setIcon(make_icon(name, T[token]))
            except RuntimeError:                 # widget already deleted (closed tile, view ...)
                continue
            alive.append((target, name, token))
        self._icons = alive

    def build_statusbar(self):
        sb = QStatusBar()
        self.setStatusBar(sb)
        self.status_msg = QLabel("Ready")
        self.status_msg.setObjectName("statusMsg")
        self.status_msg.setProperty("level", "muted")
        self.progress = QProgressBar()
        self.progress.setFixedWidth(160)
        self.progress.setTextVisible(False)
        self.progress.hide()
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.hide()
        sb.addWidget(self.status_msg, 1)
        sb.addPermanentWidget(self.progress)
        sb.addPermanentWidget(self.cancel_btn)

    def add_status_label(self, text=""):
        l = label(text, muted=True)
        self.statusBar().addPermanentWidget(l)
        return l

    def set_status(self, text, level="muted"):
        self.status_msg.setText(text)
        set_prop(self.status_msg, "level", level)

    def busy(self, on, cancellable=False):
        self._busy += 1 if on else -1
        b = self._busy > 0
        self.progress.setRange(0, 0)       # indeterminate
        self.progress.setVisible(b)
        self.cancel_btn.setVisible(b and cancellable)

    def build_central(self):
        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.banners)
        self.center = self.build_center()
        lay.addWidget(self.center, stretch=1)
        bottom = self.build_bottom_bar()
        if bottom is not None:
            lay.addWidget(bottom)
        self.setCentralWidget(central)

    def build_docks(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.left = self.build_left()
        scroll.setWidget(self.left)
        scroll.setMinimumWidth(300)
        self.left_dock = QDockWidget(self.LEFT_TITLE, self)
        self.left_dock.setObjectName("dockLeft")          # objectName needed for saveState()
        self.left_dock.setWidget(scroll)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.left_dock)

        self.right = self.build_right()
        self.right_dock = QDockWidget(self.RIGHT_TITLE, self)
        self.right_dock.setObjectName("dockRight")
        self.right_dock.setWidget(self.right)
        self.right_dock.setMinimumWidth(460)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.right_dock)
        self.resizeDocks([self.left_dock, self.right_dock], [self.LEFT_W, self.RIGHT_W], Qt.Orientation.Horizontal)

    def build_menus(self):
        mb = self.menuBar()
        self.file_menu = mb.addMenu("&File")
        self.file_menu.addAction(self.act("Exit", self.close, "Ctrl+Q"))

        v = mb.addMenu("&View")
        theme = v.addMenu("Theme")
        grp = QActionGroup(self)
        grp.setExclusive(True)
        for text, mode in (("Dark", "dark"), ("Light", "light"), ("Follow system", "system")):
            a = QAction(text, self, checkable=True)
            a.setChecked(self.theme_mode == mode)
            a.triggered.connect(lambda _=False, md=mode: self.set_theme(md))
            grp.addAction(a)
            theme.addAction(a)
        v.addSeparator()
        v.addAction(self.left_dock.toggleViewAction())
        v.addAction(self.right_dock.toggleViewAction())
        v.addAction(self.act("Reset panel layout", lambda: self.restoreState(self._default_state)))
        self.view_menu = v

        self.build_extra_menus(mb)
        self.help_menu = mb.addMenu("&Help")

    def build_toolbar(self):
        tb = QToolBar("Main")
        tb.setObjectName("mainToolbar")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(tb)
        self.toolbar = tb
        self.build_toolbar_items(tb)

    def add_search(self, tb, placeholder="Search   (Ctrl+L)", on_enter=None):
        e = QLineEdit()
        e.setPlaceholderText(placeholder)
        e.setClearButtonEnabled(True)
        e.setMinimumWidth(340)
        if on_enter:
            e.returnPressed.connect(lambda: on_enter(e.text()))
        sa = e.addAction(make_icon("search", T["muted"]), QLineEdit.ActionPosition.LeadingPosition)
        self.reg_icon(sa, "search", "muted")
        tb.addWidget(e)
        focus = self.act("Search", lambda: e.setFocus(), "Ctrl+L")
        self.addAction(focus)
        return e

    @staticmethod
    def add_spacer(tb):
        s = QWidget()
        s.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(s)

    # ------------------------------------------------------------ theme / state
    def set_theme(self, mode):
        self.theme_mode = mode
        self.qs.setValue("theme", mode)
        self.apply_theme()

    def apply_theme(self):
        apply_theme(self.theme_mode)
        self.refresh_icons()
        for fn in self.theme_listeners:
            fn()

    def restore_ui(self):
        geo, state = self.qs.value("geometry"), self.qs.value("windowState")
        if geo:
            self.restoreGeometry(geo)
        if state:
            self.restoreState(state)

    def closeEvent(self, e):
        self.qs.setValue("geometry", self.saveGeometry())
        self.qs.setValue("windowState", self.saveState())
        super().closeEvent(e)


def run(window_cls, org="MyOrg", app="MyApp"):
    QCoreApplication.setOrganizationName(org)
    QCoreApplication.setApplicationName(app)
    a = QApplication.instance() or QApplication(sys.argv)
    w = window_cls()
    w.show()
    return a.exec()


# ===================================================================== demo
class DemoWindow(AppWindow):
    TITLE = "UI kit demo"
    LEFT_TITLE, RIGHT_TITLE = "Layers & Location", "Results"

    def build_left(self):
        acc = Accordion()
        box = QWidget()
        l = QVBoxLayout(box)
        l.setContentsMargins(2, 2, 2, 2)
        l.addWidget(QCheckBox("Show boundaries"))
        l.addWidget(label("Opacity", muted=True, small=True))
        s = QSlider(Qt.Orientation.Horizontal)
        s.setValue(60)
        l.addWidget(s)
        acc.add("Layers", box)
        box2 = QWidget()
        l2 = QVBoxLayout(box2)
        l2.setContentsMargins(2, 2, 2, 2)
        c = QComboBox()
        c.addItems(["Bangkok", "Chiang Mai"])
        l2.addWidget(c)
        acc.add("Location", box2)
        return acc

    def build_right(self):
        tabs = QTabWidget()
        summary = QWidget()
        v = QVBoxLayout(summary)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(10)
        v.addWidget(label("Summary", name="h1"))
        grid = QGridLayout()
        grid.setSpacing(8)
        for i, (t, val) in enumerate([("Soil group", "Clay loam"), ("Topsoil pH", "6.2"),
                                      ("District", "Mueang"), ("Fertility", "Medium")]):
            card = Card(t)
            card.set_value(val)
            grid.addWidget(card, i // 2, i % 2)
        v.addLayout(grid)
        row = QHBoxLayout()
        for text, lvl in (("Dry", "warn"), ("Optimal", "ok"), ("Wet", "info"), ("Error", "bad")):
            row.addWidget(Chip(text, lvl))
        row.addStretch(1)
        v.addLayout(row)
        days = QHBoxLayout()
        for d in ("Mon", "Tue", "Wed"):
            f, fl = tile()
            for w in (QLabel(f"<b>{d}</b>"), QLabel("☀️"), QLabel("<b>34°</b> / 25°C"), label("ET₀ 4.1 mm/d", True, True)):
                w.setAlignment(Qt.AlignmentFlag.AlignCenter)
                fl.addWidget(w)
            days.addWidget(f)
        days.addStretch(1)
        v.addLayout(days)
        v.addStretch(1)
        tabs.addTab(summary, "Summary")
        tabs.addTab(EmptyState("📈", "No forecast yet", "Click the map to fetch one.", "Open file", lambda: None), "Forecast")
        return tabs

    def build_bottom_bar(self):
        f = QFrame()
        f.setObjectName("bottomBar")
        l = QHBoxLayout(f)
        l.setContentsMargins(10, 6, 10, 6)
        l.addWidget(QLabel("Bottom bar (e.g. date slider)"))
        l.addStretch(1)
        return f

    def build_toolbar_items(self, tb):
        tb.addAction(self.act("Open...", lambda: self.toast.popup("Open clicked"), "Ctrl+O", "folder-open"))
        tb.addSeparator()
        self.add_search(tb, on_enter=lambda q: self.set_status(f"Searched: {q}", "ok"))
        tb.addSeparator()
        tb.addAction(self.act("Fetch", lambda: self.banners.post("err", "Request failed (demo).", "bad", "Retry",
                                                                  lambda: self.banners.dismiss("err")),
                              "Ctrl+R", "cloud-download"))
        self.add_spacer(tb)
        tb.addAction(self.act("Settings", lambda: self.banners.post("cred", "No credentials set.", "warn"),
                              None, "sliders"))


if __name__ == "__main__":
    sys.exit(run(DemoWindow, "SpexDemo", "UiKitDemo"))
