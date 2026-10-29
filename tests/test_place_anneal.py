"""S10 acceptance tests: SA placement refinement (place_anneal).

Plan S10 accept criteria:
  - on golden board 2 stripped of placement (board_init -> seed): annealer
    beats the seed placement by >=20% HPWL
  - results reproducible per seed; runtime bounded
  - route-feedback mode is stubbed behind --route-feedback until S11 (the
    blending logic is exercised here with an injected fake probe)

Pure tests (engine invariants, cost terms, determinism, edge handling,
feedback blending) run with no toolchain and are unmarked; tests that build
the corpus board or drive SWIG carry `smoke`.
"""
from __future__ import annotations

import json
import math
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
import env  # noqa: E402
import placelib  # noqa: E402
import place_anneal  # noqa: E402
from geom import _rot  # noqa: E402
from place_anneal import Engine, Params, _seg_cross  # noqa: E402


# ---- synthetic boards (test_place.py helpers) ------------------------------

def _fp(ref: str, x: float, y: float, angle: float = 0.0,
        layer: str = "F.Cu", pads: str = "", courtyard: str | None = "rect",
        cy: tuple = (-1.0, -1.0, 1.0, 1.0), attr: str | None = "smd",
        locked: bool = False) -> str:
    at = f"(at {x} {y} {angle})" if angle else f"(at {x} {y})"
    crt = "F.CrtYd" if layer.startswith("F.") else "B.CrtYd"
    court = ""
    if courtyard == "rect":
        court = (f'    (fp_rect (start {cy[0]} {cy[1]}) (end {cy[2]} {cy[3]})'
                 f' (stroke (width 0.05)) (fill no) (layer "{crt}"))\n')
    lock = "    (locked yes)\n" if locked else ""
    att = f"    (attr {attr})\n" if attr else ""
    return (f'  (footprint "t:{ref}" (layer "{layer}")\n'
            f'    {at}\n'
            f'    (property "Reference" "{ref}" (at 0 0 0))\n'
            f'{lock}{att}{court}{pads})\n')


def _pad(num: str, x: float, y: float, net: str | None = None,
         size: float = 0.6, kind: str = "smd rect",
         layers: str = '"F.Cu"') -> str:
    n = f' (net "{net}")' if net else ""
    return (f'    (pad "{num}" {kind} (at {x} {y}) (size {size} {size})'
            f' (layers {layers}){n})\n')


def _pcb(tmp_path_factory, name: str, body: str, w: float = 60.0,
         h: float = 40.0) -> Path:
    text = f"""(kicad_pcb
  (version 20260206) (generator "test")
  (general (thickness 1.6))
  (layers (0 "F.Cu" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user))
  (setup)
  (gr_rect (start 0 0) (end {w} {h}) (stroke (width 0.1)) (fill no)
    (layer "Edge.Cuts"))
{body})
"""
    p = tmp_path_factory.mktemp(name) / f"{name}.kicad_pcb"
    p.write_text(text, encoding="utf-8")
    return p


def _scatter_board(tmp_path_factory, name="scatter") -> Path:
    """U1 center-ish with 4 nets; R1..R4 sharing one net each, scattered to
    the corners; J1 declared on the left edge; C1 a decoupling satellite."""
    body = _fp("U1", 40, 20, cy=(-3, -3, 3, 3),
               pads=_pad("1", -2, -2, "A") + _pad("2", 2, -2, "B")
               + _pad("3", 2, 2, "C") + _pad("4", -2, 2, "VCC"))
    body += _fp("R1", 4, 4, pads=_pad("1", -0.5, 0, "A")
                + _pad("2", 0.5, 0, "GND"))
    body += _fp("R2", 55, 4, pads=_pad("1", -0.5, 0, "B")
                + _pad("2", 0.5, 0, "GND"))
    body += _fp("R3", 55, 36, pads=_pad("1", -0.5, 0, "C")
                + _pad("2", 0.5, 0, "GND"))
    body += _fp("R4", 4, 36, pads=_pad("1", -0.5, 0, "VCC")
                + _pad("2", 0.5, 0, "GND"))
    body += _fp("C1", 20, 35, pads=_pad("1", -0.5, 0, "VCC")
                + _pad("2", 0.5, 0, "GND"))
    # J1 flush on the left edge: the annealer only SLIDES edge clusters
    # (seating them flush is place_seed's job), so the fixture starts legal
    body += _fp("J1", 2, 20, cy=(-2, -3, 2, 3),
                pads=_pad("1", 0, -1, "A") + _pad("2", 0, 1, "GND"))
    return _pcb(tmp_path_factory, name, body)


SCATTER_CON = {"power": [{"net": "VCC", "current_a": 1.0}],
               "placement": {"edges": [{"ref": "J1", "edge": "left"}]}}
SCATTER_DEC = {"associations": [
    {"cap": "C1", "ic": "U1", "pin": "4", "rail": "VCC", "value": "100nF"}]}

FAST = dict(moves_per_cluster=25, max_epochs=12, stall=6, candidates=2)


def _fold_ops(model, ops) -> None:
    """Apply an absolute op list to a parsed model without SWIG.

    Honors `side`: a flipped op must mirror the local frame (placelib.mirror)
    or every cross-side pair reads as a courtyard overlap."""
    for o in ops:
        fp = model.footprints[o["ref"]]
        if o.get("side") and fp.side != o["side"]:
            fp.mirror()
        fp.pos = (o["x"], o["y"])
        fp.angle = o["deg"]


def _run_anneal(pcb, constraints=SCATTER_CON, decoupling=SCATTER_DEC,
                seed=1, probe=None, **kw):
    params = Params(seed=seed, **{**FAST, **kw})
    return place_anneal.anneal(pcb, constraints, decoupling, params,
                               route_probe=probe)


def _engine(pcb, constraints=SCATTER_CON, decoupling=SCATTER_DEC, **kw):
    model = placelib.PlaceModel(pcb)
    placement = constraints.get("placement") or {}
    clusters, warns = placelib.build_clusters(model, decoupling, placement)
    bodies = place_anneal._build_bodies(model, clusters, warns)
    return Engine(model, bodies, constraints, decoupling, **kw), model


# ============================================================ pure: geometry

def test_seg_cross_predicate():
    assert _seg_cross((0, 0), (2, 2), (0, 2), (2, 0))
    assert not _seg_cross((0, 0), (2, 2), (3, 0), (5, 2))       # disjoint
    assert not _seg_cross((0, 0), (2, 2), (2, 2), (4, 0))       # endpoint touch
    assert not _seg_cross((0, 0), (2, 2), (1, 1), (3, 3))       # collinear
    assert not _seg_cross((0, 0), (2, 0), (1, 0), (1, 2))       # T-touch


def test_engine_hpwl_matches_placelib(tmp_path_factory):
    pcb = _scatter_board(tmp_path_factory, "hpwlref")
    eng, model = _engine(pcb)
    # engine normalizes satellites into slots; apply that state to the model
    place_anneal._apply_state(model, eng.bodies, eng.centers, eng.angles)
    want = placelib.hpwl(model)["total_mm"]
    assert eng.hpwl_raw_total == pytest.approx(want, abs=0.01)


def test_incremental_matches_full_sync(tmp_path_factory):
    import random
    pcb = _scatter_board(tmp_path_factory, "increm")
    eng, _model = _engine(pcb)
    rng = random.Random(42)
    movable = [b.cid for b in eng.bodies if b.kind != "edge_fixed"]
    for _ in range(60):
        cid = rng.choice(movable)
        c = (rng.uniform(2, 58), rng.uniform(2, 38))
        a = rng.choice((0.0, 90.0, 180.0, 270.0))
        eng.set_state(cid, c, a)
    kept = (eng.hpwl_raw_total, eng.hpwl_w_total, eng.overlap_total,
            eng.overflow, eng.cross_total, eng.rule_total)
    eng.full_sync()
    fresh = (eng.hpwl_raw_total, eng.hpwl_w_total, eng.overlap_total,
             eng.overflow, eng.cross_total, eng.rule_total)
    for k, f in zip(kept, fresh):
        assert k == pytest.approx(f, abs=1e-6)


