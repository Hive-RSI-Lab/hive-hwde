"""U3 acceptance tests: BOM assembly classes + first-class DNP (codex H1, C9).

Plan U3 accept criteria:
  - regenerating rf-term BOM/CPL preserves R1 + instructions
        -> test_rf_term_regeneration_preserves_r1
  - regenerating rf-de BOM/CPL omits exactly the 9 DNP refs with no
    board-local filter in the loop
        -> test_rf_de_regenerates_without_a_local_filter,
           test_no_board_local_dnp_filter_survives
  - bom_cpl.json evidence no longer reports pass with bom_complete: false
        -> test_incomplete_bom_is_not_a_pass

Every test here is HERMETIC: the two real boards are read through their
committed position exports (`--pos`), so no kicad-cli is needed, and nothing is
written outside tmp_path.
"""
from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / ".claude" / "skills" / "hwde" / "scripts"
PYTHON = sys.executable
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import bom_cpl  # noqa: E402
import checklib  # noqa: E402
import dfm_check  # noqa: E402
from _boards import BOARDS, SHIPPED_CHECKS, board_path, need_board  # noqa: E402

RF_DE = board_path("rf-de-20m")
RF_TERM = board_path("rf-term-150w")

# The nine sites route-notes s17 / fab/README s2.2 name. Three of them (C203,
# C308, C309) ARE the P8 ZVS fix - fitting them halves the output power.
RF_DE_DNP = ["C203", "C205", "C206", "C308", "C309", "C318",
             "C321", "C322", "C323"]



def _lf(path):
    """CSV bytes with line endings normalized: the writer emits CRLF (csv
    default, what JLC gets) while a Linux checkout of the shipped fixture is
    LF (gitattributes text normalization) - byte identity must not depend on
    the checkout platform."""
    return path.read_bytes().replace(b"\r\n", b"\n")

def _run(board: Path, out_dir: Path, parts: Path | None = None) -> dict:
    """bom_cpl on a real board, driven from its committed pos export."""
    pos = next(board.glob("fab/*-pos.csv"))
    return bom_cpl.run(board / "kicad" / f"{board.name}.kicad_pcb", out_dir,
                       pos=pos, parts_json=parts)


def _designators(path: Path) -> list[str]:
    out: list[str] = []
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            out += [d.strip() for d in (row.get("Designator") or "").split(",")
                    if d.strip()]
    return out


def _rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# ================================================== pure: class resolution

def test_classes_default_to_smt_placed():
    cls, notes, src = bom_cpl.resolve_assembly({"R1", "C1"}, [], {})
    assert cls == {"R1": "smt_placed", "C1": "smt_placed"}
    assert src["R1"] == "default"
    assert notes["R1"] == ""          # a placed part needs no instruction


def test_line_class_applies_to_every_refdes_on_the_line(tmp_path):
    p = tmp_path / "parts.json"
    p.write_text(json.dumps({"parts": [
        {"refdes": ["R1", "R2"], "assembly_class": "off_board",
         "assembly_notes": "bolts to the sink"}]}), encoding="utf-8")
    recs = bom_cpl.load_parts_records(p)
    cls, notes, src = bom_cpl.resolve_assembly({"R1", "R2", "C1"}, recs, {})
    assert cls == {"R1": "off_board", "R2": "off_board", "C1": "smt_placed"}
    assert notes["R2"] == "bolts to the sink"
    assert src["R1"] == "parts_line"


def test_per_ref_class_outranks_line_and_board(tmp_path):
    """Priority: refdes_class > assembly_class > the board's own attribute."""
    p = tmp_path / "parts.json"
    p.write_text(json.dumps({"parts": [
        {"refdes": ["C1", "C2"], "assembly_class": "hand_install",
         "refdes_class": {"C2": "dnp"},
         "refdes_notes": {"C2": "trim site"}}]}), encoding="utf-8")
    recs = bom_cpl.load_parts_records(p)
    board = {"C1": {"attr_class": "dnp"}, "C2": {"attr_class": None}}
    cls, notes, src = bom_cpl.resolve_assembly({"C1", "C2"}, recs, board)
    assert cls["C1"] == "hand_install" and src["C1"] == "parts_line"
    assert cls["C2"] == "dnp" and src["C2"] == "parts_ref"
    assert notes["C2"] == "trim site"


