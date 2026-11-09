"""
paths.py - where MCX Adapter keeps its files (the only module that knows).

                 from source (dev)                  installed (service / EXE)
  user data      ~/Documents/MCX Adapter/           %PROGRAMDATA%/MCX Adapter (Windows service)
  projects       <user data>/Projects/*.mcx
  audit log      next to the project: <project dir>/audit/YYYY-MM.jsonl
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from mcx_core import APP_NAME

FROZEN = getattr(sys, "frozen", False)


def data_dir() -> Path:
    env = os.environ.get("MCX_DATA_DIR")
    if env:
        return Path(env)
    if FROZEN and sys.platform == "win32" and os.environ.get("PROGRAMDATA"):
        return Path(os.environ["PROGRAMDATA"]) / APP_NAME
    return Path.home() / "Documents" / APP_NAME


def projects_dir() -> Path:
    return data_dir() / "Projects"


def demo_dir() -> Path:
    return data_dir() / "Demo"


def audit_dir(project_file: str | os.PathLike | None) -> Path:
    """Audit files live next to the project (one folder per adapter)."""
    if project_file:
        return Path(project_file).resolve().parent / "audit"
    return data_dir() / "audit"


def repo_root() -> Path | None:
    if FROZEN:
        return None
    root = Path(__file__).resolve().parents[2]
    return root if (root / "pyproject.toml").exists() else None


def docs_dir() -> Path | None:
    root = repo_root()
    d = (root / "docs") if root else Path(sys.executable).parent / "docs"
    return d if d.is_dir() else None