def test_crossings_and_congestion_react_to_moves(tmp_path_factory):
    # A(R1-R2 horizontal) and B(R3-R4) - move R3/R4 to cross A, then away
    body = _fp("R1", 10, 20, pads=_pad("1", 0, 0, "A"))
    body += _fp("R2", 50, 20, pads=_pad("1", 0, 0, "A"))
    body += _fp("R3", 30, 5, pads=_pad("1", 0, 0, "B"))
    body += _fp("R4", 30, 35, pads=_pad("1", 0, 0, "B"))
    pcb = _pcb(tmp_path_factory, "cross", body)
    eng, _m = _engine(pcb, {"placement": {}}, {})
    assert eng.cross_total == 1.0          # vertical B crosses horizontal A
    r3 = next(b.cid for b in eng.bodies if b.cluster.anchor == "R3")
    eng.set_state(r3, (10.0, 35.0), 0.0)   # B now to one side, no crossing
    assert eng.cross_total == 0.0
    assert eng.overflow >= 0


def test_congestion_overflow_counts_demand_above_cap(tmp_path_factory):
    # 6 two-pad nets all funneled through the same 2 mm cell column
    body = ""
    for i in range(6):
        body += _fp(f"L{i}", 20, 19 + 0.1 * i,
                    pads=_pad("1", 0, 0, f"N{i}"))
        body += _fp(f"R{i}", 40, 19 + 0.1 * i,
                    pads=_pad("1", 0, 0, f"N{i}"))
    pcb = _pcb(tmp_path_factory, "cong", body)
    eng, _m = _engine(pcb, {"placement": {}}, {}, cong_cap=4)
    assert eng.overflow > 0
    eng2, _m2 = _engine(pcb, {"placement": {}}, {}, cong_cap=50)
    assert eng2.overflow == 0


def test_rule_terms(tmp_path_factory):
    pcb = _scatter_board(tmp_path_factory, "rules")
    eng, _m = _engine(pcb)
    # high-current path: current_a * hpwl(VCC) folded into rule_total
    assert eng.rule_total == pytest.approx(
        1.0 * eng.hpwl_raw["VCC"], abs=1e-6)
    # separation groups
    con = {**SCATTER_CON,
           "placement": {**SCATTER_CON["placement"],
                         "separation": [{"a": ["R1"], "b": ["R2"],
                                         "min_mm": 100.0}]}}
    eng2, _m2 = _engine(pcb, con)
    assert eng2.rule_total > eng.rule_total   # R1/R2 are < 100 mm apart
    assert eng2.sep_unknown_refs == []
    # S14: refs absent from the board must be SURFACED, not silently dropped
    con_bad = {**SCATTER_CON,
               "placement": {**SCATTER_CON["placement"],
                             "separation": [{"a": ["ZZ9"], "b": ["R2"],
                                             "min_mm": 8.0}]}}
    eng_bad, _mb = _engine(pcb, con_bad)
    assert eng_bad.sep_unknown_refs == ["ZZ9"]
    # thermal spreading fires when hot parts sit closer than the spread
    con3 = {**SCATTER_CON, "thermal": [{"ref": "R1", "power_w": 1.0},
                                       {"ref": "R2", "power_w": 1.0}]}
    eng3, _m3 = _engine(pcb, con3)
    r1 = next(b.cid for b in eng3.bodies if b.cluster.anchor == "R1")
    r2 = next(b.cid for b in eng3.bodies if b.cluster.anchor == "R2")
    base = eng3.rule_total
    eng3.set_state(r1, (30.0, 20.0), 0.0)
    eng3.set_state(r2, (32.0, 20.0), 0.0)   # 2 mm apart < 10 mm spread
    assert eng3.rule_total > base


def test_gnd_excluded_from_mst_terms(tmp_path_factory):
    pcb = _scatter_board(tmp_path_factory, "gndmst")
    eng, _m = _engine(pcb)
    assert "GND" not in eng.mst_nets
    assert "GND" in eng.nets                # still counts toward hpwl
    assert eng._wnet["GND"] == place_anneal.W_GND


def _pro(pcb: Path, patterns: list, classes: list) -> None:
    pro = {"net_settings": {"classes": classes,
                            "netclass_patterns": patterns}}
    pcb.with_suffix(".kicad_pro").write_text(json.dumps(pro),
                                             encoding="utf-8")


DEFAULT_CLS = {"name": "Default", "track_width": 0.2, "clearance": 0.2}


def test_net_pitch_units_reads_netclass_and_dru(tmp_path_factory):
    pcb = _scatter_board(tmp_path_factory, "pitch")
    nets = ["A", "B", "C", "GND", "VCC"]
    # no project: every net demands one track, as before widths counted
    assert place_anneal.net_pitch_units(pcb, nets) == {}
    _pro(pcb, [{"netclass": "Power", "pattern": "VCC"}],
         [DEFAULT_CLS, {"name": "Power", "track_width": 1.0,
                        "clearance": 0.2}])
    pcb.with_suffix(".kicad_dru").write_text(
        "(version 1)\n(rule \"w_B\"\n"
        "\t(constraint track_width (min 0.6000mm))\n"
        "\t(condition \"A.NetName == 'B' && A.Type == 'track'\")\n)\n",
        encoding="utf-8")
    got = place_anneal.net_pitch_units(pcb, nets)
    # (1.0 + 0.2) / (0.2 + 0.2) from the netclass; (0.6 + 0.2) / 0.4 from
    # the DRU floor; Default-width nets are left out (1.0)
    assert got == {"B": 2.0, "VCC": 3.0}


def test_congestion_charges_gnd_and_width(tmp_path_factory):
    pcb = _scatter_board(tmp_path_factory, "cwidth")
    eng, _m = _engine(pcb, cong_cap=1)
    # GND stays out of the crossing term but now demands channel room
    assert "GND" not in eng.mst_nets and "GND" in eng.cong_nets
    assert eng.plane_nets == []
    narrow = sum(eng.demand.values())
    _pro(pcb, [{"netclass": "Power", "pattern": "VCC"}],
         [DEFAULT_CLS, {"name": "Power", "track_width": 1.0,
                        "clearance": 0.2}])
    wide, _m2 = _engine(pcb, cong_cap=1)
    assert wide._unit["VCC"] == 3.0 and wide._unit["A"] == 1.0
    # VCC's flight lines now cost 3 tracks each instead of 1
    vcc = sum(wide.netcells["VCC"].values())
    assert sum(wide.demand.values()) == pytest.approx(narrow + 2 * vcc)
    assert wide.overflow > eng.overflow
    # incremental bookkeeping stays exact with fractional demand
    for cid, c in ((0, (30.0, 12.0)), (1, (8.0, 30.0)), (0, (44.0, 22.0))):
        if wide.bodies[cid].kind != "edge_fixed":
            wide.set_state(cid, c, 90.0)
    kept = (wide.overflow, wide.cross_total)
    wide.full_sync()
    assert kept == pytest.approx((wide.overflow, wide.cross_total), abs=1e-6)


def test_plane_net_left_out_of_congestion(tmp_path_factory):
    body = _fp("R1", 10, 20, pads=_pad("1", 0, 0, "GND"))
    body += _fp("R2", 50, 20, pads=_pad("1", 0, 0, "GND"))
    body += _fp("R3", 10, 30, pads=_pad("1", 0, 0, "S"))
    body += _fp("R4", 50, 30, pads=_pad("1", 0, 0, "S"))
    zone = ('  (zone (net "GND") (layers "In1.Cu")\n'
            '    (polygon (pts (xy 0 0) (xy 60 0) (xy 60 40) (xy 0 40))))\n')
    pcb = _pcb(tmp_path_factory, "plane", body + zone)
    text = pcb.read_text(encoding="utf-8").replace(
        '(2 "B.Cu" signal)', '(4 "In1.Cu" power) (2 "B.Cu" signal)')
    pcb.write_text(text, encoding="utf-8")
    eng, _m = _engine(pcb, {"placement": {}}, {})
    assert eng.plane_nets == ["GND"]
    assert eng.cong_nets == ["S"]
    # the same GND pour on an outer layer is not a plane: it is charged
    pcb.write_text(text.replace('(layers "In1.Cu")', '(layers "B.Cu")'),
                   encoding="utf-8")
    eng2, _m2 = _engine(pcb, {"placement": {}}, {})
    assert eng2.plane_nets == [] and eng2.cong_nets == ["GND", "S"]


