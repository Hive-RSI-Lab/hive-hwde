"""S0 acceptance tests (ai-ee-implementation-plan.md S0).

check_env.py must: exit 0 on this dev machine with pure-JSON stdout, and
exit 1 with actionable remediation when a dependency is missing (simulated
via the HWDE_* env overrides - a set-but-invalid override must fail loudly,
never fall through to discovery).

T6 (env-pin, ladder row 18): an explicit HWDE_KICAD_CLI/HWDE_KICAD_ROOT pin
below KiCad 10 is a stale-pin mistake (10-format boards are unreadable by
9.x) and must be rejected at run start, not deep in the run.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / ".claude" / "skills" / "hwde" / "scripts"
CHECK_ENV = SCRIPTS / "check_env.py"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

from lib import env  # noqa: E402


def run_check_env(extra_env=None, *args):
    env = dict(os.environ)
    env.update(extra_env or {})
    return subprocess.run(
        [sys.executable, str(CHECK_ENV), *args],
        capture_output=True, text=True, env=env, timeout=300, cwd=REPO,
    )


def test_passes_on_dev_machine():
    r = run_check_env()
    assert r.returncode == 0, f"stderr:\n{r.stderr}"
    report = json.loads(r.stdout)  # stdout must be pure JSON
    assert report["status"] == "pass"
    names = {c["name"] for c in report["checks"]}
    assert "kicad-cli" in names
    assert "package:kicad-python" in names
    assert report["resolved"]["kicad_cli"].lower().endswith(
        "kicad-cli.exe" if sys.platform == "win32" else "kicad-cli")


def test_missing_kicad_fails_with_remediation():
    r = run_check_env({"HWDE_KICAD_CLI": r"C:\nonexistent\kicad-cli.exe"})
    assert r.returncode == 1
    report = json.loads(r.stdout)
    assert report["status"] == "fail"
    kc = next(c for c in report["checks"] if c["name"] == "kicad-cli")
    assert kc["status"] == "fail"
    assert "HWDE_KICAD_CLI" in kc["detail"]
    assert "install" in kc.get("remediation", "").lower()


def test_validate_pin_rejects_pre10_versions(tmp_path):
    """Unit: the guard rejects 9.x-shaped pins and unknown versions, accepts
    10.x - no subprocess (stubbed version tuples)."""
    p = tmp_path / "kicad-cli.exe"
    p.write_bytes(b"x")
    assert env._validate_pin(p, "HWDE_KICAD_CLI", ver=(10, 0, 3)) == p
    assert env._validate_pin(p, "HWDE_KICAD_CLI", ver=(11, 1)) == p
    with pytest.raises(env.EnvError, match=r"pins KiCad 9\.0\.5"):
        env._validate_pin(p, "HWDE_KICAD_CLI", ver=(9, 0, 5))
    with pytest.raises(env.EnvError, match="HWDE_KICAD_ROOT"):
        env._validate_pin(p, "HWDE_KICAD_ROOT", ver=(9, 0, 5))
    with pytest.raises(env.EnvError, match="could not be determined"):
        env._validate_pin(p, "HWDE_KICAD_CLI", ver=())


@pytest.mark.skipif(sys.platform != "win32", reason="batch-file stub")
def test_stale_9x_pin_fails_check_env(tmp_path):
    """Integration: a pin that reports 9.0.5 fails the kicad-cli check with
    the stale-pin remediation instead of sailing through >= MIN_KICAD."""
    stub = tmp_path / "kicad-cli.bat"
    stub.write_text("@echo 9.0.5\n", encoding="ascii")
    r = run_check_env({"HWDE_KICAD_CLI": str(stub)})
    assert r.returncode == 1
    report = json.loads(r.stdout)
    kc = next(c for c in report["checks"] if c["name"] == "kicad-cli")
    assert kc["status"] == "fail"
    assert "pins KiCad 9.0.5" in kc["detail"]
    assert "10" in kc["detail"]          # remediation names the pipeline major
    assert kc.get("remediation")


def test_unpinned_discovery_still_resolves_10x():
    """The guard must not touch plain discovery: unset pins resolve 10.x."""
    e = {k: v for k, v in os.environ.items()
         if k not in ("HWDE_KICAD_CLI", "HWDE_KICAD_ROOT")}
    r = subprocess.run([sys.executable, str(CHECK_ENV)],
                       capture_output=True, text=True, env=e, timeout=300,
                       cwd=REPO)
    assert r.returncode == 0, r.stderr
    report = json.loads(r.stdout)
    assert report["resolved"]["kicad_version"].startswith("10.")


def test_missing_java_fails_with_remediation():
    r = run_check_env({"HWDE_JAVA": r"C:\nonexistent\java.exe"})
    assert r.returncode == 1
    report = json.loads(r.stdout)
    j = next(c for c in report["checks"] if c["name"] == "java-for-freerouting")
    assert j["status"] == "fail"
    assert "HWDE_JAVA" in j["detail"]
    rem = j.get("remediation", "").lower()
    assert "adoptium" in rem or "temurin" in rem


def test_out_file_and_quiet():
    out = REPO / "_scratch" / "test_env_report.json"
    if out.exists():
        out.unlink()
    r = run_check_env(None, "--out", str(out), "--quiet")
    assert r.returncode == 0
    assert r.stdout.strip() == ""
    assert r.stderr.strip() == ""
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["status"] == "pass"


def test_lualatex_check_survives_report_gen_import_failure(monkeypatch):
    """pyyaml missing makes report_gen unimportable; check_env reports the
    lualatex check as a warning instead of dying (package:pyyaml says why)."""
    import check_env
    monkeypatch.setitem(sys.modules, "report_gen", None)  # import -> ImportError
    resolved: dict = {}
    c = check_env.check_lualatex(resolved)
    assert c["name"] == "lualatex" and c["status"] == "warn"
    assert "cannot load report_gen" in c["detail"]


# --- lock-packages: venv vs requirements.lock -------------------------------

def _lock_check(monkeypatch, tmp_path, lock_text, installed, platform="linux"):
    import importlib.metadata as md

    import check_env as ce
    lock = tmp_path / "requirements.lock"
    lock.write_text(lock_text, encoding="utf-8")

    def fake_version(name):
        if name in installed:
            return installed[name]
        raise md.PackageNotFoundError(name)
    monkeypatch.setattr(ce.importlib.metadata, "version", fake_version)
    monkeypatch.setattr(ce.sys, "platform", platform)
    return ce.check_lock(lock)


def test_lock_missing_package_fails_with_pip_remedy(monkeypatch, tmp_path):
    c = _lock_check(monkeypatch, tmp_path, "pypdf==6.14.2\nrich==1.0\n", {"rich": "1.0"})
    assert c["status"] == "fail"
    assert "pypdf missing" in c["detail"]
    assert "pip install -r" in c["remediation"] and "requirements.lock" in c["remediation"]


def test_lock_version_mismatch_warns(monkeypatch, tmp_path):
    c = _lock_check(monkeypatch, tmp_path, "rich==1.0\n", {"rich": "0.9"})
    assert c["status"] == "warn" and "rich 0.9 installed, locked 1.0" in c["detail"]


def test_lock_missing_beats_version_drift(monkeypatch, tmp_path):
    c = _lock_check(monkeypatch, tmp_path, "pypdf==6.14.2\nrich==1.0\n", {"rich": "0.9"})
    assert c["status"] == "fail"


def test_lock_skips_windows_only_and_false_markers(monkeypatch, tmp_path):
    text = "pywin32==312\npywin32-ctypes==0.2.3\nfoo==1 ; sys_platform == 'win32'\nrich==1.0\n"
    c = _lock_check(monkeypatch, tmp_path, text, {"rich": "1.0"})
    assert c["status"] == "pass"
    c = _lock_check(monkeypatch, tmp_path, text, {"rich": "1.0"}, platform="win32")
    assert c["status"] == "fail" and "pywin32 missing" in c["detail"]


def test_datasheet_extract_pdf_names_missing_module(monkeypatch, tmp_path, capsys):
    import datasheet_extract as de
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setitem(sys.modules, "pypdf", None)  # import raises ImportError
    rc = de.main(["--pdf", str(pdf)])
    out = capsys.readouterr().out
    assert rc == 2
    assert "missing-module" in out and "pypdf" in out and "check_env" in out
