"""T6 (P7B-1) tests: route_critical --pad-window - the deterministic
widest-connectable-track probe (LEARNINGS 782/795, ladder row 78).

Known-answer fixture: the frozen pd-trigger PLACE board, where the USB-C's
two VBUS pads measure ~1.49 mm against a 1.75 mm DRU floor (unmeetable ->
exit 1) while every other VBUS pad clears 4+ mm. NOTE the pinned value:
the continuous bisection solver measures 1.489; LEARNINGS 782's hand
measurement (0.025 mm grid search) reported 1.465 - the grid under-reports
by missing the off-lattice optimum. Both agree the 1.75 floor cannot be
met, which is the check's contract.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / ".claude" / "skills" / "hwde" / "scripts"
FIX = REPO / "tests" / "fixtures" / "stages" / "pd_trigger"

sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
import geom  # noqa: E402
import route_critical as rc  # noqa: E402
from checklib import CheckError  # noqa: E402


# ---- synthetic fixtures -----------------------------------------------------

def _pcb(tmp_path, name: str, body: str, w: float = 20.0,
         h: float = 20.0) -> Path:
    text = f"""(kicad_pcb
  (version 20260206) (generator "test")
  (general (thickness 1.6))
  (layers (0 "F.Cu" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user))
  (setup)
  (gr_rect (start 0 0) (end {w} {h}) (stroke (width 0.1)) (fill no)
    (layer "Edge.Cuts"))
{body})
"""
    p = tmp_path / f"{name}.kicad_pcb"
    p.write_text(text, encoding="utf-8")
    return p


def _fp(ref, x, y, pads):
    return (f'  (footprint "t:{ref}" (layer "F.Cu")\n    (at {x} {y})\n'
            f'    (property "Reference" "{ref}" (at 0 0 0))\n{pads})\n')


def _pad(num, x, y, net, w=1.0, h=1.0):
    n = f' (net "{net}")' if net else ""
    return (f'    (pad "{num}" smd rect (at {x} {y}) (size {w} {h})'
            f' (layers "F.Cu"){n})\n')


def _corridor_board(tmp_path) -> Path:
    """VBUS pad 1x1 at board centre inside a 2 mm foreign-GND corridor:
    widest = 2 * (1.0 - CLR 0.2) = 1.6 mm."""
    body = _fp("U1", 10, 10, _pad("1", 0, 0, "VBUS"))
    body += _fp("B1", 4.5, 10, _pad("1", 0, 0, "GND", w=9, h=18))
    body += _fp("B2", 15.5, 10, _pad("1", 0, 0, "GND", w=9, h=18))
    return _pcb(tmp_path, "corridor", body)


# ============================================================ pure: solver

def test_corridor_widest_is_gap_minus_clearance(tmp_path):
    bg = geom.BoardGeom.from_file(_corridor_board(tmp_path))
    pad = bg.pads_of(net="VBUS")[0]
    foreign = rc._pad_window_foreign(bg, "VBUS", "F.Cu")
    w = rc.widest_connectable_mm(pad, foreign, bg.outline,
                                 clearance=0.2, edge_clearance=0.3)
    assert w == pytest.approx(1.6, abs=0.01)


def test_open_board_hits_the_cap(tmp_path):
    body = _fp("U1", 10, 10, _pad("1", 0, 0, "VBUS"))
    bg = geom.BoardGeom.from_file(_pcb(tmp_path, "open", body))
    pad = bg.pads_of(net="VBUS")[0]
    w = rc.widest_connectable_mm(pad, None, bg.outline, 0.2, 0.3)
    assert w == rc.PAD_WINDOW_CAP


def test_unnetted_pads_count_as_foreign(tmp_path):
    """LEARNINGS 1538: bg.nets omits no-net pads; the probe must not."""
    body = _fp("U1", 10, 10, _pad("1", 0, 0, "VBUS"))
    body += _fp("X1", 8.4, 10, _pad("1", 0, 0, None, w=2, h=18))
    body += _fp("X2", 11.6, 10, _pad("1", 0, 0, None, w=2, h=18))
    bg = geom.BoardGeom.from_file(_pcb(tmp_path, "unnet", body))
    pad = bg.pads_of(net="VBUS")[0]
    foreign = rc._pad_window_foreign(bg, "VBUS", "F.Cu")
    w = rc.widest_connectable_mm(pad, foreign, bg.outline, 0.2, 0.3)
    # corridor between the no-net pads: inner edges 9.4 / 10.6 -> gap
    # half-width 0.6 -> widest 2*(0.6 - 0.2) = 0.8; if unnetted pads were
    # invisible this would be the 8.0 cap
    assert w == pytest.approx(0.8, abs=0.01)


# ============================================================ CLI contract

def test_pad_window_cli_flags_rule_floor(tmp_path):
    pcb = _corridor_board(tmp_path)
    pcb.with_suffix(".kicad_dru").write_text("""(version 1)
