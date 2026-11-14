"""#911: every edge connector's mating face points off the board edge.

The laptop hub shipped its microSD socket (J6, Molex 104031-0811 as EasyEDA's
SIM-SMD_8P-1040310811) facing into the board: no family row knew the name,
so check_mating judged nothing. The card_slot family now knows 104031, a
`mouth` override puts its mouth at the signal tails (the inner-lead layout
the pad rule would read backwards), and a J/P/CN part within EDGE_NEAR_MM of
the edge that no family knows is an error at release (mating_edge_unjudged),
waivable per connector.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "lib"))
sys.path.insert(0, str(REPO / "tests"))
import check_mating  # noqa: E402
import gate  # noqa: E402
import matinglib  # noqa: E402
import verify_all  # noqa: E402
from test_mating import _board, _fp, _pad, small  # noqa: E402

# J6's layout: 8 signal tails along local +y, the mouth end (#911)
SD_PADS = "".join(_pad(str(i + 1), -3.3 + 1.1 * i, 5.0, 0.7, 1.4)
                  for i in range(8)) + \
    _pad("S1", -6.0, -4.0, 1.2, 1.6) + _pad("S2", 6.0, -4.0, 1.2, 1.6)
SD_CY = (-7.0, -6.0, 7.0, 6.5)
GATE = {"strict": False, "fail_severities": ["error"], "max_count": 0}


def _sd(tmp_path, angle, name="SIM-SMD_8P-1040310811"):
    # at angle 0 local +y is board +y: the tails face the bottom edge (y=20)
    # turned 180 in the same spot, the mouth points into the board
    return _board(tmp_path, _fp("J6", name, 15.0, 13.0, angle, pads=SD_PADS,
                                cy=SD_CY) + small("R1", 3.0, 3.0))


def _mating(pcb):
    return check_mating.run(["--pcb", str(pcb)])[0]


def _kinds(r):
    return sorted(v["kind"] for v in r["violations"])


def test_family_rows_know_the_laptop_and_pinned_parts():
    fam = lambda n: (matinglib.family_of("x:" + n) or {}).get("family")
    assert fam("SIM-SMD_8P-1040310811") == "card_slot"
    assert fam("Molex_104031-0811") == "card_slot"
    assert fam("USB_C_Receptacle_GCT_USB4105") == "usb_c"
    assert fam("USB_A_Molex_87520-0010BLF") == "usb_a"
    # digits inside a longer number do not match
    assert fam("R_1104031_x") is None


def test_sd_socket_facing_the_edge_passes(tmp_path):
    r = _mating(_sd(tmp_path, 0))
    assert r["status"] == "pass", r["violations"]
    assert r["edge_unjudged"] == []


def test_planted_backwards_sd_socket_fails(tmp_path):
    """The planted fault: the laptop's J6 turned round, mouth into the board."""
    r = _mating(_sd(tmp_path, 180))
    assert "mating_faces_inward" in _kinds(r)
    v = next(v for v in r["violations"] if v["kind"] == "mating_faces_inward")
    assert v["refs"] == ["J6"] and v["severity"] == "error"


def test_mouth_at_pads_is_not_called_reversed():
    """model_span_mm assumes the pads sit at the back; for a mouth override
    that points at the pads it says nothing, so model_reversed cannot fire."""
    class F:
        fpid = "x:SIM-SMD_8P-1040310811"
    import types
    # contact centroid local (0,5), body centroid (0,0.25), mouth +y
    f = types.SimpleNamespace(
        fpid=F.fpid,
        pads=[types.SimpleNamespace(number=str(i), size=(0.7, 1.4),
                                    through=False, local=(i - 3.5, 5.0))
              for i in range(8)],
        extents_local=lambda: types.SimpleNamespace(
            centroid=types.SimpleNamespace(x=0.0, y=0.25)))
    orig = matinglib.model_corners_local
    matinglib.model_corners_local = lambda *a: [(-7, -6), (7, 6.5)]
    try:
        assert matinglib.model_span_mm(f, "+y", {}, Path(".")) is None
        # pads at the back, as the rule assumes: measured as before
        f.pads = [p.__class__(**{**vars(p), "local": (p.local[0], -5.0)})
                  for p in f.pads]
        assert matinglib.model_span_mm(f, "+y", {}, Path(".")) == (-1, 11.5)
    finally:
        matinglib.model_corners_local = orig


def _unlisted(tmp_path):
    return _sd(tmp_path, 0, name="SOCKET-SMD_8P-UNLISTED")


