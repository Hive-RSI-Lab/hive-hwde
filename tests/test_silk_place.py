"""T6 p1: silk_place - the refdes silk solver (LEARNINGS recipe promoted to L3).

Pure tests exercise the solver's own geometric model on synthetic boards
(candidate generation, collision scoring, crowded-first, skips, residuals,
determinism, ops schema). The smoke test applies the ops to a copy of the
blinky2 golden and holds the REAL DRC to zero silk findings - check_silk is
lenient and never the oracle (LEARNINGS 2026-07-27).
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / ".claude" / "skills" / "hwde" / "scripts"
GOLDEN = REPO / "tests" / "golden"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
import env  # noqa: E402
import placelib  # noqa: E402
import place_edit  # noqa: E402
import silk_place  # noqa: E402
from checklib import CheckError  # noqa: E402


# ---- synthetic boards ------------------------------------------------------

def _fp(ref, x, y, pads="", cy=(-0.8, -0.5, 0.8, 0.5), refat="(at 0 -4 0)",
        attr="smd", hide=""):
    court = (f'    (fp_rect (start {cy[0]} {cy[1]}) (end {cy[2]} {cy[3]})'
             f' (stroke (width 0.05)) (fill no) (layer "F.CrtYd"))\n')
    return (f'  (footprint "t:{ref}" (layer "F.Cu")\n    (at {x} {y})\n'
            f'    (property "Reference" "{ref}" {refat} (layer "F.SilkS") '
            f'{hide}(effects (font (size 1 1) (thickness 0.15))))\n'
            f'    (attr {attr})\n{court}{pads})\n')


def _pad(num, x, y, net, w=0.9, h=0.95):
    return (f'    (pad "{num}" smd rect (at {x} {y}) (size {w} {h})'
            f' (layers "F.Cu") (net "{net}"))\n')


def _board(tmp_path_factory, name, body, w=30.0, h=20.0):
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


def _crowded(tmp_path_factory, name="crowd"):
    """Three parts with library-style (0, -4) refdes offsets, packed so the
    naive offsets land labels on neighbours."""
    body = _fp("C1", 10, 10, _pad("1", -0.77, 0, "A") + _pad("2", 0.77, 0, "G"))
    body += _fp("C2", 10, 12.4, _pad("1", -0.77, 0, "B") + _pad("2", 0.77, 0, "G"))
    body += _fp("R1", 13, 10, _pad("1", -0.77, 0, "A") + _pad("2", 0.77, 0, "B"))
    body += _fp("H1", 3, 3, attr="board_only", refat="(at 0 -2 0)")
    body += _fp("D9", 25, 10, _pad("1", 0, 0, "A"), hide="(hide yes) ")
    return _board(tmp_path_factory, name, body)


# ============================================================ pure: solver

def test_solver_pulls_labels_in_and_clears_collisions(tmp_path_factory):
    pcb = _crowded(tmp_path_factory, "pull")
    payload, _ = silk_place.run(["--pcb", str(pcb)])
    assert payload["status"] == "pass"          # no residuals
    assert payload["targets"] == 3 and payload["moved"] == 3
    assert payload["median_beyond_extent_mm"] \
        < payload["median_beyond_extent_mm_before"]
    assert payload["median_beyond_extent_mm"] <= 1.5
    # verify with the solver's own box model: no label-label / label-pad hits
    ops = json.loads(Path(payload["ops_out"]).read_text("utf-8"))["ops"]
    place_edit.validate_ops({"version": 1, "ops": ops})
    model = placelib.PlaceModel(pcb)
    boxes = {o["ref"]: placelib.text_box(o["ref"], 1.0, 0.15,
                                         o["x"], o["y"], o.get("deg", 0.0))
             for o in ops}
    pads = [pp for ref in model.footprints
            for pp in silk_place._pad_polys_abs(model.footprints[ref])]
    refs = sorted(boxes)
    for i, a in enumerate(refs):
        for b in refs[i + 1:]:
            assert not boxes[a].intersects(boxes[b]), (a, b)
        for pp in pads:
            assert not boxes[a].intersects(pp), a


def test_solver_skips_hidden_and_board_only(tmp_path_factory):
    pcb = _crowded(tmp_path_factory, "skips")
    payload, _ = silk_place.run(["--pcb", str(pcb)])
    reasons = {s["ref"]: s["reason"] for s in payload["skipped"]}
    assert reasons["D9"] == "hidden"
    assert reasons["H1"] == "board_only"
    ops = json.loads(Path(payload["ops_out"]).read_text("utf-8"))["ops"]
    assert not any(o["ref"] in ("D9", "H1") for o in ops)


def test_solver_deterministic(tmp_path_factory):
    pcb = _crowded(tmp_path_factory, "det")
    outs = []
    for i in (1, 2):
        out = pcb.parent / f"ops{i}.json"
        silk_place.run(["--pcb", str(pcb), "--ops-out", str(out)])
        outs.append(out.read_text("utf-8"))
    assert outs[0] == outs[1]


def test_residual_reported_not_forced(tmp_path_factory):
    """A part walled in by oversized neighbour pads on all four sides within
    the push range must land in residual (exit 1), never overlap."""
    big = _pad("1", 0, 0, "X", w=6.0, h=6.0)
    body = _fp("C1", 10, 10, _pad("1", -0.77, 0, "A") + _pad("2", 0.77, 0, "G"))
    body += _fp("W1", 10, 5.2, big)
    body += _fp("W2", 10, 14.8, big)
    body += _fp("W3", 5.2, 10, big)
    body += _fp("W4", 14.8, 10, big)
    pcb = _board(tmp_path_factory, "walled", body)
    payload, _ = silk_place.run(["--pcb", str(pcb), "--refs", "C1"])
    assert payload["status"] == "violations"
    assert [r["ref"] for r in payload["residual"]] == ["C1"]
    kinds = {v["kind"] for v in payload["violations"]}
    assert kinds == {"silk_residual"}


# ===================================== the solver and check_silk agree

def _apply_ops_text(pcb: Path, ops: list[dict]) -> Path:
    """Write the move_text ops into a copy of the board as s-expr edits (no
    SWIG), so check_silk can judge exactly what the solver placed. The
    synthetic footprints are unrotated, so local = absolute - footprint."""
    import sexpdata
    tree = sexpdata.loads(pcb.read_text("utf-8"))
    sym = sexpdata.Symbol
    by_ref = {o["ref"]: o for o in ops}

    def kids(n, name):
        return [c for c in n if isinstance(c, list) and c and c[0] == sym(name)]
    for fp in kids(tree, "footprint"):
        fat = kids(fp, "at")[0]
        assert len(fat) < 4 or float(fat[3]) == 0.0
        for prop in kids(fp, "property"):
            o = by_ref.get(prop[2]) if prop[1] == "Reference" else None
            if o is None:
                continue
            for i, c in enumerate(prop):
                if isinstance(c, list) and c and c[0] == sym("at"):
                    prop[i] = [sym("at"), o["x"] - float(fat[1]),
                               o["y"] - float(fat[2]), o["deg"]]
    out = pcb.with_name(pcb.stem + "_applied.kicad_pcb")
    out.write_text(sexpdata.dumps(tree), encoding="utf-8")
    return out


def _check_silk_findings(pcb: Path) -> list[tuple[str, str]]:
    import check_silk
    payload, _ = check_silk.run(["--pcb", str(pcb)])
    return [(v["kind"], v.get("ref")) for v in payload["violations"]
            if v["kind"] in ("silk_misattributed", "silk_over_pad")]


def _passive(ref, x, y, vertical=False):
    if vertical:
        return _fp(ref, x, y, _pad("1", 0, -0.77, "A") + _pad("2", 0, 0.77, "B"),
                   cy=(-0.5, -0.8, 0.5, 0.8))
    return _fp(ref, x, y, _pad("1", -0.77, 0, "A") + _pad("2", 0.77, 0, "B"))


# a 3x3 passive cluster where the clearance-first score of the old solver
# parked C5 and R8 on neighbours (check_silk: silk_misattributed x2)
_CLUSTER = [("C1", 10.0, 8.0, False), ("R2", 10.0, 10.2, False),
            ("C3", 10.0, 13.2, False), ("R4", 13.4, 8.0, True),
            ("C5", 13.0, 10.0, False), ("R6", 13.4, 12.4, False),
            ("C7", 16.8, 8.0, False), ("R8", 15.2, 11.0, True),
            ("C9", 16.8, 14.0, False)]


def test_crowded_result_passes_check_silk(tmp_path_factory):
    """Every label the solver places must pass check_silk: it judges each
    candidate with check_silk's own attribution and over-pad functions."""
    pcb = _board(tmp_path_factory, "agree",
                 "".join(_passive(*p) for p in _CLUSTER))
    assert _check_silk_findings(pcb)          # the (0,-4) offsets misread
    payload, _ = silk_place.run(["--pcb", str(pcb)])
    assert payload["status"] == "pass", payload["residual"]
    assert payload["unplaceable"] == []
    ops = json.loads(Path(payload["ops_out"]).read_text("utf-8"))["ops"]
    applied = _apply_ops_text(pcb, ops)
    assert _check_silk_findings(applied) == []