def test_power_plane_net_still_crosses(tmp_path_factory):
    # usb-buck: +3V3 owns an inner zone, so it leaves the demand, but a
    # power net still counts in the crossings (it crashed with KeyError)
    body = _fp("R1", 10, 20, pads=_pad("1", 0, 0, "P"))
    body += _fp("R2", 50, 20, pads=_pad("1", 0, 0, "P"))
    body += _fp("R3", 30, 5, pads=_pad("1", 0, 0, "S"))
    body += _fp("R4", 30, 35, pads=_pad("1", 0, 0, "S"))
    zone = ('  (zone (net "P") (layers "In1.Cu")\n'
            '    (polygon (pts (xy 0 0) (xy 60 0) (xy 60 40) (xy 0 40))))\n')
    pcb = _pcb(tmp_path_factory, "pplane", body + zone)
    text = pcb.read_text(encoding="utf-8").replace(
        '(2 "B.Cu" signal)', '(4 "In1.Cu" power) (2 "B.Cu" signal)')
    pcb.write_text(text, encoding="utf-8")
    eng, _m = _engine(pcb, {"placement": {}, "power": [{"net": "P"}]}, {})
    assert eng.plane_nets == ["P"] and eng.cong_nets == ["S"]
    assert "P" in eng.mst_nets and "P" not in eng.netcells
    assert eng.cross_total > 0
    # moving the plane net's body updates its crossings, not the demand
    demand = dict(eng.demand)
    eng.set_state(0, (30.0, 30.0), 0.0)
    assert eng.demand == demand
    kept = (eng.overflow, eng.cross_total)
    eng.full_sync()
    assert kept == pytest.approx((eng.overflow, eng.cross_total), abs=1e-6)


# ============================================================ pure: annealing

def test_anneal_improves_and_stays_legal(tmp_path_factory):
    pcb = _scatter_board(tmp_path_factory, "improve")
    before = placelib.hpwl(placelib.PlaceModel(pcb))["total_mm"]
    candidates, facts, _model = _run_anneal(pcb)
    best = candidates[0]
    assert best["legal"], best["violations"]
    assert best["hpwl_mm"] < before
    assert facts["hpwl_input_mm"] == pytest.approx(before, abs=0.01)


def test_board_file_untouched_by_anneal(tmp_path_factory):
    pcb = _scatter_board(tmp_path_factory, "untouched")
    raw = pcb.read_bytes()
    _run_anneal(pcb)
    assert pcb.read_bytes() == raw


def test_deterministic_per_seed(tmp_path_factory):
    pcb = _scatter_board(tmp_path_factory, "determ")
    c1, f1, _ = _run_anneal(pcb, seed=7)
    c2, f2, _ = _run_anneal(pcb, seed=7)
    assert [c["ops"] for c in c1] == [c["ops"] for c in c2]
    assert [c["cost"] for c in c1] == [c["cost"] for c in c2]
    assert f1["moves"] == f2["moves"] and f1["accepted"] == f2["accepted"]
    c3, f3, _ = _run_anneal(pcb, seed=8)
    assert (c3[0]["ops"] != c1[0]["ops"] or f3["accepted"] != f1["accepted"])


def test_satellites_ride_their_anchor(tmp_path_factory):
    pcb = _scatter_board(tmp_path_factory, "satride")
    candidates, _f, model = _run_anneal(pcb)
    # model holds the last candidate applied; re-apply best explicitly
    eng, model = _engine(pcb)
    ops = {o["ref"]: o for o in candidates[0]["ops"]}
    u1, c1 = ops["U1"], ops["C1"]
    # satellite offset in the anchor frame must match the engine's slot for
    # the side the candidate put the cluster on
    b = next(b for b in eng.bodies if b.cluster.anchor == "U1")
    slot, rel = b.variants[u1["side"]].slots["C1"]
    dx, dy = c1["x"] - u1["x"], c1["y"] - u1["y"]
    lx, ly = _rot(dx, dy, u1["deg"])       # back into the anchor frame
    assert (lx, ly) == (pytest.approx(slot[0], abs=0.01),
                        pytest.approx(slot[1], abs=0.01))
    assert (c1["deg"] - u1["deg"]) % 360.0 == pytest.approx(rel % 360.0,
                                                            abs=0.05)


def test_edge_cluster_stays_on_edge(tmp_path_factory):
    pcb = _scatter_board(tmp_path_factory, "edgemv")
    candidates, _f, _m = _run_anneal(pcb)
    model = placelib.PlaceModel(pcb)
    _fold_ops(model, candidates[0]["ops"])
    ext = model.footprints["J1"].extents_abs()
    line = placelib.edge_line(model.outline, "left")
    assert ext.distance(line) <= placelib.EDGE_TOL
    assert model.footprints["J1"].angle == pytest.approx(0.0)  # never rotated


def test_edge_with_explicit_pos_is_frozen(tmp_path_factory):
    pcb = _scatter_board(tmp_path_factory, "edgefix")
    con = {**SCATTER_CON, "placement": {
        "edges": [{"ref": "J1", "edge": "left", "pos": 0.5}]}}
    eng, _m = _engine(pcb, con)
    j1 = next(b for b in eng.bodies if b.cluster.anchor == "J1")
    assert j1.kind == "edge_fixed"
    before = eng.centers[j1.cid]
    candidates, _f, _m2 = _run_anneal(pcb, constraints=con)
    ops = {o["ref"]: o for o in candidates[0]["ops"]}
    # J1's cluster center never moved (origin may differ from center; compare
    # against the engine's own initial state via a fresh engine)
    eng2, _m3 = _engine(pcb, con)
    assert eng2.centers[j1.cid] == before


def test_top_n_distinct_and_sorted(tmp_path_factory):
    pcb = _scatter_board(tmp_path_factory, "topn")
    candidates, _f, _m = _run_anneal(pcb, candidates=3)
    scores = [c["score"] for c in candidates if c["legal"]]
    assert scores == sorted(scores)
    assert [c["rank"] for c in candidates] == list(
        range(1, len(candidates) + 1))
    for i, a in enumerate(candidates):
        for b in candidates[i + 1:]:
            assert a["ops"] != b["ops"]


def test_repair_fixes_forced_overlap(tmp_path_factory):
    pcb = _scatter_board(tmp_path_factory, "repair")
    eng, model = _engine(pcb)
    r1 = next(b.cid for b in eng.bodies if b.cluster.anchor == "R1")
    r2 = next(b.cid for b in eng.bodies if b.cluster.anchor == "R2")
    eng.set_state(r1, (30.0, 20.0), 0.0)
    eng.set_state(r2, (30.2, 20.0), 0.0)   # forced courtyard overlap
    place_anneal._apply_state(model, eng.bodies, eng.centers, eng.angles)
    assert any(v["kind"] == "courtyard_overlap"
               for v in placelib.legality_violations(model, {}))
    moved = place_anneal._repair(model, eng, {})
    assert moved
    assert not [v for v in placelib.legality_violations(model, {})
                if v["severity"] == "error"]


def test_all_locked_yields_note(tmp_path_factory):
    body = _fp("R1", 10, 10, locked=True, pads=_pad("1", 0, 0, "A"))
    body += _fp("R2", 20, 10, locked=True, pads=_pad("1", 0, 0, "A"))
    pcb = _pcb(tmp_path_factory, "alllock", body)
    candidates, facts, _m = _run_anneal(pcb, {"placement": {}}, {})
    assert facts["movable_clusters"] == 0
    assert "note" in facts
    assert len(candidates) == 1


# ============================================================ pure: margin

def test_margin_buffers_overlap_term_only(tmp_path_factory):
    """T6 P6A-6 (ladder row 53): with --margin-mm two bodies closer than the
    margin pay overlap cost even though their TRUE courtyards are clear;
    default 0.0 keeps the exact legacy behavior."""
    pcb = _scatter_board(tmp_path_factory, "margin")
    eng0, _m0 = _engine(pcb)
    r1 = next(b.cid for b in eng0.bodies if b.cluster.anchor == "R1")
    r2 = next(b.cid for b in eng0.bodies if b.cluster.anchor == "R2")
    # park the parts 2.3 mm apart center-to-center: effective extents
    # (courtyard union pad box, +-1.05) leave a 0.2 mm true gap - legal and
    # overlap-free at margin 0
    eng0.set_state(r1, (30.0, 20.0), 0.0)
    eng0.set_state(r2, (32.3, 20.0), 0.0)
    assert eng0.overlap_total == pytest.approx(0.0, abs=1e-9)

    model = placelib.PlaceModel(pcb)
    placement = SCATTER_CON.get("placement") or {}
    clusters, warns = placelib.build_clusters(model, SCATTER_DEC, placement)
    bodies = place_anneal._build_bodies(model, clusters, warns, margin_mm=0.6)
    eng1 = Engine(model, bodies, SCATTER_CON, SCATTER_DEC, margin_mm=0.6)
    eng1.set_state(r1, (30.0, 20.0), 0.0)
    eng1.set_state(r2, (32.3, 20.0), 0.0)
    assert eng1.overlap_total > 0.0          # buffered polys now overlap
    # legality still uses true courtyards: no violation at this spacing
    place_anneal._apply_state(model, eng1.bodies, eng1.centers, eng1.angles)
    assert not [v for v in placelib.legality_violations(model, placement)
                if v["kind"] == "courtyard_overlap"]


