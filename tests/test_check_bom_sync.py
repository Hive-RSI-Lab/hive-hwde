"""check_bom_sync.py: DNP, BOM, CPL and board attributes agree (DFA-07,
DFA-04, DFA-08).

Each case copies the blinky2 golden (board + schematic) into its own tmp dir,
writes the fab files and parts.json it needs, and asserts both the ref that is
flagged and the refs that are not. The committed mutant dfa-07-dnp-populated
is checked as it is in the scorecard: caught at R2 by bom_dnp_populated.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / ".claude" / "skills" / "hwde" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
import check_bom_sync  # noqa: E402

GOLDEN = REPO / "tests" / "golden"
MUTANT = GOLDEN / "mutants" / "dfa-07-dnp-populated"
R2_POS = [127.1, 129.5]


def _mutation():
    spec = importlib.util.spec_from_file_location(
        "dfa_07_dnp_populated",
        GOLDEN / "mutations" / "dfa_07_dnp_populated.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MUT = _mutation()
GOLDEN_PCB = (GOLDEN / "blinky2" / "blinky2.kicad_pcb").read_text(
    encoding="utf-8")


def _workspace(tmp_path, pcb_text=GOLDEN_PCB, sch_dnp=None, fab=True):
    """A flat copy of blinky2 in tmp_path; fab=True writes the clean fab
    files built from the golden footprints. Returns the board path."""
    pcb = tmp_path / "blinky2.kicad_pcb"
    pcb.write_text(pcb_text, encoding="utf-8")
    sch = (GOLDEN / "blinky2" / "blinky2.kicad_sch").read_text(
        encoding="utf-8")
    if sch_dnp:
        sch = MUT.mark_schematic_dnp(sch, sch_dnp)
    (tmp_path / "blinky2.kicad_sch").write_text(sch, encoding="utf-8")
    if fab:
        for name, body in MUT.fab_files(GOLDEN_PCB).items():
            (tmp_path / name).write_text(body, encoding="utf-8")
    return pcb


def _run(pcb, parts=None):
    viols, legs = check_bom_sync.check(
        pcb, check_bom_sync.find_fab_dir(pcb, None), parts)
    return viols, legs


def _errors(viols, code=None):
    return [v for v in viols if v["severity"] == "error"
            and (code is None or v["kind"] == code)]


def _refs(viols, code):
    return {r for v in viols if v["kind"] == code for r in v["refs"]}


def _drop_ref(path, ref):
    """Remove one designator from a fab csv (whole CPL row, or one name
    from a BOM row's comma list)."""
    lines = path.read_text(encoding="utf-8").splitlines()
    out = [lines[0]]
    for line in lines[1:]:
        if line.startswith(f"{ref},"):
            continue
        out.append(line.replace(f"{ref},", "").replace(f",{ref}\"", "\""))
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def test_golden_has_no_findings_and_says_which_legs_skipped():
    # a bare board is no fault: no finding, but the skipped legs are named
    viols, legs = _run(GOLDEN / "blinky2" / "blinky2.kicad_pcb")
    assert viols == []
    skipped = legs.pop("skipped")
    assert legs == {"schematic": True, "parts": False, "fab": False}
    assert [s.split(":")[0] for s in skipped] == ["fab", "parts"]


def test_clean_fab_files_pass(tmp_path):
    viols, legs = _run(_workspace(tmp_path))
    assert _errors(viols) == []
    assert legs["fab"] is True
    assert "fab" not in [s.split(":")[0] for s in legs["skipped"]]


def test_committed_mutant_is_caught_at_r2():
    viols, _ = _run(MUTANT / "blinky2.kicad_pcb")
    hits = _errors(viols, "bom_dnp_populated")
    assert {v["refs"][0] for v in hits} == {"R2"}
    assert all(v["pos"] == R2_POS for v in hits)
    # board attr and schematic agree, so nothing else is an error
    assert {v["kind"] for v in _errors(viols)} == {"bom_dnp_populated"}


def test_mutation_reproduces_committed_fab_files():
    for name, body in MUT.fab_files(GOLDEN_PCB).items():
        assert (MUTANT / name).read_text(encoding="utf-8") == body


def test_schematic_dnp_the_board_does_not_carry(tmp_path):
    # schematic says DNP, board attr and parts.json are silent: bom_cpl would
    # still place R2, and the stale BOM lists it
    viols, _ = _run(_workspace(tmp_path, sch_dnp="R2"))
    assert _refs(_errors(viols), "bom_dnp_mismatch") == {"R2"}
    assert _refs(_errors(viols), "bom_dnp_populated") == {"R2"}
    assert _refs(_errors(viols), "bom_ref_missing") == set()


def test_board_dnp_the_schematic_does_not_carry(tmp_path):
    pcb_text = MUT.mutlib.edit_footprint(
        GOLDEN_PCB, "R1", "(attr smd)", "(attr smd dnp)", "R1 dnp")
    viols, _ = _run(_workspace(tmp_path, pcb_text=pcb_text))
    assert _refs(_errors(viols), "bom_dnp_mismatch") == {"R1"}
    assert _refs(_errors(viols), "bom_dnp_populated") == {"R1"}


def test_placed_part_missing_from_cpl_and_bom_of_record(tmp_path):
    pcb = _workspace(tmp_path)
    _drop_ref(tmp_path / "CPL.csv", "C4")
    _drop_ref(tmp_path / "BOM-full.csv", "D1")
    viols, _ = _run(pcb)
    missing = _errors(viols, "bom_ref_missing")
    assert {(v["refs"][0], "CPL.csv" in v["msg"]) for v in missing} == {
        ("C4", True), ("D1", False)}


def test_unknown_designator_in_bom(tmp_path):
    pcb = _workspace(tmp_path)
    with (tmp_path / "BOM.csv").open("a", encoding="utf-8") as fh:
        fh.write("10k,R99,R_0603_1608Metric,\n")
    viols, _ = _run(pcb)
    assert _refs(_errors(viols), "bom_ref_unknown") == {"R99"}
    assert _errors(viols, "bom_ref_unknown")[0]["pos"] is None


def test_parts_json_dnp_class_counts(tmp_path):
    pcb = _workspace(tmp_path)
    parts = tmp_path / "parts.json"
    parts.write_text(json.dumps({"parts": [
        {"refdes": ["C1", "C2"], "lcsc": "C14663",
         "refdes_class": {"C2": "dnp"}}]}), encoding="utf-8")
    viols, legs = _run(pcb, parts)
    assert legs["parts"] is True
    assert _refs(_errors(viols), "bom_dnp_populated") == {"C2"}
    # a DNP class from parts.json needs no schematic flag to agree with
    assert _refs(viols, "bom_dnp_mismatch") == set()


def test_pos_excluded_smd_part_warns_until_parts_json_names_it(tmp_path):
    pcb_text = MUT.mutlib.edit_footprint(
        GOLDEN_PCB, "C4", "(attr smd)", "(attr smd exclude_from_pos_files)",
        "C4 pos excluded")
    pcb = _workspace(tmp_path, pcb_text=pcb_text)
    _drop_ref(tmp_path / "BOM.csv", "C4")
    _drop_ref(tmp_path / "CPL.csv", "C4")
    viols, _ = _run(pcb)
    assert _refs(viols, "bom_pos_excluded") == {"C4"}
    assert _errors(viols) == []          # hand_install: BOM-full only
    parts = tmp_path / "parts.json"
    parts.write_text(json.dumps({"parts": [
        {"refdes": ["C4"], "lcsc": "C15849",
         "assembly_class": "hand_install"}]}), encoding="utf-8")
    viols, _ = _run(pcb, parts)
    assert _refs(viols, "bom_pos_excluded") == set()


def test_cli_exit_codes(tmp_path):
    pcb = _workspace(tmp_path)
    out = tmp_path / "r.json"
    # clean files, parts.json absent: exit 0, and the report names the skip
    assert check_bom_sync.main(["--pcb", str(pcb), "--out", str(out)]) == 0
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert rep["legs"]["fab"] is True
    assert [s.split(":")[0] for s in rep["skipped"]] == ["parts"]
    assert check_bom_sync.main(["--pcb", str(MUTANT / "blinky2.kicad_pcb")]) \
        == 1
    assert check_bom_sync.main(["--pcb", str(tmp_path / "nope.kicad_pcb")]) \
        == 2


def test_board_feature_with_pos_excluded_is_no_part(tmp_path):
    # fiducials, printed coils and pogo pads carry exclude_from_pos_files
    # too, but exclude_from_bom makes them board_feature, not hand_install
    pcb_text = MUT.mutlib.edit_footprint(
        GOLDEN_PCB, "C4", "(attr smd)",
        "(attr smd exclude_from_pos_files exclude_from_bom)",
        "C4 board feature")
    pcb = _workspace(tmp_path, pcb_text=pcb_text)
    _drop_ref(tmp_path / "BOM.csv", "C4")
    _drop_ref(tmp_path / "CPL.csv", "C4")
    viols, _ = _run(pcb)
    assert _refs(viols, "bom_pos_excluded") == set()
    assert _errors(viols) == []