def test_legacy_refdes_dnp_is_the_dnp_class(tmp_path):
    p = tmp_path / "parts.json"
    p.write_text(json.dumps({"parts": [
        {"refdes": ["C1", "C2"], "refdes_dnp": ["C2"]}]}), encoding="utf-8")
    cls, notes, _ = bom_cpl.resolve_assembly(
        {"C1", "C2"}, bom_cpl.load_parts_records(p), {})
    assert cls == {"C1": "smt_placed", "C2": "dnp"}
    assert notes["C2"] == bom_cpl.CLASS_INSTRUCTION["dnp"]


def test_unknown_class_is_rejected(tmp_path):
    p = tmp_path / "parts.json"
    p.write_text(json.dumps({"parts": [
        {"refdes": ["R1"], "assembly_class": "maybe"}]}), encoding="utf-8")
    with pytest.raises(bom_cpl.AssemblyDataError, match="unknown assembly_class"):
        bom_cpl.load_parts_records(p)


def test_board_attributes_class_parts_the_pos_export_never_shows(tmp_path):
    """KiCad's own footprint flags are a class statement: board_only /
    exclude_from_bom = a board feature, exclude_from_pos_files alone = a part
    that is in the BOM but hand-fitted."""
    pcb = tmp_path / "b.kicad_pcb"
    pcb.write_text(
        '(kicad_pcb (version 20241229)\n'
        '  (footprint "x" (attr smd) (property "Reference" "R1")'
        ' (property "LCSC" "C1"))\n'
        '  (footprint "h" (attr through_hole board_only exclude_from_bom'
        ' exclude_from_pos_files) (property "Reference" "H1"))\n'
        '  (footprint "j" (attr through_hole exclude_from_pos_files)'
        ' (property "Reference" "J1") (property "LCSC" "C2"))\n'
        '  (footprint "d" (attr smd dnp) (property "Reference" "C9")'
        ' (property "LCSC" "C3"))\n)', encoding="utf-8")
    fields = bom_cpl.board_part_fields(pcb)
    assert fields["R1"]["attr_class"] is None
    assert fields["H1"]["attr_class"] == "board_feature"
    assert fields["J1"]["attr_class"] == "hand_install"
    assert fields["C9"]["attr_class"] == "dnp"
    # board_lcsc_map keeps its S12 contract (refs that carry an LCSC field)
    assert set(bom_cpl.board_lcsc_map(pcb)) == {"R1", "J1", "C9"}


# ============================================== pure: membership + outputs

POS = (
    "Ref,Val,Package,PosX,PosY,Rot,Side\n"
    "C1,100nF,C0402,1.0,-1.0,0,top\n"
    "C2,100nF,C0402,2.0,-1.0,0,top\n"
    "R1,50R,R2010,3.0,-1.0,0,top\n"
)


def _synth(tmp_path, parts: dict) -> tuple[dict, Path]:
    out = tmp_path / "fab"
    pcb = tmp_path / "s.kicad_pcb"
    pcb.write_text("(kicad_pcb)", encoding="utf-8")
    pos = tmp_path / "s-pos.csv"
    pos.write_text(POS, encoding="utf-8")
    pj = tmp_path / "parts.json"
    pj.write_text(json.dumps(parts), encoding="utf-8")
    return bom_cpl.run(pcb, out, pos=pos, parts_json=pj), out


def test_bom_full_lists_every_class_and_cpl_places_only_smt(tmp_path):
    rep, out = _synth(tmp_path, {"parts": [
        {"refdes": ["C1", "C2"], "lcsc": "C111", "value": "100nF",
         "refdes_class": {"C2": "dnp"}},
        {"refdes": ["R1"], "lcsc": "C222", "value": "50R",
         "assembly_class": "off_board", "assembly_notes": "BeO - do not drill"},
        {"qty_per_board": 4, "assembly_class": "customer_supplied",
         "value": "M3 screw"},
    ]})
    assert rep["status"] == "pass"
    assert rep["class_counts"] == {"smt_placed": 1, "dnp": 1, "off_board": 1}

    # CPL and the upload BOM agree exactly, and hold the placed part only
    assert _designators(out / "CPL.csv") == ["C1"]
    assert _designators(out / "BOM.csv") == ["C1"]
    # JLC's upload template header, with the part number under "LCSC Part #"
    assert (out / "BOM.csv").read_text(encoding="utf-8").splitlines()[0] == \
        "Comment,Designator,Footprint,LCSC Part #"
    assert _rows(out / "BOM.csv")[0]["LCSC Part #"] == "C111"

    full = {r["Designator"]: r for r in _rows(out / "BOM-full.csv")}
    assert set(full) == {"C1", "C2", "R1", ""}
    assert full["C2"]["Assembly Class"] == "dnp"
    assert "DO NOT POPULATE" in full["C2"]["Instructions"]
    assert full["R1"]["Assembly Class"] == "off_board"
    assert full["R1"]["Instructions"] == "BeO - do not drill"
    # a refdes-less line only reaches the BOM when it declares a class
    assert full[""]["Assembly Class"] == "customer_supplied"
    assert full[""]["Qty Per Board"] == "4"