def test_margin_anneal_keeps_min_gap(tmp_path_factory):
    """Roomy board + --margin-mm 0.6: the best candidate keeps clear air
    between SAME-SIDE clusters (intra-cluster satellite gaps are not
    margined, and opposite-side bodies never contend for the same air)."""
    pcb = _scatter_board(tmp_path_factory, "margingap")
    candidates, _f, _m = _run_anneal(pcb, margin_mm=0.6)
    best = candidates[0]
    assert best["legal"]
    model = placelib.PlaceModel(pcb)
    _fold_ops(model, best["ops"])
    clusters, _w = placelib.build_clusters(
        model, SCATTER_DEC, SCATTER_CON.get("placement"))
    owner = {r: c.anchor for c in clusters for r in c.refs}
    ext = {r: f.extents_abs() for r, f in model.footprints.items()}
    refs = sorted(owner)
    gaps = [ext[a].distance(ext[b])
            for i, a in enumerate(refs) for b in refs[i + 1:]
            if owner[a] != owner[b]
            and model.footprints[a].side == model.footprints[b].side]
    assert min(gaps) >= 0.3   # soft target: no packed-tight pair remains


# ============================================================ pure: sides (U19)

def _hub_board(tmp_path_factory, name="hub", sat=False):
    """R1 wired to 8 locked anchors ringing the board centre, with the whole
    centre a FRONT-SIDE keepout. On the front R1 is exiled to the frame; on
    the back it sits dead centre. The wirelength gap (~70 mm) clears the
    assembly cost, so a working flip move must find it."""
    ring = [("AN", 30, 8), ("AS", 30, 32), ("AW", 14, 20), ("AE", 46, 20),
            ("ANE", 46, 8), ("ANW", 14, 8), ("ASE", 46, 32), ("ASW", 14, 32)]
    body = ""
    pads = ""
    for i, (ref, x, y) in enumerate(ring):
        body += _fp(ref, x, y, locked=True, cy=(-1.5, -1.5, 1.5, 1.5),
                    pads=_pad("1", 0, 0, f"N{i}"))
        pads += _pad(str(i + 1), -0.7 + 0.2 * i, 0, f"N{i}", size=0.15)
    body += _fp("R1", 4, 36, cy=(-1.2, -0.6, 1.2, 0.6), pads=pads)
    if sat:
        body += _fp("C1", 8, 36, cy=(-0.8, -0.5, 0.8, 0.5),
                    pads=_pad("1", -0.4, 0, "N0") + _pad("2", 0.4, 0, "N1"))
    return _pcb(tmp_path_factory, name, body)


HUB_CON = {"placement": {"keepouts": [
    {"rect": [6, 4, 54, 36], "side": "front", "reason": "display window"}]}}
HUB_SAT_CON = {"placement": {
    **HUB_CON["placement"],
    "groups": [{"name": "pair", "anchor": "R1", "members": ["C1"]}]}}


def test_anneal_discovers_the_back_side(tmp_path_factory):
    """U19 acceptance: where a back-side placement is measurably better the
    annealer FINDS it - and with flips off it cannot, at a real HPWL cost."""
    pcb = _hub_board(tmp_path_factory, "hubfind")
    cands, facts, _m = _run_anneal(pcb, HUB_CON, {}, max_epochs=25, stall=10)
    best = cands[0]
    assert facts["flippable_clusters"] == 1
    assert best["legal"], best["violations"]
    assert best["sides"] == {"front": 0, "back": 1}
    r1 = next(o for o in best["ops"] if o["ref"] == "R1")
    assert r1["side"] == "back"
    # it went where the wirelength is: the centre of the front keepout
    assert 20.0 <= r1["x"] <= 40.0 and 12.0 <= r1["y"] <= 28.0
    assert best["terms"]["assembly_mm"] == pytest.approx(
        place_anneal.ASM_SECOND_SIDE_MM + place_anneal.ASM_PER_PART_MM)

    flat, _f2, _m2 = _run_anneal(pcb, HUB_CON, {}, max_epochs=25, stall=10,
                                 allow_flip=False)
    assert flat[0]["sides"] == {"front": 1, "back": 0}
    assert flat[0]["hpwl_mm"] > best["hpwl_mm"]      # the front cannot match


def test_flip_carries_satellites_and_stays_legal(tmp_path_factory):
    """A flipped anchor takes its satellite with it, at the MIRRORED slot."""
    pcb = _hub_board(tmp_path_factory, "hubsat", sat=True)
    cands, _f, _m = _run_anneal(pcb, HUB_SAT_CON, {}, max_epochs=25, stall=10)
    best = cands[0]
    ops = {o["ref"]: o for o in best["ops"]}
    assert ops["R1"]["side"] == "back" and ops["C1"]["side"] == "back"
    eng, _m2 = _engine(pcb, HUB_SAT_CON, {})
    slot, rel = eng.bodies[0].variants["back"].slots["C1"]
    dx, dy = ops["C1"]["x"] - ops["R1"]["x"], ops["C1"]["y"] - ops["R1"]["y"]
    lx, ly = _rot(dx, dy, ops["R1"]["deg"])
    assert (lx, ly) == (pytest.approx(slot[0], abs=0.01),
                        pytest.approx(slot[1], abs=0.01))
    assert (ops["C1"]["deg"] - ops["R1"]["deg"]) % 360.0 == pytest.approx(
        rel % 360.0, abs=0.05)
    # legality holds on the folded model (mirrored locals, back keepout free)
    model = placelib.PlaceModel(pcb)
    _fold_ops(model, best["ops"])
    assert not [v for v in placelib.legality_violations(
        model, HUB_SAT_CON["placement"]) if v["severity"] == "error"]


def _chain_board(tmp_path_factory, name="chain"):
    """Five tiny parts wired in a chain on a roomy board: the optimum is a
    compact front-side row, and no part gains anything by going to the back
    (nothing is boxed in, so the back buys no space it cannot already have)."""
    body = ""
    corners = [(4, 4), (56, 4), (4, 36), (56, 36), (30, 4)]
    for i, (x, y) in enumerate(corners):
        pads = _pad("1", -0.5, 0, f"N{i - 1}") if i else ""
        pads += _pad("2", 0.5, 0, f"N{i}") if i < len(corners) - 1 else ""
        body += _fp(f"R{i + 1}", x, y, pads=pads)
    return _pcb(tmp_path_factory, name, body)


def test_no_gratuitous_flipping_on_a_roomy_board(tmp_path_factory):
    """U19 acceptance: at the default assembly weight a board that does not
    need two sides stays single-sided.

    Budget note: FAST params leave t_end ~150 on a cost-100 board - the
    schedule never reaches the cold regime, so the winner is whatever the
    random walk happened to see and a side assertion on it would be noise.
    This runs a schedule that actually converges (~1.5 s)."""
    pcb = _chain_board(tmp_path_factory, "noflip")
    params = Params(seed=1, candidates=2, moves_per_cluster=40,
                    max_epochs=40, stall=10)
    cands, facts, _m = place_anneal.anneal(pcb, {"placement": {}}, {}, params)
    assert facts["flippable_clusters"] == 5       # flips WERE available
    for c in cands:
        assert c["sides"]["back"] == 0, c["ops"]
    assert cands[0]["terms"]["assembly_mm"] == 0.0


