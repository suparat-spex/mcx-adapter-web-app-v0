"""MCX Studio - the desktop setup tool of MCX Adapter (PySide6): connect -> discover -> expose ->
test -> publish, tool profiles, safety and limits, audit log. The engine is mcx_core (no Qt)."""
import os

from mcx_core import APP_NAME, ORG_NAME, __version__

STUDIO_NAME = "MCX Studio"
os.environ.setdefault("QT_API", "pyside6")

__all__ = ["APP_NAME", "ORG_NAME", "STUDIO_NAME", "__version__"]