def test_upload_bom_has_one_row_per_lcsc_part(tmp_path):
    """Two rows with one LCSC number split that part's stock in JLC's BOM
    review; parts sharing an LCSC number share a row whatever their value."""
    rep, out = _synth(tmp_path, {"parts": [
        {"refdes": ["C1", "R1"], "lcsc": "C111", "value": "100nF"},
        {"refdes": ["C2"], "lcsc": "C333", "value": "100nF"},
    ]})
    assert rep["status"] == "pass"
    assert [(r["Designator"], r["LCSC Part #"])
            for r in _rows(out / "BOM.csv")] == [("C1,R1", "C111"),
                                                 ("C2", "C333")]


def test_prebuy_lists_each_extended_placed_part_for_the_build(tmp_path):
    """Owner, 2026-09-24: JLC's BOM review left an Extended part (idle stock)
    unselected at qty 0 until it was bought in. Every Extended part the
    assembler places is on the pre-buy list with LCSC number and build qty;
    Basic parts and unplaced Extended sites are not."""
    pos = tmp_path / "s-pos.csv"
    pos.write_text(POS, encoding="utf-8")
    pcb = tmp_path / "s.kicad_pcb"
    pcb.write_text("(kicad_pcb)", encoding="utf-8")
    pj = tmp_path / "parts.json"
    pj.write_text(json.dumps({"parts": [
        {"refdes": ["C1", "C2"], "lcsc": "C111", "value": "100nF",
         "mpn": "X1", "basic": False},
        {"refdes": ["R1"], "lcsc": "C222", "value": "50R", "type": "basic"},
    ]}), encoding="utf-8")
    out = tmp_path / "fab"
    rep = bom_cpl.run(pcb, out, pos=pos, parts_json=pj, build_qty=10)
    rows = _rows(out / "prebuy.csv")
    assert [(r["LCSC"], r["MPN"], r["Designator"], r["Qty Per Board"],
             r["Boards"], r["Qty To Buy"]) for r in rows] == \
        [("C111", "X1", "C1,C2", "2", "10", "20")]
    assert "idle stock" in rows[0]["Note"]
    assert rep["prebuy_rows"] == rows and rep["build_qty"] == 10

    # the same Extended part on a DNP site only: nothing to pre-buy
    pj.write_text(json.dumps({"parts": [
        {"refdes": ["C1", "C2"], "lcsc": "C111", "value": "100nF",
         "basic": False, "assembly_class": "dnp"},
        {"refdes": ["R1"], "lcsc": "C222", "value": "50R", "basic": True},
    ]}), encoding="utf-8")
    rep = bom_cpl.run(pcb, tmp_path / "fab2", pos=pos, parts_json=pj)
    assert rep["prebuy_rows"] == [] and rep["build_qty"] == 5
    assert (tmp_path / "fab2" / "prebuy.csv").read_text(
        encoding="utf-8").splitlines() == [",".join(bom_cpl.PREBUY_FIELDS)]


def test_incomplete_bom_is_not_a_pass(tmp_path):
    """codex H1: reports/bom_cpl.json used to say status pass beside
    bom_complete false. A part nobody can buy is a violation, exit 1."""
    rep, _ = _synth(tmp_path, {"parts": [
        {"refdes": ["C1", "C2", "R1"], "value": "x"}]})
    assert rep["bom_complete"] is False
    assert rep["status"] == "violations"
    assert [v["kind"] for v in rep["violations"]] == ["bom_unsourced"]

    pcb, pos = tmp_path / "s.kicad_pcb", tmp_path / "s-pos.csv"
    pj = tmp_path / "parts.json"
    code = subprocess.run(
        [PYTHON, str(SCRIPTS / "bom_cpl.py"), "--pcb", str(pcb),
         "--out-dir", str(tmp_path / "cli"), "--pos", str(pos),
         "--parts", str(pj), "--out", str(tmp_path / "r.json")]).returncode
    assert code == 1                       # SPEC 6: 1 = violations, 2 = error


def test_a_distributor_line_is_a_complete_bom_entry_off_lcsc(tmp_path):
    """A hand-built board legitimately buys from DigiKey: sourced but not
    JLC-sourceable. That is a warning class, not an incomplete BOM."""
    rep, _ = _synth(tmp_path, {"parts": [
        {"refdes": ["C1", "C2", "R1"], "value": "x", "mpn": "5602",
         "distributor": "DigiKey", "distributor_pn": "1956-1000-ND"}]})
    assert rep["status"] == "pass" and rep["bom_complete"] is True
    assert rep["off_lcsc"] == ["C1", "C2", "R1"] and rep["unsourced"] == []


