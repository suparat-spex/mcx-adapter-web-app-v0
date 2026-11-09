"""app.py - start MCX Studio: application names, settings, icon, then the main window."""
from __future__ import annotations

import logging
import sys

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from mcx_core import APP_NAME, ORG_NAME
from mcx_studio.icons import app_icon


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("pymodbus").setLevel(logging.CRITICAL)
    QCoreApplication.setOrganizationName(ORG_NAME)
    QCoreApplication.setApplicationName(APP_NAME)
    app = QApplication.instance() or QApplication(sys.argv if argv is None else argv)
    app.setWindowIcon(app_icon())
    from mcx_studio.main_window import AdapterWindow      # after QApplication: widgets need it
    w = AdapterWindow()
    w.show()
    return app.exec()
