"""The `kicad_recorded` tests skip on any kicad-cli but the one their numbers
came from. Moving the pin without re-recording them left 15 tests skipped on
every run, CI included, with nothing to say so (#795). So the pin and the
recorded version must agree, and a skip for this reason is named in the summary."""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import RECORDED_KICAD

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / ".github" / "actions" / "hwde-tools" / "action.yml"


def _dotted(version) -> str:
    return ".".join(map(str, version))


def test_the_recorded_numbers_are_from_the_pinned_kicad():
    pin = re.search(r"KICAD_VERSION=([\d.]+)", ACTION.read_text(encoding="utf-8")).group(1)
    assert pin == _dotted(RECORDED_KICAD), (
        f"KiCad pin is {pin}, the kicad_recorded tests hold numbers from "
        f"{_dotted(RECORDED_KICAD)}: re-baseline them on the new pin, one at a "
        "time, then move RECORDED_KICAD (tests/conftest.py)")


def test_the_hdi_maze_records_the_same_kicad():
    text = (ROOT / "tests" / "test_hdi_maze.py").read_text(encoding="utf-8")
    assert re.search(r'^RECORDED = "([\d.]+)"', text, re.M).group(1) == _dotted(RECORDED_KICAD)


@pytest.mark.skipif(sys.platform == "win32", reason="sh stub")
def test_another_kicad_names_the_recorded_tests_it_skips(tmp_path):
    cli = tmp_path / "kicad-cli"
    cli.write_text("#!/bin/sh\necho 9.9.9\n")
    cli.chmod(0o755)
    (tmp_path / "test_x.py").write_text(
        "import pytest\n"
        "@pytest.mark.kicad_recorded\n"
        "def test_recorded():\n    pass\n"
        "def test_plain():\n    pass\n")
    (tmp_path / "conftest.py").write_text(
        f"import sys\nsys.path.insert(0, {str(ROOT / 'scripts' / 'lib')!r})\n"
        + (ROOT / "tests" / "conftest.py").read_text())
    e = {k: v for k, v in os.environ.items() if not k.startswith(("HWDE_", "AIEE_"))}
    e["HWDE_KICAD_CLI"] = str(cli)
    e["XDG_STATE_HOME"] = str(tmp_path / "state")
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider",
         "-o", "markers=kicad_recorded", str(tmp_path)],
        cwd=tmp_path, env=e, capture_output=True, text=True, timeout=120)
    out = r.stdout + r.stderr
    assert "1 passed, 1 skipped" in out, out
    assert "kicad_recorded: 1 tests did NOT run" in out, out
    assert "9.9.9" in out, out