def test_default_weight_makes_every_single_flip_uphill(tmp_path_factory):
    """The mechanism behind the test above, without the search in the way:
    from the converged single-sided placement, flipping ANY one cluster costs
    more than it saves."""
    pcb = _chain_board(tmp_path_factory, "uphill")
    params = Params(seed=1, candidates=1, moves_per_cluster=40,
                    max_epochs=40, stall=10)
    cands, _f, _m = place_anneal.anneal(pcb, {"placement": {}}, {}, params)
    model = placelib.PlaceModel(pcb)
    _fold_ops(model, cands[0]["ops"])             # the converged placement
    clusters, warns = placelib.build_clusters(model, {}, {})
    eng = Engine(model, place_anneal._build_bodies(model, clusters, warns),
                 {"placement": {}}, {})
    base = eng.cost()
    for b in eng.bodies:
        eng.set_state(b.cid, eng.centers[b.cid], eng.angles[b.cid], "back")
        assert eng.cost() > base, b.refs
        eng.set_state(b.cid, eng.centers[b.cid], eng.angles[b.cid], "front")
    assert eng.cost() == pytest.approx(base, abs=1e-9)


def test_side_pins_and_guards_block_flips(tmp_path_factory):
    """Never flip: a pinned ref, a declared-edge connector, a THT cluster."""
    pcb = _scatter_board(tmp_path_factory, "pins")
    con = {**SCATTER_CON, "placement": {
        **SCATTER_CON["placement"],
        "sides": [{"ref": "R1", "side": "front"},
                  {"ref": "C1", "side": "front", "reason": "under a shield"},
                  {"ref": "ZZ9", "side": "back"}]}}
    eng, _m = _engine(pcb, con)
    by = {b.cluster.anchor: b for b in eng.bodies}
    assert by["R1"].flippable is False and by["R1"].pin_side == "front"
    assert by["U1"].flippable is False        # C1 satellite carries the pin
    assert by["J1"].flippable is False        # declared-edge connector
    assert by["R2"].flippable is True
    assert eng.side_unknown_refs == ["ZZ9"]
    assert eng.side_conflicts == []

    # a THT cluster is never flipped (back-side THT is a hand operation this
    # cost model cannot price)
    body = _fp("R9", 10, 10, pads=_pad("1", 0, 0, "A", kind="thru_hole circle",
                                       layers='"*.Cu"'))
    body += _fp("R8", 30, 10, pads=_pad("1", 0, 0, "A"))
    tht = _pcb(tmp_path_factory, "tht", body)
    eng2, _m2 = _engine(tht, {"placement": {}}, {})
    assert {b.cluster.anchor: b.flippable for b in eng2.bodies} == {
        "R9": False, "R8": True}


def test_side_conflict_is_surfaced_not_fixed(tmp_path_factory):
    """A ref already on the wrong side is reported; the annealer is not the
    fixer, so it stays put (and stays unflippable)."""
    body = _fp("R1", 10, 10, layer="B.Cu", pads=_pad("1", 0, 0, "A",
                                                     layers='"B.Cu"'))
    body += _fp("R2", 30, 10, pads=_pad("1", 0, 0, "A"))
    pcb = _pcb(tmp_path_factory, "sideconf", body)
    con = {"placement": {"sides": [{"ref": "R1", "side": "front"}]}}
    cands, facts, _m = _run_anneal(pcb, con, {})
    assert facts["side_conflicts"] == ["R1 on back, pinned front"]
    assert facts["sides_input"] == {"front": 1, "back": 1}
    assert next(o for o in cands[0]["ops"] if o["ref"] == "R1")["side"] \
        == "back"


def test_assembly_term_prices_the_second_side(tmp_path_factory):
    pcb = _scatter_board(tmp_path_factory, "asm")
    eng, _m = _engine(pcb)
    assert eng.terms()["assembly_mm"] == 0.0 and eng.back_parts == 0
    r2 = next(b.cid for b in eng.bodies if b.cluster.anchor == "R2")
    r3 = next(b.cid for b in eng.bodies if b.cluster.anchor == "R3")
    base = eng.cost()
    eng.set_state(r2, eng.centers[r2], eng.angles[r2], "back")
    assert eng.back_parts == 1
    assert eng.terms()["assembly_mm"] == pytest.approx(
        place_anneal.ASM_SECOND_SIDE_MM + place_anneal.ASM_PER_PART_MM)
    # the SECOND part on an already-open back costs only the per-part term
    step2 = eng.terms()["assembly_mm"]
    eng.set_state(r3, eng.centers[r3], eng.angles[r3], "back")
    assert eng.terms()["assembly_mm"] - step2 == pytest.approx(
        place_anneal.ASM_PER_PART_MM)
    # flipping back is exactly reversible
    eng.set_state(r3, eng.centers[r3], eng.angles[r3], "front")
    eng.set_state(r2, eng.centers[r2], eng.angles[r2], "front")
    assert eng.terms()["assembly_mm"] == 0.0
    assert eng.cost() == pytest.approx(base, abs=1e-9)


def test_flip_updates_match_full_sync(tmp_path_factory):
    """Incremental maintenance across side changes == full re-derivation."""
    import random
    pcb = _scatter_board(tmp_path_factory, "flipsync")
    eng, _m = _engine(pcb)
    rng = random.Random(3)
    movable = [b.cid for b in eng.bodies if b.flippable]
    for _ in range(40):
        cid = rng.choice(movable)
        eng.set_state(cid, (rng.uniform(4, 56), rng.uniform(4, 36)),
                      rng.choice((0.0, 90.0, 180.0, 270.0)),
                      rng.choice(("front", "back")))
    kept = (eng.hpwl_raw_total, eng.hpwl_w_total, eng.overlap_total,
            eng.overflow, eng.cross_total, eng.rule_total, eng.assembly_raw,
            eng.back_parts)
    eng.full_sync()
    fresh = (eng.hpwl_raw_total, eng.hpwl_w_total, eng.overlap_total,
             eng.overflow, eng.cross_total, eng.rule_total, eng.assembly_raw,
             eng.back_parts)
    for k, f in zip(kept, fresh):
        assert k == pytest.approx(f, abs=1e-6)
    # opposite-side bodies do not collide; same-side ones still do
    a, b = movable[0], movable[1]
    eng.set_state(a, (30.0, 20.0), 0.0, "front")
    eng.set_state(b, (30.0, 20.0), 0.0, "back")
    assert eng.overlap_total == pytest.approx(0.0, abs=1e-9)
    eng.set_state(b, (30.0, 20.0), 0.0, "front")
    assert eng.overlap_total > 0.0


def test_mirror_is_involutive_and_moves_pads(tmp_path_factory):
    """placelib.Footprint.mirror is the in-memory pcbnew Flip."""
    body = _fp("U1", 20, 15, angle=90.0, cy=(-3, -1, 1, 3),
               pads=_pad("1", -2, -1, "A") + _pad("2", 2, 1, "B"))
    pcb = _pcb(tmp_path_factory, "mirror", body)
    model = placelib.PlaceModel(pcb)
    fp = model.footprints["U1"]
    before = ([p.local for p in fp.pads], fp.side,
              list(fp.extents_local().exterior.coords))
    abs_before = {n: (x, y) for n, _net, x, y in fp.pad_centers_abs()}
    fp.mirror()
    assert fp.side == "back"
    assert [p.local for p in fp.pads] == [(-2.0, 1.0), (2.0, -1.0)]
    # mirror() leaves the ANGLE to the caller; pair it with KiCad's angle
    # negation and the board-frame result is an exact mirror about the
    # footprint ORIGIN's y (TOP_BOTTOM - KiCad flips about the part's own
    # position, and pos is untouched)
    fp.angle = (-fp.angle) % 360.0
    for n, _net, x, y in fp.pad_centers_abs():
        assert (x, y) == (pytest.approx(abs_before[n][0]),
                          pytest.approx(2 * fp.pos[1] - abs_before[n][1]))
    fp.angle = (-fp.angle) % 360.0
    fp.mirror()
    assert ([p.local for p in fp.pads], fp.side,
            list(fp.extents_local().exterior.coords)) == before


def test_no_side_flips_flag_disables_the_move(tmp_path_factory, tmp_path):
    pcb = _hub_board(tmp_path_factory, "hubcli")
    (pcb.parent / "constraints.json").write_text(json.dumps(HUB_CON),
                                                 encoding="utf-8")
    rep = tmp_path / "rep.json"
    rc = place_anneal.main([
        "--pcb", str(pcb), "--no-side-flips", "--max-epochs", "10",
        "--stall", "5", "--moves-per-cluster", "20",
        "--out-dir", str(tmp_path / "c"), "--out-report", str(rep)])
    assert rc == 0
    r = json.loads(rep.read_text("utf-8"))
    assert r["flippable_clusters"] == 0
    assert r["sides_best"] == {"front": 1, "back": 0}
    assert r["candidates"][0]["terms"]["assembly_mm"] == 0.0


