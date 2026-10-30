"""A slow kicad-cli on a strained machine doesn't abort the whole test run at
collection (issue #252): conftest asks the pinned kicad-cli its version to
gate the `kicad_recorded` tests, and a probe that times out made
find_kicad_cli raise EnvError inside pytest_collection_modifyitems, an
INTERNALERROR that ran no test at all. Now those tests skip with the reason
and the rest run."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".claude" / "skills" / "hwde" / "scripts" / "lib"))

import env  # noqa: E402

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="sh stub")


def test_a_pin_whose_probe_times_out_skips_recorded_tests(tmp_path):
    cli = tmp_path / "kicad-cli"
    cli.write_text("#!/bin/sh\nexec sleep 30\n")
    cli.chmod(0o755)
    (tmp_path / "test_x.py").write_text(
        "import pytest\n"
        "@pytest.mark.kicad_recorded\n"
        "def test_recorded():\n    pass\n"
        "def test_plain():\n    pass\n")
    # run this repo's conftest on the scratch tests, with the version probe
    # cut short so a hang reads as a timeout in a second, not two minutes
    (tmp_path / "conftest.py").write_text(
        f"import sys\nsys.path.insert(0, {str(env.__file__.rsplit('/', 1)[0])!r})\n"
        "import env\nenv._VERSION_TIMEOUT = 1\n"
        + (ROOT / "tests" / "conftest.py").read_text())
    e = {k: v for k, v in os.environ.items() if not k.startswith(("HWDE_", "AIEE_"))}
    e["HWDE_KICAD_CLI"] = str(cli)
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-rs",
         "-o", "markers=kicad_recorded", str(tmp_path)],
        cwd=tmp_path, env=e, capture_output=True, text=True, timeout=120)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "INTERNALERROR" not in out
    assert "1 passed, 1 skipped" in out
    assert "no usable kicad-cli" in out
