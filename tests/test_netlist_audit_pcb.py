"""netlist_audit --pcb: the verify gate's call (#877, #878).

The gate passes only --pcb (and constraints/decoupling when the board has
them). The audit finds the root schematic beside the board, else a .net,
else says there is nothing to judge; with no constraints file it still
judges the netlist's own faults but drops power_undeclared.
"""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import netlist_audit  # noqa: E402

GOLDEN = ROOT / "tests" / "golden"


def _board(tmp_path, sch_from=None, with_sch=True):
    """usbbuck4 copied to tmp_path, its schematic optionally swapped."""
    src = GOLDEN / "usbbuck4"
    for name in ("usbbuck4.kicad_pcb", "usbbuck4.kicad_pro"):
        shutil.copy(src / name, tmp_path / name)
    if with_sch:
        shutil.copy((sch_from or src) / "usbbuck4.kicad_sch",
                    tmp_path / "usbbuck4.kicad_sch")
    return tmp_path / "usbbuck4.kicad_pcb"


def _kinds(payload):
    return sorted((v["kind"], v.get("net"), tuple(v.get("refs") or ()))
                  for v in payload["violations"])


def test_clean_golden_board_passes(tmp_path):
    payload, _ = netlist_audit.run(["--pcb", str(_board(tmp_path))])
    assert payload["status"] == "pass"
    assert payload["violations"] == []


def test_schematic_beside_the_board_is_audited(tmp_path):
    pcb = _board(tmp_path, GOLDEN / "mutants" / "label-typo-dangling")
    payload, _ = netlist_audit.run(["--pcb", str(pcb)])
    assert ("dangling_net", "/OSC_IM", ("C7",)) in _kinds(payload)


def test_netlist_beside_the_board_when_no_schematic(tmp_path):
    sch_dir = tmp_path / "sch"
    sch_dir.mkdir()
    shutil.copy(GOLDEN / "mutants" / "sot23-gds-pinout" / "usbbuck4.kicad_sch",
                sch_dir / "usbbuck4.kicad_sch")
    shutil.copy(GOLDEN / "usbbuck4" / "usbbuck4.kicad_pro",
                sch_dir / "usbbuck4.kicad_pro")
    net = netlist_audit._export_netlist(sch_dir / "usbbuck4.kicad_sch")
    pcb = _board(tmp_path, with_sch=False)
    shutil.copy(net, pcb.with_suffix(".net"))
    payload, _ = netlist_audit.run(["--pcb", str(pcb)])
    assert any(k == "sot23_pinout" and "Q9" in refs
               for k, _n, refs in _kinds(payload))


def test_no_schematic_or_netlist_is_nothing_to_judge(tmp_path):
    pcb = _board(tmp_path, with_sch=False)
    payload, _ = netlist_audit.run(["--pcb", str(pcb)])
    assert payload["status"] == "pass"
    assert payload["violations"] == []
    assert "usbbuck4.kicad_sch" in json.dumps(payload)


def test_power_undeclared_needs_a_constraints_file(tmp_path):
    pcb = _board(tmp_path)
    empty = tmp_path / "constraints.json"
    empty.write_text("{}")
    with_c, _ = netlist_audit.run(["--pcb", str(pcb),
                                   "--constraints", str(empty)])
    without, _ = netlist_audit.run(["--pcb", str(pcb)])
    assert any(v["kind"] == "power_undeclared" for v in with_c["violations"])
    assert not any(v["kind"] == "power_undeclared"
                   for v in without["violations"])


def test_no_kicad_cli_is_nothing_to_judge_not_a_crash(tmp_path,
                                                      monkeypatch):
    """A host with no KiCad can't export the schematic: the audit says so
    (and uses a .net beside the board when there is one)."""
    monkeypatch.setattr(netlist_audit, "_have_kicad_cli", lambda: False)
    pcb = _board(tmp_path)
    payload, _ = netlist_audit.run(["--pcb", str(pcb)])
    assert payload["status"] == "pass"
    assert "no kicad-cli" in payload["nothing_to_judge"]