def test_w_assembly_flag_moves_the_threshold(tmp_path_factory):
    """The owner ruling is a weight, not a hard rule: zero it and the flip is
    free; raise it and the same board stays single-sided."""
    pcb = _hub_board(tmp_path_factory, "hubw")
    free, _f, _m = _run_anneal(pcb, HUB_CON, {}, max_epochs=25, stall=10,
                               weights={"assembly": 0.0})
    assert free[0]["sides"]["back"] == 1
    assert free[0]["terms"]["assembly_mm"] > 0.0     # raw term still reported
    steep, _f2, _m2 = _run_anneal(pcb, HUB_CON, {}, max_epochs=25, stall=10,
                                  weights={"assembly": 20.0})
    assert steep[0]["sides"]["back"] == 0


# ============================================================ pure: corridor

def _corridor_board(tmp_path_factory, name="corr"):
    """A1/A2 fixed at both ends; R1 shares a net with both so HPWL pulls it
    straight into the A1-A2 band; R2 is neutral."""
    body = _fp("A1", 5, 20, pads=_pad("1", 0, 0, "N"))
    body += _fp("A2", 55, 20, pads=_pad("1", 0, 0, "N"))
    body += _fp("R1", 30, 20, pads=_pad("1", -0.5, 0, "N")
                + _pad("2", 0.5, 0, "X"))
    body += _fp("R2", 30, 5, pads=_pad("1", 0, 0, "X"))
    return _pcb(tmp_path_factory, name, body)


CORR_CON = {"placement": {"fixed": ["A1", "A2"],
                          "corridors": [{"a": "A1", "b": "A2",
                                         "width_mm": 6.0}]}}


def test_corridor_term_reacts_and_matches_full_sync(tmp_path_factory):
    import random
    pcb = _corridor_board(tmp_path_factory, "correng")
    eng, _m = _engine(pcb, CORR_CON, {})
    r1 = next(b.cid for b in eng.bodies if b.cluster.anchor == "R1")
    eng.set_state(r1, (30.0, 20.0), 0.0)     # dead center of the swath
    assert eng.corridor_area > 0
    assert eng.terms()["corridor_mm2"] > 0
    in_cost = eng.cost()
    eng.set_state(r1, (30.0, 5.0), 0.0)      # well outside
    assert eng.corridor_area == pytest.approx(0.0, abs=1e-9)
    assert eng.cost() < in_cost
    # incremental maintenance == full re-derivation after random churn
    rng = random.Random(7)
    movable = [b.cid for b in eng.bodies if b.kind != "edge_fixed"]
    for _ in range(40):
        eng.set_state(rng.choice(movable),
                      (rng.uniform(2, 58), rng.uniform(2, 38)),
                      rng.choice((0.0, 90.0, 180.0, 270.0)))
    kept = (eng.corridor_area, eng.rule_total)
    eng.full_sync()
    assert kept[0] == pytest.approx(eng.corridor_area, abs=1e-6)
    assert kept[1] == pytest.approx(eng.rule_total, abs=1e-6)


def test_corridor_clears_the_swath(tmp_path_factory):
    """T6 P6A-5 acceptance: with the corridor declared the best candidate
    leaves the swath body-free; without it R1 parks inside (the pd-trigger
    hand-floorplan failure mode)."""
    pcb = _corridor_board(tmp_path_factory, "corrbest")
    cands, facts, _m = _run_anneal(pcb, constraints=CORR_CON, decoupling={})
    best = cands[0]
    assert best["corridors"][0]["intrusion_mm2"] == 0.0
    assert best["terms"]["corridor_mm2"] == 0.0
    assert facts["corridor_unknown_refs"] == []
    no_con = {"placement": {"fixed": ["A1", "A2"]}}
    cands2, _f2, _m2 = _run_anneal(pcb, constraints=no_con, decoupling={})
    r1 = next(o for o in cands2[0]["ops"] if o["ref"] == "R1")
    assert 17.0 <= r1["y"] <= 23.0           # sits in the (undeclared) swath


def test_corridor_unknown_ref_surfaced(tmp_path_factory):
    pcb = _corridor_board(tmp_path_factory, "corrunk")
    con = {"placement": {"fixed": ["A1", "A2"],
                         "corridors": [{"a": "ZZ9", "b": "A2",
                                        "width_mm": 3.0}]}}
    eng, _m = _engine(pcb, con, {})
    assert eng.corridor_unknown_refs == ["ZZ9"]
    assert eng.corridors == []               # malformed entry never scores


# ============================================================ pure: feedback

def test_route_feedback_flag_wires_the_s11_probe(tmp_path_factory, monkeypatch):
    # S11 replaced the exit-2 stub: --route-feedback now builds a live probe
    # (place_edit snapshot -> route_auto.route_probe). Intercept the factory
    # so no toolchain runs; the flag must reach anneal() with a callable.
    pcb = _scatter_board(tmp_path_factory, "fbwire")
    seen = {}

    def fake_factory(board, *, passes, timeout_s):
        seen["args"] = (Path(board).name, passes, timeout_s)
        return lambda model: 1.0

    monkeypatch.setattr(place_anneal, "make_route_probe", fake_factory)
    rc = place_anneal.main(["--pcb", str(pcb), "--route-feedback",
                            "--probe-passes", "3", "--probe-timeout-s", "60",
                            "--max-epochs", "4", "--stall", "4",
                            "--moves-per-cluster", "8",
                            "--out-dir", str(pcb.parent / "anneal")])
    assert rc == 0
    assert seen["args"] == (pcb.name, 3, 60)


def test_route_probe_blends_into_scoring(tmp_path_factory):
    pcb = _scatter_board(tmp_path_factory, "fbprobe")
    calls = []

    def probe(model):
        calls.append(len(model.footprints))
        return 0.4

    candidates, facts, _m = _run_anneal(pcb, probe=probe, feedback_every=2,
                                        max_epochs=6, stall=6)
    assert calls, "probe was never invoked"
    assert facts["feedback_used"] is True
    assert facts["last_completion"] == pytest.approx(0.4)
    best = candidates[0]
    assert best["completion"] == pytest.approx(0.4)
    # blended score = cost * (1 + w_fb * (1 - completion))
    w_fb = place_anneal.DEFAULT_WEIGHTS["feedback"]
    # cost/score are rounded to 3 dp in the report - allow that much slack
    assert best["score"] == pytest.approx(best["cost"] * (1 + w_fb * 0.6),
                                          abs=2e-3)


def test_probe_boosts_cong_and_cross_weights(tmp_path_factory):
    pcb = _scatter_board(tmp_path_factory, "fbboost")
    eng, _m = _engine(pcb)
    params = Params(seed=1, **{**FAST, "max_epochs": 4}, feedback_every=1)
    ann = place_anneal.Annealer(eng, params, route_probe=lambda m: 0.25)
    ann.run()
    assert eng.fb_boost == pytest.approx(
        1.0 + place_anneal.FB_GAIN * 0.75)


# ============================================================ pure: CLI

def test_cli_report_and_ops_files(tmp_path_factory, tmp_path):
    pcb = _scatter_board(tmp_path_factory, "cli")
    (pcb.parent / "constraints.json").write_text(json.dumps(SCATTER_CON),
                                                 encoding="utf-8")
    (pcb.parent / "decoupling.json").write_text(json.dumps(SCATTER_DEC),
                                                encoding="utf-8")
    rep = tmp_path / "rep.json"
    rc = place_anneal.main([
        "--pcb", str(pcb), "--seed", "3", "--candidates", "2",
        "--moves-per-cluster", "25", "--max-epochs", "10", "--stall", "6",
        "--out-dir", str(tmp_path / "cands"), "--out-report", str(rep)])
    assert rc == 0
    r = json.loads(rep.read_text("utf-8"))
    assert r["script"] == "place_anneal" and r["status"] == "pass"
    for k in ("seed", "clusters", "hpwl_input_mm", "hpwl_best_mm",
              "improvement_pct", "epochs", "moves", "t0", "runtime_s",
              "candidates"):
        assert k in r, k
    for c in r["candidates"]:
        f = Path(c["ops_file"])
        assert f.is_file()
        doc = json.loads(f.read_text("utf-8"))
        assert doc["version"] == 1 and doc["ops"]
        assert "ops" not in c and "violations" not in c   # slim in report
        assert "n_violations" in c


def test_cli_missing_board_exits_2():
    assert place_anneal.main(["--pcb", "no/such/board.kicad_pcb"]) == 2


