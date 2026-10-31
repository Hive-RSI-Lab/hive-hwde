"""route_quality (a layout judged as good, not only legal) and route_improve's
re-route rounds that stop when they stop improving.

  - a straight route scores 1.0, a zig-zag between the same pads scores
    lower, and its bends cost on top of its length          -> test_zigzag_*
  - vias past what the pads' layers need cost VIA_MM each   -> test_vias_*
  - a matched pair is judged against its longest member's
    shortest route: meander up to that is free, past it is
    charged                                                 -> test_matched_*
  - nets classify plane / diff_pair / matched / power /
    signal, a pour net is never judged, and an unrouted
    net is not scored as perfect                            -> test_classes_*
  - route_improve keeps a round that raises the score,
    restores one that doesn't, puts back the nets a round
    made worse, and stops after `patience` flat rounds      -> test_improve_*
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
import geom  # noqa: E402
import route_improve  # noqa: E402
import route_quality as rq  # noqa: E402


def _fp(ref, x, y, net, layer="F.Cu"):
    return (f'(footprint "R" (layer "{layer}") (at {x} {y}) '
            f'(property "Reference" "{ref}") (pad "1" smd rect (at 0 0) '
            f'(size 1 1) (layers "{layer}") (net "{net}")))\n')


def _seg(a, b, net, layer="F.Cu", uid=""):
    return (f'(segment (start {a[0]} {a[1]}) (end {b[0]} {b[1]}) (width 0.25) '
            f'(layer "{layer}") (net "{net}") (uuid "{uid or id((a, b))}"))\n')


def _path(pts, net, layer="F.Cu"):
    return "".join(_seg(a, b, net, layer, f"{net}{i}")
                   for i, (a, b) in enumerate(zip(pts, pts[1:])))


def _via(at, net):
    return (f'(via (at {at[0]} {at[1]}) (size 0.6) (drill 0.3) '
            f'(layers "F.Cu" "B.Cu") (net "{net}") (uuid "v{at[0]}"))\n')


def _board(tmp_path, body: str, name="b") -> Path:
    p = tmp_path / f"{name}.kicad_pcb"
    p.write_text('(kicad_pcb (version 20240108) (generator "t") '
                 '(general (thickness 1.6))\n(layers (0 "F.Cu" signal) '
                 '(31 "B.Cu" signal) (44 "Edge.Cuts" user))\n' + body + ")\n",
                 encoding="utf-8")
    return p


def _q(pcb, cons=None):
    return rq.score_board(geom.BoardGeom.from_file(pcb), cons)


def test_octilinear_metric_and_mst():
    assert rq.octi((0, 0), (10, 0)) == pytest.approx(10)
    assert rq.octi((0, 0), (10, 10)) == pytest.approx(10 * 2 ** 0.5)
    assert rq.octi((0, 0), (10, 4)) == pytest.approx(6 + 4 * 2 ** 0.5)
    assert rq.mst_length([(0, 0), (10, 0), (20, 0)]) == pytest.approx(20)
    assert rq.mst_length([(0, 0)]) == 0


def test_zigzag_scores_under_a_straight_route(tmp_path):
    pads = _fp("R1", 0, 0, "A") + _fp("R2", 20, 0, "A")
    straight = _q(_board(tmp_path, pads + _path([(0, 0), (20, 0)], "A"), "s"))
    zz = [(0, 0)] + [(2 * i + 2, 2 if i % 2 == 0 else 0) for i in range(9)] + [(20, 0)]
    zig = _q(_board(tmp_path, pads + _path(zz, "A"), "z"))
    a, z = straight["nets"]["A"], zig["nets"]["A"]
    assert a["quality"] == pytest.approx(1.0)
    assert z["bends"] == 9
    # length alone: 20 * sqrt2 against 20; the 8 excess bends cost on top
    assert z["cost_mm"] == pytest.approx(z["length_mm"] + 8 * rq.BEND_MM, abs=1e-3)
    assert z["quality"] < 20 / z["length_mm"] < 1.0
    assert zig["score"] < straight["score"]
    assert zig["classes"]["signal"]["worst"][0]["net"] == "A"


def test_vias_past_the_pads_need_cost(tmp_path):
    same = _fp("R1", 0, 0, "A") + _fp("R2", 10, 0, "A")
    hop = (same + _seg((0, 0), (3, 0), "A") + _via((3, 0), "A")
           + _seg((3, 0), (7, 0), "A", "B.Cu", "x") + _via((7, 0), "A")
           + _seg((7, 0), (10, 0), "A", uid="y"))
    f = _q(_board(tmp_path, hop))["nets"]["A"]
    assert f["free_vias"] == 0 and f["vias"] == 2
    assert f["cost_mm"] == pytest.approx(10 + 2 * rq.VIA_MM)
    # pads on opposite sides: one via is the least the net can have
    cross = (_fp("R1", 0, 0, "B") + _fp("R2", 10, 0, "B", "B.Cu")
             + _seg((0, 0), (5, 0), "B") + _via((5, 0), "B")
             + _seg((5, 0), (10, 0), "B", "B.Cu", "z"))
    g = _q(_board(tmp_path, cross, "c"))["nets"]["B"]
    assert g["free_vias"] == 1 and g["quality"] == pytest.approx(1.0)


def test_matched_meander_to_the_target_is_free_past_it_is_charged(tmp_path):
    # A spans 20 mm, B 10 mm: B may meander to 20 mm and no further
    body = (_fp("R1", 0, 0, "A") + _fp("R2", 20, 0, "A")
            + _fp("R3", 0, 10, "B") + _fp("R4", 10, 10, "B")
            + _path([(0, 0), (20, 0)], "A")
            + _path([(0, 10), (0, 15), (10, 15), (10, 10)], "B"))
    cons = {"length_match": [{"nets": ["A", "B"]}]}
    q = _q(_board(tmp_path, body), cons)
    b = q["nets"]["B"]
    assert b["class"] == "matched" and b["ideal_mm"] == pytest.approx(20)
    assert b["quality"] == pytest.approx(20 / (20 + rq.BEND_MM), abs=1e-4)
    # the same B unmatched is charged its detour
    free = _q(_board(tmp_path, body, "u"))["nets"]["B"]
    assert free["class"] == "signal" and free["quality"] < 0.6
    # a zig-zag past the group's target is charged even when matched
    long = (body.replace(_path([(0, 10), (0, 15), (10, 15), (10, 10)], "B"),
                         _path([(0, 10), (0, 25), (10, 25), (10, 10)], "B")))
    lb = _q(_board(tmp_path, long, "l"), cons)["nets"]["B"]
    assert lb["quality"] < b["quality"]
    # a length_groups entry (globs, a reference net) is matched the same way
    groups = {"length_groups": [{"nets": ["B*"], "ref_net": "A", "tol_mm": 1}]}
    gb = _q(_board(tmp_path, body, "g"), groups)["nets"]["B"]
    assert gb["class"] == "matched" and gb["ideal_mm"] == pytest.approx(20)


def test_classes_and_unjudged_nets(tmp_path):
    body = (_fp("R1", 0, 0, "GND") + _fp("R2", 10, 0, "GND")
            + _fp("R3", 0, 5, "USB_DP") + _fp("R4", 10, 5, "USB_DP")
            + _fp("R5", 0, 6, "USB_DM") + _fp("R6", 10, 6, "USB_DM")
            + _fp("R7", 0, 9, "+5V") + _fp("R8", 10, 9, "+5V")
            + _fp("R9", 0, 12, "SIG") + _fp("R10", 30, 12, "SIG")
            + _path([(0, 0), (10, 0)], "GND")
            + _path([(0, 5), (10, 5)], "USB_DP")
            + _path([(0, 6), (10, 6)], "USB_DM")
            + _path([(0, 9), (10, 9)], "+5V")
            + _path([(0, 12), (5, 12)], "SIG"))
    cons = {"planes": [{"layer": "B.Cu", "net": "GND"}],
            "power": [{"net": "+5V", "current_a": 1}]}
    q = _q(_board(tmp_path, body), cons)
    cls = {n: f["class"] for n, f in q["nets"].items()}
    assert cls == {"GND": "plane", "USB_DP": "diff_pair", "USB_DM": "diff_pair",
                   "+5V": "power", "SIG": "signal"}
    assert q["nets"]["GND"]["quality"] is None
    # 5 mm of track on a 30 mm net is not a perfect route, it is not a route
    assert q["nets"]["SIG"]["quality"] is None
    assert "half" in q["nets"]["SIG"]["reason"]
    assert set(q["classes"]) == {"plane", "diff_pair", "power", "signal"}
    assert q["classes"]["signal"]["score"] is None


def test_cli_warns_only_under_min_quality(tmp_path):
    zz = [(0, 0), (5, 5), (10, 0)]
    pcb = _board(tmp_path, _fp("R1", 0, 0, "A") + _fp("R2", 10, 0, "A")
                 + _path(zz, "A"))
    payload, _ = rq.run(["--pcb", str(pcb)])
    assert payload["violations"] == [] and payload["quality"]["judged"] == 1
    payload, _ = rq.run(["--pcb", str(pcb), "--min-quality", "0.9"])
    assert [v["severity"] for v in payload["violations"]] == ["warning"]
    assert payload["violations"][0]["net"] == "A"


# ------------------------------------------------------------ route_improve

def _improve_fixture(tmp_path):
    """Three nets, each a zig-zag; the fake router straightens what it is
    given, or (for net C) lays it longer."""
    body = ""
    for i, n in enumerate("ABC"):
        y = 10 * i
        body += _fp(f"R{2 * i}", 0, y, n) + _fp(f"R{2 * i + 1}", 20, y, n)
        body += _path([(0, y), (5, y + 5), (10, y), (15, y + 5), (20, y)], n)
    return _board(tmp_path, body), body


def _clean():
    return {"violations": [], "counts": {"total": 0}}


def _rewrite(pcb: Path, net: str, pts):
    import re
    text = pcb.read_text(encoding="utf-8")
    text = re.sub(rf'\(segment [^\n]*\(net "{re.escape(net)}"\)[^\n]*\n', "", text)
    text = text.rstrip()[:-1] + _path(pts, net) + ")\n"
    pcb.write_text(text, encoding="utf-8")


def _fake_router(longer=()):
    calls = []

    def reroute(staged, drc, nets):
        calls.append(list(nets))
        for n in nets:
            y = 10 * "ABC".index(n)
            pts = ([(0, y), (0, y + 5), (20, y + 5), (20, y)] if n in longer
                   else [(0, y), (20, y)])
            _rewrite(staged, n, pts)
        return _clean()

    def restore(staged, backup, nets):
        old = backup.read_text(encoding="utf-8")
        for n in nets:
            y = 10 * "ABC".index(n)
            assert f'(net "{n}")' in old
            _rewrite(staged, n, [(0, y), (5, y + 5), (10, y), (15, y + 5), (20, y)])
        return _clean()

    return reroute, restore, calls


def _run(monkeypatch, pcb, **kw):
    monkeypatch.setattr(route_improve.kc, "run_drc", lambda *a, **k: _clean())
    return route_improve.improve_staged(
        Path("kicad-cli"), pcb, None, None, classes=["signal"], refill=False,
        **kw)


def test_improve_keeps_gains_and_stops_when_flat(tmp_path, monkeypatch):
    pcb, _ = _improve_fixture(tmp_path)
    reroute, restore, calls = _fake_router()
    f = _run(monkeypatch, pcb, batch=1, rounds=10, patience=2,
             reroute=reroute, restore=restore)
    assert f["score_after"] > f["score_before"]
    assert f["score_after"] == pytest.approx(1.0)
    assert f["kept_rounds"] == 3 and f["stopped"] == "no candidate left"
    assert sorted(c[0] for c in calls) == ["A", "B", "C"]


def test_improve_restores_the_nets_a_round_made_worse(tmp_path, monkeypatch):
    pcb, _ = _improve_fixture(tmp_path)
    reroute, restore, _calls = _fake_router(longer={"C"})
    f = _run(monkeypatch, pcb, batch=3, rounds=5, reroute=reroute,
             restore=restore)
    r = f["rounds"][0]
    assert r["kept"] and r["restored"] == ["C"]
    q = _q(pcb)["nets"]
    assert q["A"]["quality"] == pytest.approx(1.0)
    assert q["C"]["quality"] < 1.0 and q["C"]["length_mm"] < 40


def test_improve_rejects_a_round_and_stops_after_patience(tmp_path, monkeypatch):
    pcb, _ = _improve_fixture(tmp_path)
    before = pcb.read_text(encoding="utf-8")
    reroute, restore, _calls = _fake_router(longer=set("ABC"))
    f = _run(monkeypatch, pcb, batch=1, rounds=10, patience=2,
             reroute=reroute, restore=restore)
    assert f["kept_rounds"] == 0 and len(f["rounds"]) == 2
    assert f["stopped"].startswith("stopped improving")
    assert all(not r["kept"] for r in f["rounds"])
    assert pcb.read_text(encoding="utf-8") == before


def test_improve_rejects_a_round_that_grows_drc():
    before = _clean()
    after = {"violations": [{"source": "drc", "check": "clearance",
                             "severity": "error"}]}
    why = route_improve.judge_round(before, after, 0.7, 0.9)
    assert why and "clearance" in why
    un = {"violations": [{"source": "unconnected", "net": "A",
                          "severity": "error"}]}
    assert "unconnected" in route_improve.judge_round(before, un, 0.7, 0.9)
    assert route_improve.judge_round(before, before, 0.7, 0.7)
    assert route_improve.judge_round(before, before, 0.7, 0.7001) is None


def test_improve_keeps_a_small_gain_but_counts_it_toward_stopping(
        tmp_path, monkeypatch):
    pcb, _ = _improve_fixture(tmp_path)
    reroute, restore, _calls = _fake_router()
    f = _run(monkeypatch, pcb, batch=1, rounds=10, patience=2, min_gain=0.5,
             reroute=reroute, restore=restore)
    assert [r["kept"] for r in f["rounds"]] == [True, True]
    assert f["stopped"].startswith("stopped improving")


def test_improve_leaves_intent_classes_and_kept_nets_alone(tmp_path):
    body = (_fp("R1", 0, 0, "USB_DP") + _fp("R2", 20, 0, "USB_DP")
            + _fp("R3", 0, 1, "USB_DM") + _fp("R4", 20, 1, "USB_DM")
            + _fp("R5", 0, 9, "S") + _fp("R6", 20, 9, "S")
            + _fp("R7", 0, 19, "T") + _fp("R8", 20, 19, "T")
            + _path([(0, 0), (10, 8), (20, 0)], "USB_DP")
            + _path([(0, 1), (10, 9), (20, 1)], "USB_DM")
            + _path([(0, 9), (10, 15), (20, 9)], "S")
            + _path([(0, 19), (10, 25), (20, 19)], "T"))
    q = _q(_board(tmp_path, body))
    got = route_improve.candidates(q, route_improve.DEFAULT_CLASSES, {"T"},
                                   set(), 0.95, 10)
    assert got == ["S"]


def test_improve_judges_the_round_as_routed_when_restoring_clashes(
        tmp_path, monkeypatch):
    pcb, _ = _improve_fixture(tmp_path)
    reroute, _restore, _calls = _fake_router(longer={"C"})

    def clashing(staged, backup, nets):
        _restore(staged, backup, nets)
        return {"violations": [{"source": "drc", "check": "shorting_items",
                                "severity": "error"}], "counts": {"total": 1}}

    f = _run(monkeypatch, pcb, batch=3, rounds=1, reroute=reroute,
             restore=clashing)
    r = f["rounds"][0]
    assert "restored" not in r and "shorting_items" in r["restore_skipped"]
    # A and B straight, C as the router laid it: still a gain, so kept
    assert r["kept"]
    assert _q(pcb)["nets"]["C"]["length_mm"] == pytest.approx(30)