def test_settled_label_is_not_moved(tmp_path_factory):
    """The label's current spot is itself a candidate (first, so it wins
    ties): a label the solver already placed is not churned."""
    pcb = _board(tmp_path_factory, "settled", _passive("R1", 10, 10))
    first, _ = silk_place.run(["--pcb", str(pcb)])
    assert first["moved"] == 1
    ops = json.loads(Path(first["ops_out"]).read_text("utf-8"))["ops"]
    again, _ = silk_place.run(["--pcb", str(_apply_ops_text(pcb, ops))])
    assert again["moved"] == 0 and again["status"] == "pass"


def test_unplaceable_when_every_free_spot_fails_check_silk(tmp_path_factory):
    """C1's own silk body marking (a solid 5 x 5 mm block around its pads)
    pushes its label > 1 mm from its pads, and the only collision-free
    channel around it hugs four neighbours' pads: every free spot is
    silk_misattributed, so the solver reports C1 unplaceable and leaves it
    alone rather than keeping a failing spot."""
    def block(x0, y0, x1, y1):
        return (f'    (fp_poly (pts (xy {x0} {y0}) (xy {x1} {y0}) (xy {x1} {y1})'
                f' (xy {x0} {y1})) (stroke (width 0.12)) (fill yes)'
                f' (layer "F.SilkS"))\n')
    marking = (block(-2.5, -2.5, 2.5, -0.7) + block(-2.5, 0.7, 2.5, 2.5)
               + block(-2.5, -0.7, -1.4, 0.7) + block(1.4, -0.7, 2.5, 0.7))
    big = _pad("1", 0, 0, "X", w=6.0, h=6.0)
    body = _fp("C1", 10, 10, _pad("1", -0.77, 0, "A") + _pad("2", 0.77, 0, "G")
               + marking)
    body += _fp("W1", 10, 3.0, big) + _fp("W2", 10, 17.0, big)
    body += _fp("W3", 3.0, 10, big) + _fp("W4", 17.0, 10, big)
    pcb = _board(tmp_path_factory, "noroom", body)
    payload, _ = silk_place.run(["--pcb", str(pcb), "--refs", "C1"])
    assert payload["status"] == "violations"
    assert payload["unplaceable"] == ["C1"]
    res = payload["residual"][0]
    assert "passes check_silk" in res["reason"]       # free spots existed
    assert "silk_misattributed" in res["reason"]
    assert "hide" in res["suggest"] and "fab" in res["suggest"]
    # ready place_edit ops: shrink to the 0.8 mm floor first, hide last
    fx = res["fix_ops"]
    assert fx["hide"] == {"op": "set_text", "ref": "C1",
                          "field": "reference", "hide": True}
    assert fx["shrink"]["size"] == 0.8
    import place_edit
    place_edit.validate_ops({"version": 1, "ops": list(fx.values())})
    [v] = payload["violations"]
    assert v["kind"] == "silk_residual" and "unplaceable" in v["msg"]
    assert v["fix_ops"] == fx
    assert payload["moved"] == 0