def test_unknown_edge_connector_is_listed(tmp_path):
    r = _mating(_unlisted(tmp_path))
    assert [e["ref"] for e in r["edge_unjudged"]] == ["J6"]
    # well inside the board it is not an edge connector
    r = _mating(_board(tmp_path, _fp("J6", "SOCKET-SMD_8P-UNLISTED", 15.0,
                                     10.0, cy=(-2, -2, 2, 2))))
    assert r["edge_unjudged"] == []


def _merged(pcb, tmp_path, strict):
    rd = tmp_path / ("s" if strict else "l")
    rd.mkdir()
    chk = next(c for c in verify_all.CHECKS if c["name"] == "check_mating")
    res = [verify_all.run_one(chk, {"pcb": str(pcb)}, rd, {}, strict)]
    return verify_all.merge(pcb, res, {}, strict)


def test_unknown_edge_connector_fails_release_and_a_waiver_clears_it(
        tmp_path):
    pcb = _unlisted(tmp_path)
    m = _merged(pcb, tmp_path, strict=True)
    errs = [v for v in m["violations"] if v["kind"] == "mating_edge_unjudged"]
    assert len(errs) == 1 and errs[0]["refs"] == ["J6"]
    assert errs[0]["check"] == "mating" and errs[0]["severity"] == "error"
    assert gate.evaluate("verify", GATE, m)["status"] == "fail"
    w = {"check": "mating", "kind": "mating_edge_unjudged", "net": None,
         "refs": ["J6"], "reason": "mouth checked by hand",
         "approved": "test 2026-11-13"}
    m2 = {**m, "violations": [v for v in m["violations"]
                              if v["kind"] != "connector_check_unjudged"]}
    assert gate.evaluate("verify", GATE, m2, [w])["status"] != "fail"
    # mid-pipeline (not strict) it raises nothing
    m = _merged(pcb, tmp_path, strict=False)
    assert not [v for v in m["violations"]
                if v["kind"] == "mating_edge_unjudged"]


# #954: the same list, as a place-gate warning, while turning it is cheap
import place_metrics  # noqa: E402


def _place(pcb):
    return place_metrics.run(["--pcb", str(pcb)])[0]


def _edge_warnings(r):
    return [v for v in r["violations"] if v["kind"] == "mating_edge_unjudged"]


def test_planted_unknown_edge_connector_is_flagged_at_placement(tmp_path):
    """The planted fault: the laptop's J6 under a name no family knows, at
    the edge - flagged by the place gate, before routing, as a warning."""
    r = _place(_unlisted(tmp_path))
    w = _edge_warnings(r)
    assert len(w) == 1, r["violations"]
    assert w[0]["refs"] == ["J6"] and w[0]["severity"] == "warning"
    assert w[0]["check"] == "place"
    assert w[0]["footprint"] == "SOCKET-SMD_8P-UNLISTED"
    assert w[0]["edge_gap_mm"] <= matinglib.EDGE_NEAR_MM
    # the same part check_mating lists for the release gate
    assert [e["ref"] for e in _mating(_unlisted(tmp_path))["edge_unjudged"]] \
        == ["J6"]
    # a warning: placement is not held up on it
    assert not [v for v in r["violations"] if v["severity"] == "error"
                and v["kind"] == "mating_edge_unjudged"]
    assert "mating_edge_unjudged" in place_metrics.PLACE_FAMILIES["mating"]


def test_known_or_inner_connector_gets_no_placement_warning(tmp_path):
    assert _edge_warnings(_place(_sd(tmp_path, 0))) == []
    pcb = _board(tmp_path, _fp("J6", "SOCKET-SMD_8P-UNLISTED", 15.0, 10.0,
                               cy=(-2, -2, 2, 2)))
    assert _edge_warnings(_place(pcb)) == []


def test_the_note_is_not_counted_as_illegal_placement(tmp_path):
    """The bench's legality score feeds placement tuning: the note must not
    pay a placer to pull a connector off the edge."""
    r = _place(_unlisted(tmp_path))
    assert _edge_warnings(r)
    assert not [v for v in place_metrics.illegal(r["violations"])
                if v["kind"] == "mating_edge_unjudged"]