# ============================================================ U20: satellite
# slot inheritance - the annealer refines the placement it was handed instead
# of re-deriving every satellite slot from place_seed (LEARNINGS 2026-08-19
# [placement][anneal], ladder row 313).

BBADC_FIX = REPO / "tests" / "fixtures" / "bb_adc"
BBADC_UNLOCK = {"U1", "C2", "C3", "C8", "R7"}


def _bbadc_board(tmp_path):
    """The frozen bb-adc board with ONLY the converter cluster unlocked -
    everything else stays a locked obstacle. This is the hand-placed state
    the pre-U20 annealer silently degraded."""
    import re
    text = (BBADC_FIX / "bb-adc.kicad_pcb").read_text(encoding="utf-8")
    parts = text.split("(footprint ")
    out = [parts[0]]
    for seg in parts[1:]:
        m = re.search(r'\(property "Reference" "([^"]+)"', seg)
        if m and m.group(1) in BBADC_UNLOCK:
            seg = re.sub(r"\n\s*\(locked yes\)", "", seg)
        out.append(seg)
    pcb = tmp_path / "bb-adc.kicad_pcb"
    pcb.write_text("(footprint ".join(out), encoding="utf-8")
    con = json.loads((BBADC_FIX / "constraints.json").read_text("utf-8"))
    dec = json.loads((BBADC_FIX / "decoupling.json").read_text("utf-8"))
    return pcb, con, dec


def test_bbadc_seed_slots_violate_declared_limits(tmp_path):
    """The pre-U20 defect reproduced on the live fixture: place_seed-derived
    slots (what _build_bodies used to start from) put C2 at 2.44 mm against
    its declared 2.0 and C3 at 3.97 against 2.5."""
    import place_seed
    pcb, con, dec = _bbadc_board(tmp_path)
    model = placelib.PlaceModel(pcb)
    clusters, warns = placelib.build_clusters(model, dec,
                                              con.get("placement") or {})
    c = next(c for c in clusters if c.anchor == "U1")
    anchor = model.footprints["U1"]
    slots = place_seed.layout_satellites(model, c, warns)
    place_seed.apply_cluster(model, c, slots, anchor.center_abs(),
                             anchor.angle)
    viol = placelib.declared_decap_violations(model, dec)
    assert {v["refs"][0] for v in viol} == {"C2", "C3"}
    assert all(v["severity"] == "error" for v in viol)


def test_bbadc_hand_placement_survives_anneal(tmp_path):
    """U20 acceptance on the live fixture: the annealer INHERITS the
    hand-placed satellite geometry - no input/start hpwl gap, nothing
    re-slotted - and the best candidate keeps every satellite exactly where
    it was relative to its anchor, declared distances intact."""
    pcb, con, dec = _bbadc_board(tmp_path)
    cands, facts, _m = place_anneal.anneal(
        pcb, con, dec, Params(seed=1, candidates=2, moves_per_cluster=10,
                              max_epochs=4, stall=3))
    assert facts["satellites_reslotted"] == []
    assert facts["hpwl_start_mm"] == pytest.approx(facts["hpwl_input_mm"],
                                                   abs=0.01)
    best = cands[0]
    assert best["legal"]
    m0 = placelib.PlaceModel(pcb)
    m1 = placelib.PlaceModel(pcb)
    _fold_ops(m1, best["ops"])
    a0, a1 = m0.footprints["U1"], m1.footprints["U1"]
    for ref in ("C2", "C3", "C8", "R7"):
        b0, b1 = m0.footprints[ref], m1.footprints[ref]
        d0 = (b0.center_abs()[0] - a0.center_abs()[0],
              b0.center_abs()[1] - a0.center_abs()[1])
        d1 = (b1.center_abs()[0] - a1.center_abs()[0],
              b1.center_abs()[1] - a1.center_abs()[1])
        l0 = _rot(d0[0], d0[1], a0.angle)
        l1 = _rot(d1[0], d1[1], a1.angle)
        assert l1 == (pytest.approx(l0[0], abs=1e-3),
                      pytest.approx(l0[1], abs=1e-3))
        r0 = (b0.angle - a0.angle) % 360.0
        r1 = (b1.angle - a1.angle) % 360.0
        assert abs(((r1 - r0) + 180.0) % 360.0 - 180.0) < 1e-3
    assert placelib.declared_decap_violations(m1, dec) == []


DEC_C1U1 = {"associations": [
    {"cap": "C1", "ic": "U1", "pin": "4", "rail": "VCC", "value": "100nF"}]}


def _handplaced_board(tmp_path_factory, name, cap_at, cap_layer="F.Cu"):
    """U1 with a VCC pin at local (-2, 2); C1 hand-placed at cap_at."""
    body = _fp("U1", 30, 20, cy=(-3, -3, 3, 3),
               pads=_pad("1", -2, -2, "A") + _pad("4", -2, 2, "VCC"))
    body += _fp("R1", 6, 6, pads=_pad("1", -0.5, 0, "A")
                + _pad("2", 0.5, 0, "GND"))
    body += _fp("C1", cap_at[0], cap_at[1], layer=cap_layer,
                pads=_pad("1", -0.5, 0, "VCC") + _pad("2", 0.5, 0, "GND"))
    return _pcb(tmp_path_factory, name, body)


def _bodies_of(pcb, dec):
    model = placelib.PlaceModel(pcb)
    clusters, warns = placelib.build_clusters(model, dec, {})
    res: list[str] = []
    bodies = place_anneal._build_bodies(model, clusters, warns,
                                        decoupling=dec, reslotted=res)
    return model, bodies, warns, res


def test_hand_slot_inherited_when_valid(tmp_path_factory):
    pcb = _handplaced_board(tmp_path_factory, "handok", (26, 26))
    model, bodies, warns, res = _bodies_of(pcb, DEC_C1U1)
    assert res == [] and not [w for w in warns if "re-slotted" in w]
    b = next(b for b in bodies if b.cluster.anchor == "U1")
    slot, rel = b.slots["C1"]
    got = model.footprints["U1"].to_abs(slot)
    want = model.footprints["C1"].center_abs()
    assert got == (pytest.approx(want[0], abs=1e-6),
                   pytest.approx(want[1], abs=1e-6))
    assert rel == pytest.approx(0.0)


def test_invalid_slots_reslotted_loudly(tmp_path_factory):
    # colliding: C1 right on top of U1 -> re-derived, warned, reported
    pcb = _handplaced_board(tmp_path_factory, "handcol", (30, 20))
    model, bodies, warns, res = _bodies_of(pcb, DEC_C1U1)
    assert res == ["C1"]
    assert any("re-slotted" in w and "collides" in w for w in warns)
    b = next(b for b in bodies if b.cluster.anchor == "U1")
    got = model.footprints["U1"].to_abs(b.slots["C1"][0])
    want = model.footprints["C1"].center_abs()
    assert abs(got[0] - want[0]) + abs(got[1] - want[1]) > 1.0
    # wrong side: satellite on B.Cu under a front anchor -> re-derived
    pcb2 = _handplaced_board(tmp_path_factory, "handside", (26, 26),
                             cap_layer="B.Cu")
    _m2, _b2, warns2, res2 = _bodies_of(pcb2, DEC_C1U1)
    assert res2 == ["C1"]
    assert any("re-slotted" in w and "side" in w for w in warns2)


def test_candidate_rejected_on_declared_distance(tmp_path_factory):
    """Candidate REJECTION, not scoring: with a declared limit no slot can
    satisfy, the hand slot is re-derived (loudly) and every candidate is
    marked illegal by the declared violation - it can never rank as a clean
    win and --apply-best refuses it."""
    pcb = _handplaced_board(tmp_path_factory, "declfail", (26, 26))
    dec = {"associations": [{"cap": "C1", "ic": "U1", "pin": "4",
                             "rail": "VCC", "value": "100nF",
                             "max_dist_mm": 0.2}]}
    cands, facts, _m = _run_anneal(pcb, {"placement": {}}, dec)
    assert facts["satellites_reslotted"] == ["C1"]
    assert any("declared" in w for w in facts["warnings"])
    for c in cands:
        assert not c["legal"]
        assert any(v["kind"] == "decoupler_distance" and v.get("declared")
                   for v in c["violations"])


# ============================================================ smoke: corpus

@pytest.fixture(scope="session")
def cli() -> Path:
    c = env.find_kicad_cli()
    if c is None:
        pytest.skip("kicad-cli not installed")
    return c


