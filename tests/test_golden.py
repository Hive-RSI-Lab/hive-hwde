"""S1 acceptance tests: golden-board corpus.

Plan S1 accept criteria:
  - golden boards pass `kicad-cli sch erc` / `pcb drc` clean
  - mutation scripts run deterministically
  - manifest complete

The mutants are manifest.yaml's plus every manifest.d/*.yaml fragment's
(score_checks.load_manifest), so a check row's mutant is tested here without
editing this file.

Everything here drives the REAL kicad-cli (10.0.3 pin via env.py); tests
are marked `smoke` where they need the live toolchain so `pytest -m "not
smoke"` still runs the pure-file checks.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
GOLDEN = REPO / "tests" / "golden"
SCRIPTS = REPO / ".claude" / "skills" / "hwde" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
import env  # noqa: E402
import score_checks  # noqa: E402
import verify_all  # noqa: E402

BOARDS = ["blinky2", "usbbuck4", "rf4"]
MANIFEST = score_checks.load_manifest(GOLDEN)
# mutant name -> its mutation script, base manifest and fragments alike
MUTATIONS = {name: Path(m["script"]).name
             for name, m in MANIFEST["mutants"].items()}

# the 13 mutants on main when the registry landed; later rows may add more,
# so the merged manifest must contain these (subset, not equality)
PINNED_MUTANTS = (
    "plane-split-under-clock", "missing-return-via", "undersized-power-trace",
    "decoupler-moved", "diffpair-skew", "silk-over-pad", "cpl-rotation",
    "hv-rail-spacing", "ldo-thermal-starved", "swdio-off-grid",
    "rail-cap-missing", "cap-undervoltage", "usb-faces-inward",
)


@pytest.fixture(scope="session")
def kicad_cli() -> Path:
    cli = env.find_kicad_cli()
    if cli is None:
        pytest.skip("kicad-cli not installed")
    return cli


@pytest.fixture(scope="session")
def manifest() -> dict:
    return MANIFEST


def run_cli(cli: Path, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([str(cli), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=300)


def erc_violations(cli: Path, sch: Path, tmp: Path) -> list[dict]:
    rep = tmp / "erc.json"
    run_cli(cli, ["sch", "erc", "--format", "json", "--severity-all",
                  "-o", str(rep), str(sch)])
    data = json.loads(rep.read_text(encoding="utf-8"))
    return [v for s in data.get("sheets", []) for v in s.get("violations", [])]


def drc_violations(cli: Path, pcb: Path, tmp: Path, parity: bool) -> list[dict]:
    rep = tmp / "drc.json"
    args = ["pcb", "drc", "--format", "json", "--severity-all"]
    if parity:
        args.append("--schematic-parity")
    run_cli(cli, [*args, "-o", str(rep), str(pcb)])
    data = json.loads(rep.read_text(encoding="utf-8"))
    return (data.get("violations", []) + data.get("unconnected_items", [])
            + data.get("schematic_parity", []))


# ------------------------------------------------------------------ goldens

@pytest.mark.smoke
@pytest.mark.parametrize("board", BOARDS)
def test_golden_erc_clean(kicad_cli, board, tmp_path):
    sch = GOLDEN / board / f"{board}.kicad_sch"
    assert sch.exists(), f"golden schematic missing: {sch}"
    violations = erc_violations(kicad_cli, sch, tmp_path)
    assert violations == [], (
        f"{board} ERC not clean: "
        f"{[(v['type'], v['severity']) for v in violations]}")


@pytest.mark.smoke
@pytest.mark.parametrize("board", BOARDS)
def test_golden_drc_clean_with_parity(kicad_cli, board, tmp_path):
    pcb = GOLDEN / board / f"{board}.kicad_pcb"
    assert pcb.exists(), f"golden board missing: {pcb}"
    violations = drc_violations(kicad_cli, pcb, tmp_path, parity=True)
    assert violations == [], (
        f"{board} DRC/parity not clean: "
        f"{[(v['type'], v['severity']) for v in violations]}")


@pytest.mark.smoke
@pytest.mark.parametrize("board", BOARDS)
def test_golden_zones_filled(board):
    """Committed goldens must carry saved zone fills (S3 relies on them)."""
    pcb = GOLDEN / board / f"{board}.kicad_pcb"
    assert "filled_polygon" in pcb.read_text(encoding="utf-8"), (
        f"{board}: no saved zone fill - regenerate via gen.py")


# ----------------------------------------------------------------- mutants

@pytest.mark.smoke
@pytest.mark.parametrize("name", list(MUTATIONS))
def test_mutation_deterministic_and_effective(name, tmp_path):
    """Two runs -> byte-identical output; output differs from the golden."""
    script = GOLDEN / "mutations" / MUTATIONS[name]
    digests = []
    boards = []
    for i in range(2):
        out = tmp_path / f"run{i}"
        cp = subprocess.run(
            [sys.executable, str(script), "--out", str(out)],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=300)
        assert cp.returncode == 0, f"{name} run {i} failed: {cp.stdout}"
        summary = json.loads(cp.stdout.strip().splitlines()[-1])
        pcb = Path(summary["out"])
        boards.append(pcb)
        digests.append(hashlib.sha256(pcb.read_bytes()).hexdigest())
    assert digests[0] == digests[1], f"{name} is not deterministic"
    golden = GOLDEN / boards[0].stem / boards[0].name
    # a sidecar-only mutant (fault in constraints/decoupling/parts) leaves the
    # copper alone: it must then have written its sidecar
    assert (boards[0].read_bytes() != golden.read_bytes()
            or summary["sidecars"]), f"{name} changed neither board nor inputs"


@pytest.mark.parametrize("name", list(MUTATIONS))
def test_mutant_committed(name, manifest):
    """The committed mutants directory matches the manifest."""
    board = manifest["mutants"][name]["board"]
    pcb = GOLDEN / "mutants" / name / f"{board}.kicad_pcb"
    assert pcb.exists(), (
        f"committed mutant missing: {pcb} (run mutations/{MUTATIONS[name]})")


# ---------------------------------------------------------------- manifest

def test_manifest_complete(manifest):
    assert set(manifest["golden_boards"]) == set(BOARDS)
    # dfm_check is scored in test_fab.py, outside the verify suite
    known_checks = {c["name"] for c in verify_all.CHECKS} | {"dfm_check"}
    for name, m in manifest["mutants"].items():
        assert m["board"] in BOARDS, f"{name}: unknown board {m['board']}"
        assert m["check"] in known_checks, f"{name}: unknown check {m['check']}"
        # failure-modes.md 3.2 rule 2: the script is named after the mutant
        assert m["script"] == f"mutations/{name.replace('-', '_')}.py", (
            f"{name}: script {m['script']} is not named after the mutant")
        script = GOLDEN / m["script"]
        assert script.exists(), f"{name}: script missing {script}"
        assert "expect" in m and m["expect"], f"{name}: no expectation"


def test_pinned_mutants_in_manifest(manifest):
    """A mutant dropped from the manifest or a fragment that stops loading."""
    missing = set(PINNED_MUTANTS) - set(manifest["mutants"])
    assert not missing, f"pinned mutants missing from manifest: {sorted(missing)}"


def test_manifest_boards_exist(manifest):
    for name, g in manifest["golden_boards"].items():
        d = GOLDEN / g["dir"]
        for ext in (".kicad_sch", ".kicad_pcb", ".kicad_pro"):
            assert (d / f"{name}{ext}").exists(), f"{name}{ext} missing"
