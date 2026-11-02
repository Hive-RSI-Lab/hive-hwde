"""bin/hwde's venv step: the shared venv survives a new clone's fresh lock mtime."""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "requirements.lock"
OLD = 1_000_000_000  # older than any checkout's lock


def _run(tmp_path, stamp=None, python=True):
    bin_ = tmp_path / "bin"
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    bin_.mkdir()
    log = tmp_path / "uv.log"
    log.touch()
    uv = bin_ / "uv"
    uv.write_text(f'#!/bin/sh\necho "$*" >>"{log}"\n'
                  f'[ "$1" = venv ] && {{ printf "#!/bin/sh\\n" >"{venv}/bin/python"; '
                  f'chmod +x "{venv}/bin/python"; }}\nexit 0\n')
    uv.chmod(0o755)
    py = venv / "bin" / "python"
    if python:
        py.write_text("#!/bin/sh\n")
        py.chmod(0o755)
    if stamp is not None:
        f = venv / ".hwde-lock"
        f.write_text(stamp)
        os.utime(f, (OLD, OLD))
    env = dict(os.environ, PATH=f"{bin_}:{os.environ['PATH']}", HWDE_VENV=str(venv),
               HWDE_HOME=str(tmp_path / "state"))
    out = subprocess.run([str(ROOT / "bin" / "hwde"), "route_critical"], env=env, cwd=tmp_path,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return log.read_text().splitlines(), venv


def test_same_lock_contents_with_a_newer_mtime_leaves_the_venv_alone(tmp_path):
    calls, _ = _run(tmp_path, stamp=LOCK.read_text())
    assert calls == []


def test_changed_lock_updates_without_clearing(tmp_path):
    calls, venv = _run(tmp_path, stamp="old lock\n")
    assert len(calls) == 1 and calls[0].startswith("pip install")
    assert (venv / ".hwde-lock").read_text() == LOCK.read_text()


def test_stamp_from_before_this_change_updates_without_clearing(tmp_path):
    calls, _ = _run(tmp_path, stamp="")
    assert len(calls) == 1 and calls[0].startswith("pip install")


def test_interrupted_build_with_no_stamp_is_finished(tmp_path):
    calls, venv = _run(tmp_path)
    assert len(calls) == 1 and calls[0].startswith("pip install")
    assert (venv / ".hwde-lock").is_file()


def test_missing_python_builds_from_scratch(tmp_path):
    calls, _ = _run(tmp_path, python=False)
    assert calls[0].startswith("venv ") and "--clear" in calls[0]
    assert calls[1].startswith("pip install")