@pytest.fixture(scope="session")
def seeded_board(cli, tmp_path_factory) -> Path:
    """usbbuck4 netlist -> board_init -> place_seed --apply (the S10 input)."""
    import board_init
    import kc
    import place_seed
    d = tmp_path_factory.mktemp("annealinit")
    net = d / "usbbuck4.net"
    kc.export_netlist(cli, GOLDEN / "usbbuck4" / "usbbuck4.kicad_sch", net)
    rc = board_init.main([
        "--netlist", str(net), "--name", "usbbuck4",
        "--out", str(d / "kicad"), "--layers", "4", "--mounting-holes", "4"])
    assert rc == 0
    pcb = d / "kicad" / "usbbuck4.kicad_pcb"
    for name in ("constraints.json", "decoupling.json"):
        shutil.copy2(GOLDEN / "usbbuck4" / name, pcb.parent / name)
    payload, _ = place_seed.run([
        "--pcb", str(pcb), "--ops-out", str(d / "seed_ops.json"), "--apply"])
    assert payload["status"] == "pass"
    return pcb


@pytest.mark.smoke
def test_model_mirror_matches_pcbnew_flip(cli, tmp_path):
    """The claim U19 rests on: placelib.Footprint.mirror reproduces exactly
    what pcbnew's Flip() writes to the file, so an absolute `place` op with
    `side` reproduces a flipped model state.

    LEARNINGS 2026-07-11 [geometry][kicad] recorded the format fact and
    flagged it corpus-unvalidated ("no FLIPPED footprints in the corpus").
    This validates it on every movable part of a real 4-layer board at once -
    rotated parts, THT parts and duplicate pad numbers included."""
    import place_edit
    pcb = tmp_path / "usbbuck4.kicad_pcb"
    shutil.copy2(GOLDEN / "usbbuck4" / "usbbuck4.kicad_pcb", pcb)
    (tmp_path / "usbbuck4.kicad_pro").write_text('{"meta": {"filename": "x"}}',
                                                 encoding="utf-8")
    want = placelib.PlaceModel(pcb)
    refs = sorted(f.ref for f in want.movable())
    assert refs, "fixture has no movable footprints"
    ops = []
    for ref in refs:
        fp = want.footprints[ref]
        fp.mirror()
        fp.angle = (-fp.angle) % 360.0          # KiCad negates it on flip
        ops.append({"op": "place", "ref": ref, "x": fp.pos[0], "y": fp.pos[1],
                    "deg": fp.angle, "side": fp.side})
    place_edit.apply_ops(pcb, ops)

    got = placelib.PlaceModel(pcb)
    for ref in refs:
        a, b = want.footprints[ref], got.footprints[ref]
        assert b.side == a.side == "back", ref
        assert sorted((p.number, p.net) for p in b.pads) == \
            sorted((p.number, p.net) for p in a.pads), ref
        assert sorted(p.local for p in b.pads) == pytest.approx(
            sorted(p.local for p in a.pads), abs=1e-4), ref
        assert b.extents_abs().bounds == pytest.approx(
            a.extents_abs().bounds, abs=1e-3), ref
        assert sorted((x, y) for _n, _t, x, y in b.pad_centers_abs()) == \
            pytest.approx(sorted((x, y) for _n, _t, x, y
                                 in a.pad_centers_abs()), abs=1e-3), ref


@pytest.mark.smoke
def test_flip_candidate_round_trips_through_place_edit(cli, tmp_path_factory,
                                                       tmp_path):
    """End to end: the annealer picks the back side, place_edit's SWIG worker
    applies it, and the saved board matches the model - part on B.Cu with its
    satellite, legality clean, no DRC regression."""
    import kc
    import place_edit
    pcb = _hub_board(tmp_path_factory, "hubswig", sat=True)
    drc_before = kc.run_drc(cli, pcb)
    cands, _f, _m = _run_anneal(pcb, HUB_SAT_CON, {}, max_epochs=25, stall=10)
    best = cands[0]
    assert best["legal"] and best["sides"]["back"] == 2

    want = placelib.PlaceModel(pcb)
    _fold_ops(want, best["ops"])
    place_edit.apply_ops(pcb, best["ops"])
    got = placelib.PlaceModel(pcb)
    for ref in ("R1", "C1"):
        assert got.footprints[ref].side == "back"
        assert got.footprints[ref].extents_abs().bounds == pytest.approx(
            want.footprints[ref].extents_abs().bounds, abs=1e-3), ref
    assert not [v for v in placelib.legality_violations(
        got, HUB_SAT_CON["placement"]) if v["severity"] == "error"]
    drc_after = kc.run_drc(cli, pcb)
    assert len(drc_after["violations"]) <= len(drc_before["violations"])


@pytest.mark.smoke
def test_anneal_acceptance_usbbuck4(seeded_board, tmp_path):
    """Plan S10 acceptance: >=20% HPWL under the seed, legal, bounded runtime.

    60 epochs is the smoke budget (26% at 50 epochs when built); the default
    140-epoch budget reached 43.4% in ~2 min (PROGRESS S10)."""
    import place_edit
    import place_metrics
    constraints = json.loads(
        (GOLDEN / "usbbuck4" / "constraints.json").read_text("utf-8"))
    decoupling = json.loads(
        (GOLDEN / "usbbuck4" / "decoupling.json").read_text("utf-8"))
    seed_hpwl = placelib.hpwl(placelib.PlaceModel(seeded_board))["total_mm"]
    params = Params(seed=1, max_epochs=60)
    candidates, facts, _m = place_anneal.anneal(
        seeded_board, constraints, decoupling, params)
    best = candidates[0]
    assert best["legal"], best["violations"]
    improvement = 100.0 * (seed_hpwl - best["hpwl_mm"]) / seed_hpwl
    assert improvement >= 20.0, (seed_hpwl, best["hpwl_mm"])
    assert facts["runtime_s"] < 1800.0     # plan bar: <30 min

    # apply the winner and hold it to the P6 gate
    place_edit.apply_ops(seeded_board, best["ops"])
    payload, _ = place_metrics.run(["--pcb", str(seeded_board)])
    assert payload["status"] == "pass", payload["violations"]
    assert payload["metrics"]["hpwl"]["total_mm"] == pytest.approx(
        best["hpwl_mm"], abs=0.05)
    for f in payload["metrics"]["decoupling"]:
        assert f["manhattan_mm"] < 10.0, f


@pytest.mark.smoke
def test_anneal_reproducible_on_corpus(seeded_board):
    outs = []
    constraints = json.loads(
        (GOLDEN / "usbbuck4" / "constraints.json").read_text("utf-8"))
    decoupling = json.loads(
        (GOLDEN / "usbbuck4" / "decoupling.json").read_text("utf-8"))
    for _ in range(2):
        c, _f, _m = place_anneal.anneal(
            seeded_board, constraints, decoupling,
            Params(seed=11, max_epochs=8, stall=8))
        outs.append(json.dumps([x["ops"] for x in c]))
    assert outs[0] == outs[1]


def test_spot_legal_holds_edge_overhang_to_the_seed():
    """_spot_legal agrees with the SA cost and placelib: an edge cluster may
    keep the seed's designed overhang (J1's barrel off rf-term's top edge)
    but not grow it."""
    import place_seed
    fix = REPO / "tests" / "fixtures" / "rf_term_edge"
    con = json.loads((fix / "constraints.json").read_text("utf-8"))
    _o, _v, _f, model = place_seed.seed(fix / "rf_term_edge.kicad_pcb", con,
                                        {}, 1.0, 0.8)
    placement = con["placement"]
    clusters, warns = placelib.build_clusters(model, {}, placement)
    eng = Engine(model, place_anneal._build_bodies(model, clusters, warns),
                 con, {})
    cid = next(b.cid for b in eng.bodies if b.cluster.anchor == "J1")
    assert eng.out_base[cid] > 30.0          # the barrel's designed overhang
    x, y = eng.centers[cid]
    # other bodies are ignored here: only the outline term is under test
    from shapely.geometry import Polygon
    for b in eng.bodies:
        if b.cid != cid:
            eng.polys[b.cid] = Polygon()
    eng.obstacles.clear()
    assert place_anneal._spot_legal(eng, cid, (x, y), eng.angles[cid])
    assert place_anneal._spot_legal(eng, cid, (x, y + 1.0), eng.angles[cid])
    assert not place_anneal._spot_legal(eng, cid, (x, y - 1.0),
                                        eng.angles[cid])
