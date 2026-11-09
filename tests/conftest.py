import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture()
def demo(tmp_path, monkeypatch):
    """A fresh demo project in a temp folder: (project, path, api keys)."""
    monkeypatch.setenv("MCX_DATA_DIR", str(tmp_path / "data"))
    from mcx_core import demo as d
    from mcx_core.config import Project
    path, keys = d.create(tmp_path / "demo")
    return Project.load(path), str(path), keys


@pytest.fixture()
def runtime(demo):
    from mcx_core.runtime import Runtime
    p, path, keys = demo
    rt = Runtime(p, path, user="test")
    rt.start()
    yield rt
    rt.stop()


sys.dont_write_bytecode = True