def test_declared_populate_quantity_must_match_the_classes(tmp_path):
    rep, _ = _synth(tmp_path, {"parts": [
        {"refdes": ["C1", "C2"], "lcsc": "C111", "value": "100nF",
         "refdes_dnp": ["C2"], "qty_per_board": 2,
         "qty_per_board_populated": 2}]})
    assert rep["status"] == "violations"
    kinds = [v["kind"] for v in rep["violations"]]
    assert "assembly_qty_mismatch" in kinds
    assert rep["qty_mismatch"][0]["declared_populated"] == 2
    assert rep["qty_mismatch"][0]["derived_populated"] == 1


def test_smt_placed_without_a_placement_is_a_violation(tmp_path):
    rep, _ = _synth(tmp_path, {"parts": [
        {"refdes": ["C1", "C2", "R1", "L9"], "lcsc": "C111", "value": "x"}]})
    assert rep["unplaced_smt"] == ["L9"]
    assert "assembly_unplaced_smt" in [v["kind"] for v in rep["violations"]]


# ================================================ known answers: rf-de-20m

# Shipped rotation taken from the part's own LCSC model, not the table.
RF_DE_MODEL_ROTATED = {"U101"}


def test_rf_de_regenerates_without_a_local_filter(tmp_path):
    """The C9 known answer. A plain bom_cpl run on the shipped inputs must
    reproduce the shipped package - 59 placements, the nine DNP sites absent -
    with nothing between it and the files."""
    need_board("rf-de-20m")
    rep = _run(RF_DE, tmp_path, RF_DE / "parts" / "parts.json")
    assert rep["status"] == "pass"
    assert rep["n_parts"] == 68 and rep["n_placed"] == 59
    assert sorted(e["ref"] for e in rep["not_placed"]) == sorted(RF_DE_DNP)

    assert _lf(tmp_path / "BOM.csv") == _lf(RF_DE / "fab" / "BOM.csv"), \
        "BOM.csv is not reproducible"
    # Tests never read the LCSC model cache, so a part whose shipped rotation
    # came from its own model (boards #26 regenerated U101 that way) falls
    # back to the package table here. Only those pinned parts may differ, and
    # only in Rotation; everything else must match the shipped CPL exactly.
    offline = RF_DE_MODEL_ROTATED & {a["ref"] for a in rep["rotation_audit"]
                                     if a["source"] != "lcsc_model"}
    got, shipped = _rows(tmp_path / "CPL.csv"), _rows(RF_DE / "fab" / "CPL.csv")
    assert [r["Designator"] for r in got] == \
        [r["Designator"] for r in shipped], "CPL.csv is not reproducible"
    for g, s in zip(got, shipped):
        if g["Designator"] in offline:
            g, s = dict(g, Rotation=None), dict(s, Rotation=None)
        assert g == s, f"CPL.csv is not reproducible at {s['Designator']}"

    placed = _designators(tmp_path / "CPL.csv")
    assert len(placed) == 59
    for ref in RF_DE_DNP:
        assert ref not in placed
        assert ref not in _designators(tmp_path / "BOM.csv")

    # ... and they are PRESENT in the BOM of record, marked and explained
    full = _rows(tmp_path / "BOM-full.csv")
    dnp = {d: r for r in full if r["Assembly Class"] == "dnp"
           for d in r["Designator"].split(",")}
    assert sorted(dnp) == sorted(RF_DE_DNP)
    for ref in RF_DE_DNP:
        assert "DO NOT POPULATE" in dnp[ref]["Instructions"]
    for ref in ("C203", "C308", "C309"):
        assert "ZVS" in dnp[ref]["Instructions"], f"{ref} lost its ZVS warning"


def test_no_board_local_dnp_filter_survives():
    """The removal is the acceptance: a board-local mutating post-step is the
    thing U3 replaces, and it must not creep back into any workspace."""
    need_board("rf-de-20m")
    assert not (RF_DE / "fab" / "filter_dnp.py").exists()
    if SHIPPED_CHECKS:
        assert not list(BOARDS.glob("*/fab/filter_dnp.py"))