def test_fix_ops_skip_shrink_at_the_floor():
    """A label already at the 0.8 mm floor gets only the hide op - shrinking
    it further would trade misattribution for silk_illegible."""
    at_floor = {"size": 0.8, "size_y": 0.8, "thickness": 0.15}
    assert set(silk_place._fix_ops("R1", at_floor)) == {"hide"}
    big = {"size": 1.0, "size_y": 1.0, "thickness": 0.15}
    assert silk_place._fix_ops("R1", big)["shrink"] == {
        "op": "set_text", "ref": "R1", "field": "reference", "size": 0.8}


def test_rule_verdict_rejects_label_over_a_pad(tmp_path_factory):
    """The mask rule is check_silk.over_pad itself: a label centred on a
    pad fails with silk_over_pad."""
    import check_silk
    import geom
    pcb = _board(tmp_path_factory, "overpad", _passive("R1", 10, 10))
    bg = geom.load_board(pcb)
    pads = [p for p in bg.pads_of() if "F" in check_silk.pad_side(p)]
    side = (pads, silk_place.STRtree([p.poly for p in pads]))
    info = {"size": 1.0, "size_y": 1.0, "thickness": 0.15}
    fail, _, _ = silk_place.rule_verdict(
        "R1", info, 10.77, 10.0, 0.0, side, check_silk.pad_extent_boxes(bg))
    assert fail is not None and "silk_over_pad" in fail
    ok, _, att = silk_place.rule_verdict(
        "R1", info, 10.0, 8.6, 0.0, side, check_silk.pad_extent_boxes(bg))
    assert ok is None and att is not None and not att[3]


