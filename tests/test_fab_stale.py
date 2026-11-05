"""#684: fab_export refuses a board that changed after its release gates
passed, and dfm_check wants a via-fill note for filled / via-in-pad boards."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "lib"))

import fab_export  # noqa: E402
import dfm_check  # noqa: E402
import statelib  # noqa: E402

GOLD = REPO / "tests" / "golden" / "blinky2" / "blinky2.kicad_pcb"


def _workspace(tmp_path: Path, gates=("drc_routed", "verify", "dfm")) -> Path:
    ws = tmp_path / "ws"
    (ws / "kicad").mkdir(parents=True)
    pcb = ws / "kicad" / "b.kicad_pcb"
    shutil.copy(GOLD, pcb)
    dig = statelib.hash_artifact(pcb, statelib.norm_for_path(pcb))
    st = {"version": 2, "board": "b",
          "gates": {g: {"status": "pass", "last": {"inputs": {"pcb": dig}}}
                    for g in gates}}
    (ws / "state.json").write_text(json.dumps(st))
    return pcb


def test_untouched_board_not_stale(tmp_path):
    pcb = _workspace(tmp_path)
    r = fab_export.stale_gates(pcb)
    assert r["stale"] == {} and sorted(r["checked"]) == ["dfm", "drc_routed", "verify"]


def test_edited_board_is_refused(tmp_path):
    pcb = _workspace(tmp_path)
    pcb.write_text(pcb.read_text().replace("(segment", "(segment", 1)
                   .replace("(width 0.25)", "(width 0.3)", 1))
    assert pcb.read_text() != GOLD.read_text()
    with pytest.raises(fab_export.StaleBoardError, match="changed after gate"):
        fab_export.run(pcb, tmp_path / "out")


def test_dfm_check_audits_an_edited_board(tmp_path):
    """Its scratch export isn't a release: refusing it would stop the dfm gate
    from ever re-running on a board that changed since its last pass."""
    pcb = _workspace(tmp_path)
    pcb.write_text(pcb.read_text().replace("(width 0.25)", "(width 0.3)", 1))
    assert fab_export.stale_gates(pcb)["stale"]
    assert dfm_check.run(pcb)["status"]


def test_no_workspace_is_not_checked(tmp_path):
    pcb = tmp_path / "x.kicad_pcb"
    shutil.copy(GOLD, pcb)
    assert fab_export.stale_gates(pcb)["stale"] == {}


def test_via_in_pad_board_is_flagged_without_note(tmp_path):
    pcb = tmp_path / "v.kicad_pcb"
    text = GOLD.read_text()
    # plant a filled via at the first SMD pad centre
    import check_wicking as cw
    import sexpdata
    root = sexpdata.loads(text)
    d = cw.board_defaults(root)
    pad = next(p for ps in cw.read_pads(root, d).values() for p in ps
               if p["kind"] == "smd")
    x, y = pad["center"]
    via = (f'\n  (via (at {x} {y}) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu")'
           f' (filling yes) (net 0) (uuid "00000000-0000-0000-0000-000000000684"))\n')
    pcb.write_text(text.rstrip()[:-1] + via + ")\n")
    facts = fab_export.via_fill_facts(pcb)
    assert facts["filled_vias"] >= 1 and facts["in_pad"]
    fab = tmp_path / "fab"
    fab.mkdir()
    vios: list = []
    dfm_check.check_via_fill_note(pcb, fab, vios)
    assert [v["kind"] for v in vios] == ["dfm_via_fill_note_missing"]
    assert vios[0]["severity"] == "error"
    fab_export.write_via_fill_note(pcb, fab)
    vios = []
    dfm_check.check_via_fill_note(pcb, fab, vios)
    assert vios == []


def test_plain_board_needs_no_note(tmp_path):
    vios: list = []
    dfm_check.check_via_fill_note(GOLD, tmp_path, vios)
    assert vios == []