# #951/#891: rows for the pinned boards' edge connectors and the power and RF
# jacks the corpus left unjudged
def test_rows_know_the_pinned_boards_edge_connectors():
    fam = lambda n: (matinglib.family_of("x:" + n) or {}).get("family")
    for name in ("CONN-TH_P5.00_KF301-5.0-2P",
                 "CONN-TH_4P-P5.08_DIBO_DB128V-5.08",
                 "CONN-TH_2P-P5.00_WJ500V-5.08-2P",
                 "TerminalBlock_Phoenix_PT-1,5-2-5.0-H_1x02_P5.00mm_Horizontal"):
        assert fam(name) == "screw_terminal_side", name
    assert fam("CONN-TH_XDJK-0271-1650") == "rca_jack"
    assert fam("CONN_MTJ-889X1_ADM") == "rj45"
    assert fam("CONN_MTJ-669X1_ADM") == "modular_6p"
    # a top-entry terminal block is still not judged as a side-entry one
    assert fam("CONN-TH_P5.08_KF128-5.08-2P") is None
    for name in ("CONN-TH_XT30PW-M", "CONN-TH_XT60PW-M", "TH_2P-XT60PW-F",
                 "CONN-TH_MR60PW", "AMASS_XT30PW-F_1x02_P2.50mm_Horizontal"):
        assert fam(name) == "xt_power", name
    assert fam("CONN-TH_BNC-KWE-6") == "bnc_right_angle"
    assert fam("CONN-TH_L17.5-W17.5_N-KFD") == "n_flange"
    assert fam("MMCX-TH_DL-MMCX-KE-180") == "mmcx_vertical"
    assert fam("RD_SMPM_925-143J-51P") == "smpm_vertical"
    t = matinglib.family_of("x:CONN-TH_L17.5-W17.5_N-KFD")
    assert t["entry"] == "vertical" and t["finger_mm"] == 4.25


# KF301-5.0-2P: two pads of one size on the pitch line, the body reaching as
# far each way, so the pad rule reads nothing; the `mouth` entry says +y
KF_PADS = _pad("1", -2.5, 0, 2.0, 2.0, kind="thru_hole circle") + \
    _pad("2", 2.5, 0, 2.0, 2.0, kind="thru_hole circle")


def _kf(tmp_path, angle, extra=""):
    return _board(tmp_path, _fp("J6", "CONN-TH_P5.00_KF301-5.0-2P", 15.0,
                                16.5, angle, pads=KF_PADS,
                                cy=(-5.0, -3.8, 5.0, 3.8)) + extra)


def test_screw_terminal_facing_the_edge_passes(tmp_path):
    r = _mating(_kf(tmp_path, 0))
    assert r["status"] == "pass", r["violations"]
    assert r["edge_unjudged"] == []
    assert matinglib.pad_mouth(next(
        f for f in __import__("placelib").PlaceModel(_kf(tmp_path, 0))
        .footprints.values() if f.ref == "J6")) is None


def test_planted_screw_terminal_turned_round_fails(tmp_path):
    """The planted fault: lite-main's J4 - wire entries into the board."""
    r = _mating(_kf(tmp_path, 180))
    v = [v for v in r["violations"] if v["kind"] == "mating_faces_inward"]
    assert len(v) == 1 and v[0]["refs"] == ["J6"], r["violations"]
    assert v[0]["severity"] == "error"


def test_a_wire_needs_no_grip_beside_the_block(tmp_path):
    """A part just beside the block, past the edge, does not block its
    wires (core J11 and the RCA jack J4); one in front of the openings does."""
    beside = _fp("J9", "BOX_X_H8", 21.2, 22.0, cy=(-1.0, -3.0, 1.0, 3.0))
    r = _mating(_kf(tmp_path, 0, beside))
    assert not [v for v in r["violations"]
                if v["kind"] == "mating_zone_blocked"], r["violations"]
    ahead = _fp("J9", "BOX_X_H8", 17.5, 22.0, cy=(-1.0, -1.0, 1.0, 1.0))
    r = _mating(_kf(tmp_path, 0, ahead))
    assert [v["refs"] for v in r["violations"]
            if v["kind"] == "mating_zone_blocked"] == [["J6", "J9"]]


def _n_jack(tmp_path, dx):
    # 17.5 mm flange in the middle of a 40 x 40 board, a 5 mm tall part dx
    # mm off its courtyard
    return _board(tmp_path, _fp("J1", "CONN-TH_L17.5-W17.5_N-KFD", 20.0, 20.0,
                                pads=_pad("1", 0, 0, 1.5, 1.5,
                                          kind="thru_hole circle"),
                                cy=(-8.75, -8.75, 8.75, 8.75))
                  + small("U1", 28.75 + dx + 0.75, 20.0, fpid="X_H5"),
                  w=40.0, h=40.0)


def test_planted_part_under_the_n_plugs_coupling_nut_fails(tmp_path):
    """The planted fault: a 5 mm part 3.5 mm off the N jack's flange, where
    the 21 mm coupling nut and the hand turning it go (the default 2.5 mm
    finger room would pass it)."""
    r = _mating(_n_jack(tmp_path, 3.5))
    v = [v for v in r["violations"] if v["kind"] == "mating_zone_blocked"]
    assert len(v) == 1 and v[0]["refs"] == ["J1", "U1"], r["violations"]
    r = _mating(_n_jack(tmp_path, 5.0))
    assert not [v for v in r["violations"]
                if v["kind"] == "mating_zone_blocked"], r["violations"]