def test_mask_margin_grows_the_pad_keepout(tmp_path_factory):
    """KiCad clips silk against the pad's MASK aperture, so the board's
    pad_to_mask_clearance widens every pad obstacle."""
    body = "".join(_passive(*p) for p in _CLUSTER)
    pcb = _board(tmp_path_factory, "mask", body)
    text = pcb.read_text("utf-8").replace(
        "(setup)", "(setup (pad_to_mask_clearance 0.2))")
    pcb.write_text(text, encoding="utf-8")
    _, _, margins = silk_place.parse_board(pcb)
    assert margins["C1"] == pytest.approx(0.2)
    payload, _ = silk_place.run(["--pcb", str(pcb)])
    ops = json.loads(Path(payload["ops_out"]).read_text("utf-8"))["ops"]
    model = placelib.PlaceModel(pcb)
    pads = [pp for ref in model.footprints
            for pp in silk_place._pad_polys_abs(model.footprints[ref])]
    for o in ops:
        b = placelib.text_box(o["ref"], 1.0, 0.15, o["x"], o["y"], o["deg"])
        assert min(b.distance(pp) for pp in pads) > 0.2, o["ref"]


def test_pending_label_blocks_earlier_targets(tmp_path_factory):
    """A1 is solved first; B1's CURRENT label sits on A1's preferred spot
    (its normaliser target above A1). Until B1 is decided that label is an
    obstacle, so A1 must not land on it."""
    body = _fp("A1", 10, 10, _pad("1", -0.77, 0, "A") + _pad("2", 0.77, 0, "B"))
    # A1's normaliser target is (10, 10 - 0.475 - 0.25 - 0.575) = (10, 8.7)
    body += _fp("B1", 20, 10, _pad("1", -0.77, 0, "A") + _pad("2", 0.77, 0, "B"),
                refat="(at -10 -1.3 0)")
    pcb = _board(tmp_path_factory, "pending", body)
    payload, _ = silk_place.run(["--pcb", str(pcb)])
    ops = {o["ref"]: o for o in
           json.loads(Path(payload["ops_out"]).read_text("utf-8"))["ops"]}
    assert payload["status"] == "pass"
    b1_now = placelib.text_box("B1", 1.0, 0.15, 10.0, 8.7, 0.0)
    a1 = ops.get("A1", {"x": 10.0, "y": 6.0, "deg": 0.0})   # unmoved: (0,-4)
    assert not placelib.text_box("A1", 1.0, 0.15, a1["x"], a1["y"],
                                 a1["deg"]).intersects(b1_now)
    assert "B1" in ops                       # and B1 still went home


