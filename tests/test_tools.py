"""`hwde tools` fetches the board tools into HOME for a Hive with no shared tools folder.

Nothing here touches the network: the downloads are the pinned urls and shas in bin/tools.py.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import tools  # noqa: E402


def test_every_download_is_pinned():
    for sha in (tools.KICAD_SHA, tools.FR_SHA, tools.KRT_ZIP[1], tools.KRT_SO[1]):
        assert re.fullmatch(r"[0-9a-f]{64}", sha)


def test_check_reports_missing_in_an_empty_dir(tmp_path):
    assert tools.main(["--check", str(tmp_path)]) == 1
    h = tools.have(tmp_path)
    assert not h["kicad"] and not h["freerouting"] and not h["krt"]


def test_check_sees_the_layout_env_py_reads(tmp_path):
    (tmp_path / f"kicad-{tools.KICAD}" / "bin").mkdir(parents=True)
    (tmp_path / f"kicad-{tools.KICAD}" / "bin" / "kicad-cli").write_text("")
    (tmp_path / "freerouting").mkdir()
    (tmp_path / "freerouting" / f"freerouting-{tools.FR_VER}.jar").write_text("")
    rr = tmp_path / "kicad-routing-tools" / f"KiCadRoutingTools-{tools.KRT_VER}" / "plugins" / "rust_router"
    rr.mkdir(parents=True)
    (rr / "grid_router.so").write_text("")
    h = tools.have(tmp_path)
    assert h["kicad"] and h["freerouting"] and h["krt"]


def test_wrapper_follows_a_symlink(tmp_path):
    """`hive module add` links bin/hwde into ~/.local/bin: the wrapper must find its module through it."""
    link = tmp_path / "hwde"
    link.symlink_to(ROOT / "bin" / "hwde")
    out = subprocess.run([str(link), "tools", "--check", str(tmp_path / "t")],
                         capture_output=True, text=True, timeout=120)
    assert "kicad" in out.stdout and "MISSING" in out.stdout
