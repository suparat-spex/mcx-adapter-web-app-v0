"""
icons.py - every icon in MCX Studio comes from here.

UI icons are Lucide SVG files in resources/icons/ui/ (stroke="currentColor"): to add
one, save the SVG from https://lucide.dev into that folder and call icon("name").
They are tinted per theme token and cached, so theme switches stay fast.

  icon(name, color=None) -> QIcon       buttons, menus, tabs (color defaults to theme text)
  check_icon_file / grip_icon_file      file paths for QSS (stylesheets only load files)
  swatch(color)                         channel colour chip
  dot(token)                            status dot (source online / error ...)
  app_icon()                            window, taskbar (resources/icons/app/mcx.ico)
"""
from __future__ import annotations

import logging
import tempfile
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from mcx_studio import paths

log = logging.getLogger(__name__)
_QSS_DIR = Path(tempfile.gettempdir()) / "mcx_studio_ui"


@lru_cache(maxsize=None)
def svg_source(name: str) -> str:
    f = paths.resource("icons", "ui", f"{name}.svg")
    try:
        return f.read_text(encoding="utf-8")
    except OSError:
        log.warning("icon %r not found in %s", name, f.parent)
        return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"/>'


@lru_cache(maxsize=512)
def _render(name: str, color: str, px: int) -> QIcon:
    svg = svg_source(name).replace("currentColor", color)
    pm = QPixmap(px * 2, px * 2)                 # drawn at 2x, shown sharp on HiDPI
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    QSvgRenderer(QByteArray(svg.encode("utf-8"))).render(p)
    p.end()
    pm.setDevicePixelRatio(2)
    return QIcon(pm)


def icon(name: str, color: str | None = None, size: int = 24) -> QIcon:
    if color is None:
        from mcx_studio.theme.ui_kit import T
        color = T["text"]
    return _render(name, QColor(color).name(), size)


def _qss_file(name: str, svg: str) -> str:
    _QSS_DIR.mkdir(exist_ok=True)
    f = _QSS_DIR / name
    if not f.exists():
        f.write_text(svg, encoding="utf-8")
    return f.as_posix()


def check_icon_file(color: str) -> str:
    """Check box tick for QSS `image: url(...)`."""
    return _qss_file(f"check_{color.strip('#')}.svg",
                     f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
                     f'stroke="{color}" stroke-width="3.5" stroke-linecap="round" '
                     f'stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>')


def grip_icon_file(color: str, vertical: bool) -> str:
    """Three-dot splitter grip (vertical = dots stacked, for side-by-side panels)."""
    dots = "".join(f'<circle cx="{3 if vertical else 4 + 6 * i}" cy="{4 + 6 * i if vertical else 3}" r="1.2"/>'
                   for i in range(3))
    w, h = (6, 20) if vertical else (20, 6)
    return _qss_file(f"grip_{'v' if vertical else 'h'}_{color.strip('#')}.svg",
                     f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
                     f'viewBox="0 0 {w} {h}" fill="{color}">{dots}</svg>')


def swatch(color: str, size: int = 12) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(QColor(color))
    return QIcon(pm)




def dot(color: str, size: int = 8) -> QIcon:
    """Filled status dot (source online / error), drawn at 2x."""
    pm = QPixmap(2 * (size + 6), 2 * (size + 6)); pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm); p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(color))
    p.drawEllipse(6, 6, 2 * size, 2 * size); p.end()
    pm.setDevicePixelRatio(2)
    return QIcon(pm)


def app_icon() -> QIcon:
    for name in ("mcx.ico", "mcx.svg"):
        f = paths.resource("icons", "app", name)
        if f.exists():
            return QIcon(str(f))
    return QIcon()