def test_padless_footprint_label_is_placed(tmp_path_factory):
    """No pads -> no attribution rule (check_silk skips it); the solver
    still places it by closeness alone."""
    pcb = _board(tmp_path_factory, "padless", _fp("G1", 10, 10))
    payload, _ = silk_place.run(["--pcb", str(pcb)])
    assert payload["status"] == "pass"
    assert [r["ref"] for r in payload["results"]] == ["G1"]


def test_min_silk_clearance_read_from_pro(tmp_path_factory):
    pcb = _crowded(tmp_path_factory, "pro")
    assert silk_place.read_min_silk_clearance(pcb) == 0.0   # no sibling
    pcb.with_suffix(".kicad_pro").write_text(json.dumps(
        {"board": {"design_settings": {"rules": {"min_silk_clearance": 0.3}}}}),
        encoding="utf-8")
    assert silk_place.read_min_silk_clearance(pcb) == 0.3
    # the live value binds: candidates must clear obstacles by > 0.3
    payload, _ = silk_place.run(["--pcb", str(pcb)])
    for r in payload["results"]:
        assert r["clearance_mm"] > 0.3


def test_verify_drc_requires_apply(tmp_path_factory):
    pcb = _crowded(tmp_path_factory, "vguard")
    with pytest.raises(CheckError, match="--apply"):
        silk_place.run(["--pcb", str(pcb), "--verify-drc"])


def test_text_box_metrics():
    """Measured constants (LEARNINGS 2026-07-30): 3-char refdes at size 1.0 /
    t 0.15 inks ~2.64-2.69 x 1.16 mm; 90-deg text swaps the box axes."""
    b = placelib.text_box("R34", 1.0, 0.15, 0.0, 0.0, 0.0)
    x0, y0, x1, y1 = b.bounds
    assert 2.5 <= x1 - x0 <= 2.8
    assert y1 - y0 == pytest.approx(1.15)
    b90 = placelib.text_box("R34", 1.0, 0.15, 0.0, 0.0, 90.0)
    x0, y0, x1, y1 = b90.bounds
    assert y1 - y0 == pytest.approx(2.685, abs=0.2)
    assert x1 - x0 == pytest.approx(1.15, abs=1e-6)


# ============================================================ smoke: real DRC

@pytest.fixture(scope="session")
def cli():
    c = env.find_kicad_cli()
    if c is None:
        pytest.skip("kicad-cli not installed")
    return c


@pytest.mark.smoke
def test_blinky2_golden_apply_stays_drc_silk_clean(cli, tmp_path):
    """Acceptance (p1 verify plan): run the solver on a copy of the blinky2
    golden, apply, and hold the REAL DRC to zero silk findings."""
    import kc
    d = tmp_path / "b2"
    d.mkdir()
    for ext in (".kicad_pcb", ".kicad_pro", ".kicad_prl"):
        src = GOLDEN / "blinky2" / f"blinky2{ext}"
        if src.is_file():
            shutil.copy2(src, d / src.name)
    pcb = d / "blinky2.kicad_pcb"
    payload, _ = silk_place.run(["--pcb", str(pcb), "--apply", "--verify-drc"])
    assert payload["drc_silk_total"] == 0, payload["violations"]
    assert payload["residual"] == []
    assert payload["median_beyond_extent_mm"] is None \
        or payload["median_beyond_extent_mm"] <= 1.5
