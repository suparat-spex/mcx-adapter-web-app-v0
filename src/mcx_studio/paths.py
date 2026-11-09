"""paths.py - where MCX Studio finds its resources (icons). User data locations: mcx_core.paths."""
from __future__ import annotations

import sys
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)


def resource_dir() -> Path:
    if FROZEN:
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "mcx_studio" / "resources"
    return Path(__file__).resolve().parent / "resources"


def resource(*parts: str) -> Path:
    return resource_dir().joinpath(*parts)