def test_dfm_fails_a_package_that_ships_a_dnp_site(tmp_path):
    """codex C9's required action: fail release if the shipped population
    differs from the declared variant."""
    fab = tmp_path / "fab"
    fab.mkdir()
    (fab / "BOM.csv").write_text(
        "Comment,Designator,Footprint,LCSC\n"
        '56pF,"C301,C203",C1206,C113875\n', encoding="utf-8", newline="")
    (fab / "CPL.csv").write_text(
        "Designator,Mid X,Mid Y,Layer,Rotation\nC301,1,1,Top,0\n",
        encoding="utf-8", newline="")
    report = {"not_placed": [{"ref": "C203", "class": "dnp",
                              "source": "parts_ref"}]}
    vios: list = []
    dfm_check.check_release(_NoFab(), [], vios, report, fab_dir=fab)
    leak = [v for v in vios if v["kind"] == "dfm_unplaced_in_package"]
    assert len(leak) == 1 and leak[0]["severity"] == "error"
    assert leak[0]["refs"] == ["C203"] and leak[0]["file"] == "BOM.csv"


class _NoFab:
    """Just enough of gerblib.FabStack for check_release's layer leg (which
    fires its own missing-layer error - we only assert on the assembly one)."""
    copper_files: dict = {}
    mask_files: dict = {}
    silk_files: dict = {}
    drill_files: list = []
    edge_file = None
    holes: list = []


def test_dfm_severity_splits_unsourced_from_off_lcsc():
    """U3: a machine-placed part nobody can buy fails the gate; one sourced off
    LCSC is reported without failing it (the pre-U3 code warned on both)."""
    vios: list = []
    dfm_check.check_release(_NoFab(), [], vios,
                            {"missing_lcsc": ["U1", "C3"],
                             "unsourced": ["U1"], "off_lcsc": ["C3"]})
    by_kind = {v["kind"]: v for v in vios}
    assert by_kind["dfm_bom_incomplete"]["severity"] == "error"
    assert by_kind["dfm_bom_incomplete"]["refs"] == ["U1"]
    assert by_kind["dfm_bom_off_lcsc"]["severity"] == "warning"
    assert by_kind["dfm_bom_off_lcsc"]["refs"] == ["C3"]


# ============================================== known answers: rf-term-150w

def test_rf_term_regeneration_preserves_r1(tmp_path):
    """codex H1's own example. R1 is a BeO-flanged 250 W load that bolts to the
    user's heatsink; it was in the hand-authored BOM and nowhere in the
    generator's model, so a regeneration silently deleted it."""
    need_board("rf-term-150w")
    rep = _run(RF_TERM, tmp_path, RF_TERM / "parts" / "parts.json")
    assert rep["status"] == "pass"
    assert rep["assembly_classes"]["R1"] == "off_board"

    # not machine-placed: absent from CPL and from the assembler upload, and
    # the CPL is unchanged from the delivered package
    assert "R1" not in _designators(tmp_path / "CPL.csv")
    assert "R1" not in _designators(tmp_path / "BOM.csv")
    assert _lf(tmp_path / "CPL.csv") == _lf(RF_TERM / "fab" / "CPL.csv")

    rows = _rows(tmp_path / "BOM-full.csv")
    full = {r["Designator"]: r for r in rows}
    r1 = full["R1"]
    assert r1["Assembly Class"] == "off_board"
    assert r1["MPN"] == "T50R0-250-12X" and r1["Comment"] == "50R 250W"
    # the instructions the hand-authored fab/BOM.csv carried, verbatim facts
    for phrase in ("SELECT-ON-TEST", "49.00-51.00 ohm", "BeO substrate",
                   "do not machine, drill or break", "OFF-BOARD"):
        assert phrase in r1["Instructions"], f"lost {phrase!r}"

    # the user-supplied hardware survives regeneration too
    supplied = [r for r in rows if r["Assembly Class"] == "customer_supplied"]
    assert len(supplied) == 5
    assert any("ONLY RF RETURN PATH" in r["Instructions"] for r in supplied)


def test_rf_term_hand_authored_bom_semantics_are_covered(tmp_path):
    """Every part row of the delivered hand-authored BOM.csv reappears in the
    regenerated BOM of record (byte-diff of the semantics, per the plan)."""
    need_board("rf-term-150w")
    rep = _run(RF_TERM, tmp_path, RF_TERM / "parts" / "parts.json")
    assert rep["n_placed"] == 2
    hand = [r for r in _rows(RF_TERM / "fab" / "BOM.csv") if r["Designator"]]
    full = {r["Designator"]: r for r in _rows(tmp_path / "BOM-full.csv")}
    assert {r["Designator"] for r in hand} <= set(full)
    for r in hand:
        got = full[r["Designator"]]
        assert got["MPN"] == r["MPN"]
        assert got["Comment"] == r["Value"]
