"""Drop-in check registration (TEST-12, docs/failure-modes.md section 4).

A check joins verify_all and the scorecard through scripts/checks.d/<name>.yaml
and its mutant through tests/golden/manifest.d/<name>.yaml. The seeded-fault
eval builds a dummy check and a sidecar-only mutant of blinky2 (section 3.5:
the fault lives in constraints.json) in a scratch corpus, and asserts the
scorecard gives the drop-in check recall 1.0 and no false alarm on the clean
board - what test_every_check_has_recall asks of every real check.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / ".claude" / "skills" / "hwde" / "scripts"
GOLDEN = REPO / "tests" / "golden"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
import checklib  # noqa: E402
import score_checks as sc  # noqa: E402
import verify_all  # noqa: E402

MUTANT = "test-12-fragment-loaded"

DUMMY_SCRIPT = '''\
"""A drop-in check for the registry test: an error when constraints.json
carries "dummy_fault"."""
import argparse
import json
import sys


def run(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--pcb", required=True)
    ap.add_argument("--constraints", required=True)
    ap.add_argument("--decoupling")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    with open(a.constraints, encoding="utf-8") as fh:
        seeded = json.load(fh).get("dummy_fault")
    vs = [{"check": "check_dummy", "source": "check_dummy",
           "severity": "error", "kind": "fragment_loaded", "net": None,
           "refs": [], "pos": None, "layer": None,
           "msg": "seeded fault",
           "decoupling_passed": a.decoupling is not None}] if seeded else []
    return {"script": "check_dummy",
            "status": "violations" if vs else "pass",
            "counts": {"total": len(vs)}, "violations": vs}, a.out


if __name__ == "__main__":
    payload, out = run()
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    sys.exit(1 if payload["violations"] else 0)
'''

DUMMY_FRAGMENT = """\
name: check_dummy
needs: [constraints]
args: [--constraints, "{constraints}"]
optional:
  decoupling: [--decoupling, "{decoupling}"]
