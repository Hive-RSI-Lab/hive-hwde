"""bin/hwde in a published copy (hwde publish) runs on its own python.

A check sandbox has a fresh HOME and only /work and /hive/tools mounted, so
the copy at /hive/tools/hwde must start with no venv to build and no uv: this
fakes that layout (the copy's python is this test's own) and asks it for its
pins. The publish itself downloads Python and the lock's packages, so it is
not run here.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(hwde: Path, home: Path, *args: str) -> subprocess.CompletedProcess:
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "HIVE_TOOLS": str(home / "no-tools")}
    return subprocess.run([str(hwde), *args], env=env, cwd=home, capture_output=True, text=True)


def test_published_copy_uses_its_own_python(tmp_path):
    copy = tmp_path / "tools" / "hwde"
    (copy / "bin").mkdir(parents=True)
    shutil.copy2(ROOT / "bin" / "hwde", copy / "bin" / "hwde")
    shutil.copy2(ROOT / "requirements.lock", copy / "requirements.lock")
    py = copy / "python" / "cpython-3.14.0-linux-x86_64-gnu" / "bin" / "python3.14"
    py.parent.mkdir(parents=True)
    os.symlink(sys.executable, py)
    home = tmp_path / "home"
    home.mkdir()
    r = run(copy / "bin" / "hwde", home, "env")
    assert r.returncode == 0, r.stderr
    assert f"export HWDE_PY='{py}'" in r.stdout
    assert not (home / ".cache" / "hwde" / "venv").exists()


def test_publish_needs_an_existing_dir(tmp_path):
    r = run(ROOT / "bin" / "hwde", tmp_path, "publish", str(tmp_path / "missing"))
    assert r.returncode == 2
    assert "usage: hwde publish" in r.stderr
