"""Shared by the probes in this folder (bin/check_all.py runs each one).

A probe exits 0 while its cause stands and 10 once it is fixed, with one line
on stdout saying what it saw; any other exit is a probe that couldn't run.
They run with the venv python, the skill's scripts on PYTHONPATH and the
HWDE_* pins of bin/hwde, from the repo root.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / ".claude" / "skills" / "hwde" / "scripts"
HWDE = os.environ.get("HWDE_BIN", str(ROOT / "bin" / "hwde"))


def stands(why: str):
    print(f"stands: {why}")
    sys.exit(0)


def fixed(why: str):
    print(f"fixed: {why}")
    sys.exit(10)


def scratch() -> Path:
    d = Path(tempfile.mkdtemp(prefix="hwde-probe-"))
    import atexit
    atexit.register(shutil.rmtree, d, True)
    return d


def board_copy(name: str) -> Path:
    """A scratch copy of boards/<name>, so a probe never edits the repo."""
    dst = scratch() / name
    shutil.copytree(ROOT / "boards" / name, dst,
                    ignore=shutil.ignore_patterns("__pycache__", "fab-check"))
    return dst


def hwde(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run([HWDE, *args], cwd=cwd, capture_output=True,
                          text=True, stdin=subprocess.DEVNULL)


def gate(name: str, ws: Path, board: str) -> bool:
    f = f"kicad/{board}.kicad_sch" if name == "erc" else f"kicad/{board}.kicad_pcb"
    out = ws / f"probe-{name}.json"
    r = hwde("gate", "--gate", name, f, "--workspace", ".", "--out", str(out), cwd=ws)
    if r.returncode not in (0, 1):
        raise SystemExit(f"gate {name} errored ({r.returncode}): "
                         f"{(r.stdout + r.stderr).strip().splitlines()[-1:]}")
    return r.returncode == 0