"""


def _scripts_dir(root: Path) -> Path:
    """A scripts dir holding check_dummy.py and its checks.d fragment."""
    d = root / "scripts"
    (d / "checks.d").mkdir(parents=True)
    (d / "check_dummy.py").write_text(DUMMY_SCRIPT, encoding="utf-8")
    (d / "checks.d" / "check_dummy.yaml").write_text(DUMMY_FRAGMENT,
                                                    encoding="utf-8")
    return d


def _golden_dir(root: Path) -> Path:
    """A golden corpus: blinky2 clean, plus one manifest.d mutant whose
    constraints.json carries the seeded fault."""
    g = root / "golden"
    board = g / "blinky2"
    board.mkdir(parents=True)
    for f in ("blinky2.kicad_pcb", "blinky2.kicad_pro", "constraints.json",
              "decoupling.json"):
        shutil.copy(GOLDEN / "blinky2" / f, board / f)
    (g / "manifest.yaml").write_text(
        "version: 1\ngolden_boards:\n  blinky2: {dir: blinky2, layers: 2}\n"
        "mutants: {}\n", encoding="utf-8")
    (g / "manifest.d").mkdir()
    (g / "manifest.d" / "check_dummy.yaml").write_text(
        f"mutants:\n  {MUTANT}:\n    board: blinky2\n"
        "    script: mutations/test_12_fragment_loaded.py\n"
        "    check: check_dummy\n    defect: dummy fault in a sidecar\n"
        "    modes: [TEST-12]\n    expect: {kind: fragment_loaded}\n",
        encoding="utf-8")
    m = g / "mutants" / MUTANT
    m.mkdir(parents=True)
    shutil.copy(board / "blinky2.kicad_pcb", m / "blinky2.kicad_pcb")
    cons = json.loads((board / "constraints.json").read_text(encoding="utf-8"))
    (m / "constraints.json").write_text(
        json.dumps({**cons, "dummy_fault": True}), encoding="utf-8")
    return g


# ------------------------------------------------------------ seeded fault

def test_scorecard_catches_a_dropin_check_mutant(tmp_path):
    """fragment_loaded: the drop-in check is scored, catches its manifest.d
    mutant and raises nothing on the clean golden."""
    scripts, golden = _scripts_dir(tmp_path), _golden_dir(tmp_path)
    out = tmp_path / "score.json"
    rc = sc.main(["--corpora", "golden,mutants", "--golden", str(golden),
                  "--checks-d", str(scripts / "checks.d"),
                  "--history", str(tmp_path / "none.jsonl"),
                  "--out", str(out)])
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert rc == 0, payload.get("error")
    assert payload["corpora"]["mutants"]["items"] == [MUTANT]
    dummy = payload["checks"]["check_dummy"]
    assert dummy["recall"] == 1.0 and dummy["caught"] == 1
    assert dummy["by_corpus"]["golden"]["fp"] == 0
    assert list(payload["checks"])[:len(verify_all.BUILTIN_CHECKS)] == \
        [c["name"] for c in verify_all.BUILTIN_CHECKS]


def test_verify_all_runs_a_dropin_check(tmp_path):
    """verify_all.run_one runs the fragment's own script with its rendered
    args: an error on the seeded board, a pass on the clean one, and the
    optional input passed only when it exists."""
    scripts, golden = _scripts_dir(tmp_path), _golden_dir(tmp_path)
    check = next(c for c in verify_all.load_checks(scripts / "checks.d")
                 if c["name"] == "check_dummy")
    reports = tmp_path / "reports"
    reports.mkdir()
    seeded = golden / "mutants" / MUTANT
    r = verify_all.run_one(check, {
        "pcb": str(seeded / "blinky2.kicad_pcb"),
        "constraints": str(seeded / "constraints.json")}, reports)
    assert r["status"] == "violations"
    assert [v["kind"] for v in r["violations"]] == ["fragment_loaded"]
    assert r["violations"][0]["decoupling_passed"] is False
    clean = golden / "blinky2"
    r = verify_all.run_one(check, {
        "pcb": str(clean / "blinky2.kicad_pcb"),
        "constraints": str(clean / "constraints.json"),
        "decoupling": str(clean / "decoupling.json")}, reports)
    assert r["status"] == "pass" and r["violations"] == []
    r = verify_all.run_one(check, {"pcb": str(clean / "blinky2.kicad_pcb")},
                           reports)
    assert r["status"] == "skipped" and r["reason"] == "no constraints"


# ------------------------------------------------------------ validation

def test_committed_registry_loads():
    checks = verify_all.load_checks()
    assert checks[:len(verify_all.BUILTIN_CHECKS)] == verify_all.BUILTIN_CHECKS
    assert [c["name"] for c in checks] == \
        [c["name"] for c in verify_all.CHECKS]
    assert sc.load_manifest()["mutants"]


def test_fragment_args_render(tmp_path):
    scripts = _scripts_dir(tmp_path)
    check = verify_all.load_checks(scripts / "checks.d")[-1]
    assert check["script"] == scripts / "check_dummy.py"
    assert check["args"]({"constraints": "c.json"}) == \
        ["--constraints", "c.json"]
    assert check["args"]({"constraints": "c.json", "decoupling": "d.json"}) \
        == ["--constraints", "c.json", "--decoupling", "d.json"]


@pytest.mark.parametrize("text,why", [
    ("name: check_other\n", "must equal the file stem"),
    ("name: check_dummy\nneeds: [netlist]\n", "unknown inputs"),
    ("name: check_dummy\nflags: []\n", "unknown keys"),
    ("name: check_dummy\nargs: [--parts, '{parts}']\n", "placeholders"),
    ("name: check_dummy\nneeds: [parts]\n"
     "optional: {decoupling: [--x, '{parts}']}\n", "placeholders"),
    ("name: check_dummy\nneeds: [parts]\noptional: {parts: [--p, '{parts}']}\n",
     "both needed and optional"),
    ("name: check_dummy\nargs: --constraints\n", "list of strings"),
])
def test_bad_fragment_refused(tmp_path, text, why):
    scripts = _scripts_dir(tmp_path)
    (scripts / "checks.d" / "check_dummy.yaml").write_text(text,
                                                          encoding="utf-8")
    with pytest.raises(checklib.CheckError, match=why):
        verify_all.load_checks(scripts / "checks.d")


def test_fragment_needs_its_script_and_a_free_name(tmp_path):
    scripts = _scripts_dir(tmp_path)
    (scripts / "check_dummy.py").unlink()
    with pytest.raises(checklib.CheckError, match="missing"):
        verify_all.load_checks(scripts / "checks.d")
    taken = tmp_path / "taken"
    (taken / "checks.d").mkdir(parents=True)
    (taken / "check_silk.py").write_text("", encoding="utf-8")
    (taken / "checks.d" / "check_silk.yaml").write_text(
        "name: check_silk\n", encoding="utf-8")
    with pytest.raises(checklib.CheckError, match="already registered"):
        verify_all.load_checks(taken / "checks.d")


def test_manifest_fragment_merged_and_guarded(tmp_path):
    golden = _golden_dir(tmp_path)
    assert list(sc.load_manifest(golden)["mutants"]) == [MUTANT]
    frag = golden / "manifest.d" / "again.yaml"
    frag.write_text((golden / "manifest.d" / "check_dummy.yaml").read_text(
        encoding="utf-8"), encoding="utf-8")
    with pytest.raises(checklib.CheckError, match="already in the manifest"):
        sc.load_manifest(golden)
    frag.write_text("golden_boards: {x: {dir: x}}\n", encoding="utf-8")
    with pytest.raises(checklib.CheckError, match="one `mutants:` mapping"):
        sc.load_manifest(golden)
