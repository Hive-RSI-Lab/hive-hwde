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