(rule "aiee_pwr_width_VBUS"
\t(constraint track_width (min 1.7500mm))
\t(condition "A.NetName == 'VBUS' && A.Type == 'track'")
)
""", encoding="utf-8")
    raw = pcb.read_bytes()
    rep = tmp_path / "pw.json"
    code = rc.main(["--pcb", str(pcb), "--pad-window", "--nets", "VBUS",
                    "--out-report", str(rep)])
    assert code == 1                      # 1.6 < 1.75 -> unmeetable
    assert pcb.read_bytes() == raw        # probe never writes
    r = json.loads(rep.read_text(encoding="utf-8"))
    assert r["status"] == "violations"
    assert r["facts"]["probe"] == "pad_window"
    row = r["facts"]["pad_window"][0]
    assert row["net"] == "VBUS" and row["ok"] is False
    assert row["rule_min_mm"] == 1.75
    assert row["widest_mm"] == pytest.approx(1.6, abs=0.01)
    v = r["violations"][0]
    assert v["kind"] == "pad_window_unmeetable" and v["refs"] == ["U1"]
    # without a DRU floor the same geometry is informational -> exit 0
    pcb.with_suffix(".kicad_dru").unlink()
    assert rc.main(["--pcb", str(pcb), "--pad-window",
                    "--nets", "VBUS"]) == 0


def test_pad_window_needs_nets(tmp_path):
    pcb = _corridor_board(tmp_path)
    with pytest.raises(CheckError, match="no target nets"):
        rc.pad_window_report(pcb, {}, None)
    payload = rc.pad_window_report(pcb, {}, "VBUS,NOPE")
    assert payload["facts"]["missing_nets"] == ["NOPE"]


# ============================================================ known answer

def test_known_answer_pd_trigger_vbus():
    """The pd-trigger premise falsification as one deterministic call:
    J1's two VBUS pads cap at ~1.49 mm < the 1.75 mm DRU floor; every
    other VBUS pad clears 4+ mm (LEARNINGS 782/783-787)."""
    pcb = FIX / "place" / "pd-trigger.kicad_pcb"
    constraints = json.loads((FIX / "constraints.json")
                             .read_text(encoding="utf-8"))
    payload = rc.pad_window_report(pcb, constraints, "VBUS")
    assert payload["status"] == "violations"
    assert payload["facts"]["clearance_mm"] == pytest.approx(0.1524)
    rows = {(r["ref"], r["pad"]): r for r in payload["facts"]["pad_window"]}
    j1 = [r for (ref, _), r in rows.items() if ref == "J1"]
    assert len(j1) == 2
    for r in j1:
        assert r["widest_mm"] == pytest.approx(1.489, abs=0.01)
        assert r["rule_min_mm"] == 1.75 and r["ok"] is False
    for (ref, _), r in rows.items():
        if ref != "J1":
            assert r["widest_mm"] >= 4.0 and r["ok"] is True, r
    kinds = [v["kind"] for v in payload["violations"]]
    assert kinds == ["pad_window_unmeetable"] * 2
