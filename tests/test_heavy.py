"""bin/hwde runs its heavy commands (the suite, check-all, the router, whole-
board scripts) under `hive agent heavy`, so turns take turns at loading the
machine (issue #94: ten hwde suites and freerouting at once took a 20-core
load to 137). A fake hive and a fake venv python record what would run."""
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _run(tmp_path, args, *, heavy_op=True, held=False):
    bin_ = tmp_path / "bin"
    bin_.mkdir(exist_ok=True)
    log = tmp_path / "log"
    help_ = '  heavy     --what "the test suite" -- cmd args' if heavy_op else "  progress"
    hive = bin_ / "hive"
    hive.write_text(f"""#!/bin/sh
[ "$2" = help ] && {{ echo '{help_}'; exit 0; }}
echo "hive $*" >>"{log}"
shift 2; while [ "$1" != -- ]; do shift; done; shift
exec "$@"
""")
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True, exist_ok=True)
    py = venv / "bin" / "python"
    py.write_text(f'#!/bin/sh\necho "python held=$HWDE_HEAVY_HELD $*" >>"{log}"\n')
    for f in (hive, py):
        f.chmod(0o755)
    (venv / ".hwde-lock").touch()
    env = {k: v for k, v in os.environ.items() if k != "HWDE_HEAVY_HELD"}
    env.update(PATH=f"{bin_}:{env['PATH']}", HWDE_VENV=str(venv), DISPLAY=":99",
               HWDE_HOME=str(tmp_path / "state"), HWDE_KICAD_CLI="/nonexistent",
               HWDE_KNOWLEDGE_SOURCES=str(tmp_path))  # check-all would fetch them
    if held:
        env["HWDE_HEAVY_HELD"] = "1"
    out = subprocess.run([str(ROOT / "bin" / "hwde"), *args], env=env, cwd=tmp_path,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return log.read_text().splitlines()


@pytest.mark.parametrize("args,what", [
    (["route_auto", "--pcb", "x.kicad_pcb"], "hwde route_auto"),
    (["gate.py", "dfm"], "hwde gate"),
    (["check-all"], "hwde check-all (every board's gates)"),
])
def test_heavy_commands_wait_for_a_lease(tmp_path, args, what):
    hive, py = _run(tmp_path, args)
    assert hive.startswith(f"hive agent heavy --what {what} -- "), hive
    assert py.startswith("python held=1 "), py


def test_light_scripts_run_bare(tmp_path):
    assert _run(tmp_path, ["state", "show"])[0].startswith("python held= ")


def test_inside_a_lease_it_runs_bare(tmp_path):
    """check-all's gates and tests that run hwde don't ask again."""
    lines = _run(tmp_path, ["route_auto"], held=True)
    assert len(lines) == 1 and lines[0].startswith("python held=1 "), lines


def test_a_hive_without_the_op_runs_it_bare(tmp_path):
    lines = _run(tmp_path, ["route_auto"], heavy_op=False)
    assert len(lines) == 1 and lines[0].startswith("python held=1 "), lines
