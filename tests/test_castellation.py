"""Castellated edge pads end to end: footprint, checks, DFM, quote, order, report.

The fixture tests/fixtures/castellated/castellated_edge.kicad_pcb is a 30 x 20
mm board with two rows of six castellated pads from castellated_fp.py (J1 on
the top edge, J2 on the bottom edge turned 180 deg); make_fixture.py rebuilds
it. Each mutant below is a text edit of that fixture into tmp_path, so every
case stands on its own board. rp2040-mini is read from the boards repo only
(skipped when absent) and must count 0 castellated pads today.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / ".claude" / "skills" / "hwde" / "scripts"
import sys  # noqa: E402

sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import castellated_fp  # noqa: E402
import castellation  # noqa: E402
import geom  # noqa: E402
import order_quote  # noqa: E402
import order_submit  # noqa: E402
import report_gen  # noqa: E402
from _boards import real_board  # noqa: E402

FIX = REPO / "tests" / "fixtures" / "castellated" / "castellated_edge.kicad_pcb"
PLAIN = REPO / "tests" / "fixtures" / "rf_term_edge" / "rf_term_edge.kicad_pcb"
RULES = castellation.load_rules()


def _mutant(tmp_path: Path, edit) -> Path:
    out = tmp_path / "board.kicad_pcb"
    out.write_text(edit(FIX.read_text(encoding="utf-8")), encoding="utf-8")
    return out


def _kinds(pcb: Path) -> dict[str, int]:
    vios, _ = castellation.check(geom.load_board(pcb), RULES, 1.6)
    out: dict[str, int] = {}
    for v in vios:
        out[v["kind"]] = out.get(v["kind"], 0) + 1
    return out


def _move(text: str, ref: str, new_at: str) -> str:
    """Move footprint `ref` to `new_at` (the footprint's own (at ...))."""
    blocks = text.split("\n\t(footprint ")
    for i, b in enumerate(blocks):
        if f'"Reference" "{ref}"' in b:
            blocks[i] = re.sub(r"\(at [^)]*\)", f"(at {new_at})", b, count=1)
    return "\n\t(footprint ".join(blocks)


# ------------------------------------------------------------- footprint

def test_generator_marks_every_pad_castellated(tmp_path):
    out = tmp_path / "Cast.pretty" / "C6.kicad_mod"
    rep = castellated_fp.run(6, 2.54, out)
    text = out.read_text(encoding="utf-8")
    assert rep["status"] == "pass" and rep["drill_mm"] == RULES["rec_drill_mm"]
    assert text.count("(pad ") == 6
    assert text.count("(property pad_prop_castellated)") == 6
    # a board feature: nothing to buy or place
    assert "exclude_from_bom" in text and "exclude_from_pos_files" in text
    # KiCad puts the hole at the pad's (at) and moves the copper by the
    # drill offset: holes on y = 0, copper carried 0.275 mm inboard
    assert "(at -6.35 0)" in text and "(offset 0 0.275)" in text
    assert "(at -6.35 0.275)" not in text


def test_geom_puts_the_hole_at_the_pad_and_offsets_the_copper():
    # pcbnew (SWIG, KiCad 10.0.6) on this fixture: J1.1 hole (8.65, 0),
    # copper centre (8.65, 0.275); J2 is turned 180, so its copper sits at
    # y = 20 - 0.275 with the hole on y = 20
    pads = {f"{p.ref}.{p.number}": p for p in geom.load_board(FIX).pads_of()}
    for name, hole, copper in (("J1.1", (8.65, 0.0), (8.65, 0.275)),
                               ("J2.1", (21.35, 20.0), (21.35, 19.725))):
        p = pads[name]
        h, c = p.drill_poly.centroid, p.poly.centroid
        assert (h.x, h.y) == pytest.approx(hole, abs=1e-6)
        assert (c.x, c.y) == pytest.approx(copper, abs=1e-6)
        assert p.center == pytest.approx(hole, abs=1e-6)
        assert p.copper_center == pytest.approx(copper, abs=1e-6)


@pytest.mark.parametrize("kw, words", [
    ({"pitch": 1.2}, "hole edge-to-edge"),
    ({"drill": 0.5}, "drill 0.5 mm < JLC minimum 0.6"),
    ({"ring": 0.1}, "ring 0.1 mm < JLC minimum 0.18"),
    ({"extension": 0.3}, "extension 0.3 mm < JLC minimum 0.5"),
])
def test_generator_refuses_below_jlc_minimums(tmp_path, kw, words):
    out = tmp_path / "x.kicad_mod"
    args = {"pitch": 2.54, **kw}
    pitch = args.pop("pitch")
    with pytest.raises(ValueError, match=re.escape(words)):
        castellated_fp.run(4, pitch, out, **args)
    assert not out.exists()
    # the same row at JLC's own minimums is accepted
    castellated_fp.run(4, 1.2 if "pitch" not in kw else 1.6, out,
                       drill=0.6, ring=0.18, extension=0.5)
    assert out.exists()


# ---------------------------------------------------------------- checks

def test_fixture_counts_and_passes():
    assert castellation.count(FIX) == 12
    vios, facts = castellation.check(geom.load_board(FIX), RULES, 1.6)
    assert vios == []
    assert facts == {"castellated_pads": 12, "castellated_refs": ["J1", "J2"]}


def test_board_without_castellation_counts_zero():
    vios, facts = castellation.check(geom.load_board(PLAIN), RULES, 1.6)
    assert facts["castellated_pads"] == 0 and vios == []


def test_unmarked_half_hole_is_an_error(tmp_path):
    pcb = _mutant(tmp_path, lambda t: t.replace(
        " (property pad_prop_castellated)", "").replace(
        "(property pad_prop_castellated)", ""))
    assert castellation.count(pcb) == 0
    assert _kinds(pcb) == {"castellated_unmarked": 12}


def test_castellated_pad_off_the_outline(tmp_path):
    # J1 moved 2 mm inside the board; J2 stays on its edge and stays clean
    pcb = _mutant(tmp_path, lambda t: _move(t, "J1", "15 2"))
    vios, _ = castellation.check(geom.load_board(pcb), RULES, 1.6)
    assert {v["kind"] for v in vios} == {"castellated_off_outline"}
    assert {v["refs"][0] for v in vios} == {"J1"} and len(vios) == 6


def test_old_offset_convention_puts_the_hole_inboard(tmp_path):
    # J1's pads written the way the generator used to: copper centred on the
    # edge by (at), hole moved by the offset. KiCad drills at (at), 0.275 mm
    # inside the board, so every J1 hole is off the outline; J2 stays clean.
    def old(t):
        blocks = t.split("\n\t(footprint ")
        for i, b in enumerate(blocks):
            if '"Reference" "J1"' in b:
                b = re.sub(r'(\(pad "\d+" thru_hole rect\s+)\(at (-?[\d.]+) 0\)',
                           r"\1(at \2 0.275)", b)
                blocks[i] = b.replace("(offset 0 0.275)", "(offset 0 -0.275)")
        return "\n\t(footprint ".join(blocks)

    pcb = _mutant(tmp_path, old)
    vios, _ = castellation.check(geom.load_board(pcb), RULES, 1.6)
    assert {v["kind"] for v in vios} == {"castellated_off_outline"}
    assert {v["pad"] for v in vios} == {f"J1.{n}" for n in range(1, 7)}
    assert all(v["distance_mm"] == pytest.approx(0.275, abs=1e-3)
               for v in vios)


def test_castellated_hole_too_near_a_corner(tmp_path):
    # J1's row starts 0.15 mm from x = 0: the end hole is inside the corner
    # and the next edge; J2 untouched
    pcb = _mutant(tmp_path, lambda t: _move(t, "J1", "6.5 0"))
    vios, _ = castellation.check(geom.load_board(pcb), RULES, 1.6)
    near = {v["kind"] for v in vios if v["pad"] == "J1.1"}
    assert near == {"castellated_to_corner", "castellated_to_other_edge"}
    assert not [v for v in vios if v["refs"] == ["J2"]]


def _rounded(text: str, r: float, w: float = 30.0, h: float = 20.0) -> str:
    """The fixture's w x h outline redrawn with corners rounded to radius r:
    four gr_lines stopping r short of each corner and four gr_arcs."""
    text = re.sub(r"\t\(gr_line\n(?:\t\t.*\n)*?\t\)\n", "", text)
    k = r * (1 - 1 / 2 ** 0.5)  # arc midpoint inset from the sharp corner

    def line(a, b):
        return (f"\t(gr_line (start {a[0]} {a[1]}) (end {b[0]} {b[1]}) "
                "(stroke (width 0.05) (type default)) (layer \"Edge.Cuts\"))\n")

    def arc(a, m, b):
        return (f"\t(gr_arc (start {a[0]} {a[1]}) (mid {m[0]} {m[1]}) "
                f"(end {b[0]} {b[1]}) (stroke (width 0.05) (type default)) "
                "(layer \"Edge.Cuts\"))\n")

    edge = (line((r, 0), (w - r, 0)) + line((w, r), (w, h - r))
            + line((w - r, h), (r, h)) + line((0, h - r), (0, r))
            + arc((w - r, 0), (w - k, k), (w, r))
            + arc((w, h - r), (w - k, h - k), (w - r, h))
            + arc((r, h), (k, h - k), (0, h - r))
            + arc((0, r), (k, k), (r, 0)))
    head, tail = text.rsplit("\n\t(embedded_fonts", 1)  # the board's own
    return head + "\n" + edge + "\t(embedded_fonts" + tail


def test_rounded_corners_are_corners(tmp_path):
    # PCB-0019-A's case: 1 mm corner radius, end hole 1.37 mm from the short
    # edge. A sampled arc never turns CORNER_DEG at one vertex, so before the
    # fix the board had no corner and no "other edge" and this passed.
    pcb = _mutant(tmp_path, lambda t: _move(_rounded(t, 1.0), "J1", "7.72 0"))
    bg = geom.load_board(pcb)
    runs, corners, arcs = castellation._runs(bg.outline)
    assert len(runs) == 4 and len(corners) == 4 and len(arcs) == 4
    # a rounded corner counts from the sharp corner its edges would meet at
    assert sorted((round(k.x, 6), round(k.y, 6)) for k in corners) == [
        (0, 0), (0, 20), (30, 0), (30, 20)]
    vios, _ = castellation.check(bg, RULES, 1.6)
    near = {v["kind"]: v["distance_mm"] for v in vios if v["pad"] == "J1.1"}
    assert set(near) == {"castellated_to_corner", "castellated_to_other_edge"}
    # 1.37 - 0.5 hole radius from the corner; the hole cuts into the arc
    assert near["castellated_to_corner"] == pytest.approx(0.87, abs=0.01)
    assert near["castellated_to_other_edge"] < 0
    # pin 2, 3.91 mm along, clears both: only the end hole fails
    assert {v["pad"] for v in vios} == {"J1.1"}


def test_rounded_corners_compliant_board_passes(tmp_path):
    # the fixture's own placement on the same rounded outline: end holes
    # 8.65 mm from the short edges, far past 3 mm from the arcs
    pcb = _mutant(tmp_path, lambda t: _rounded(t, 1.0))
    bg = geom.load_board(pcb)
    assert bg.outline_items.get("gr_arc") == 4
    vios, facts = castellation.check(bg, RULES, 1.6)
    assert vios == [] and facts["castellated_pads"] == 12


def test_corner_spans_shapes():
    from shapely.geometry import Point as P, box
    from shapely import affinity
    sharp = list(box(0, 0, 30, 20).exterior.coords)[:-1]
    assert [i == j for i, j in castellation._corner_spans(sharp)] == [True] * 4
    # a round board: one curve all the way round, no corner
    disc = list(P(0, 0).buffer(10, quad_segs=32).exterior.coords)[:-1]
    assert castellation._corner_spans(disc) == []
    # rounded rectangle, ring started mid-arc so one corner wraps vertex 0
    rr = box(1, 1, 29, 19).buffer(1, quad_segs=8)
    pts = list(rr.exterior.coords)[:-1]
    spans = castellation._corner_spans(pts[3:] + pts[:3])
    assert len(spans) == 4 and all(i != j for i, j in spans)
    assert sum(i > j for i, j in spans) == 1
    # a sweep wider than CORNER_MAX_R_MM is a curved edge, not a corner
    wide = box(8, 8, 22, 12).buffer(castellation.CORNER_MAX_R_MM + 3,
                                    quad_segs=8)
    assert castellation._corner_spans(
        list(affinity.translate(wide, 0, 0).exterior.coords)[:-1]) == []


def test_castellated_drill_and_ring_and_extension(tmp_path):
    pcb = _mutant(tmp_path, lambda t: t.replace(
        "(drill 1\n", "(drill 0.5\n").replace("(drill 1)", "(drill 0.5)"))
    kinds = _kinds(pcb)
    assert kinds.get("castellated_drill") == 12
    # a 0.5 drill in a 1.5 x 2.05 pad leaves more ring/extension, not less
    assert "castellated_ring" not in kinds
    thin = _mutant(tmp_path, lambda t: re.sub(
        r"\(size 1\.5 2\.05\)", "(size 1.2 1.2)", t))
    kinds = _kinds(thin)
    assert kinds.get("castellated_ring") == 12
    assert kinds.get("castellated_pad_extension") == 12


def test_thickness_and_board_size(tmp_path):
    vios, _ = castellation.check(geom.load_board(FIX), RULES, 0.4)
    assert [v["kind"] for v in vios] == ["castellated_thickness"]
    vios, _ = castellation.check(geom.load_board(FIX), RULES, 0.6)
    assert vios == []


# ------------------------------------------------------------------- DFM

@pytest.mark.smoke
def test_dfm_exempts_castellated_and_checks_them(tmp_path):
    import dfm_check
    rep = dfm_check.run(FIX, polarity=False)
    assert rep["status"] == "pass", [v["msg"] for v in rep["violations"]]
    assert rep["castellated_pads"] == 12
    assert "castellation" in rep["coverage"]["ran"]
    # the same board without the property: ordinary edge rules fire
    plain = _mutant(tmp_path, lambda t: t.replace(
        " (property pad_prop_castellated)", "").replace(
        "(property pad_prop_castellated)", ""))
    rep = dfm_check.run(plain, polarity=False)
    kinds = {v["kind"] for v in rep["violations"]}
    assert {"castellated_unmarked", "dfm_hole_to_edge",
            "dfm_copper_to_edge"} <= kinds


# ------------------------------------------------------- quote and order

def test_quote_adds_the_castellated_surcharge():
    rep = order_quote.run(FIX, [5], ["HASL"], ["green"])
    row = rep["matrix"][0]
    assert rep["spec"]["castellated_pads"] == 12
    assert row["pcb"]["castellated"] == 36.0
    assert row["pcb"]["total"] == round(row["pcb"]["base"] + 36.0, 2)
    plain = order_quote.run(PLAIN, [5], ["HASL"], ["green"])
    assert "castellated_pads" not in plain["spec"]
    assert "castellated" not in plain["matrix"][0]["pcb"]


def test_pcb_param_castellated_holes_follows_the_board():
    spec = {"layers": 2, "qty": 5, "width_mm": 30, "height_mm": 20}
    assert order_submit.build_pcb_param(spec)["castellatedHoles"] == 0
    spec["castellated_pads"] = 12
    assert order_submit.build_pcb_param(spec)["castellatedHoles"] == 1


def test_order_manifest_counts_from_the_board(tmp_path):
    fab = tmp_path / "fab"
    fab.mkdir()
    man = order_submit.run(FIX, fab)
    assert man["spec_snapshot"]["castellated_pads"] == 12
    assert any(s.startswith("CASTELLATED:") for s in man["human_steps"])
    plain = order_submit.run(PLAIN, fab)
    assert plain["spec_snapshot"]["castellated_pads"] == 0
    assert not any("CASTELLATED" in s for s in plain["human_steps"])


# ----------------------------------------------------------------- report

def _doc(ws: Path) -> report_gen.DocBuilder:
    return report_gen.DocBuilder(ws, {"board": "x"}, "x")


def test_report_names_castellation_only_when_the_board_has_it(tmp_path):
    ws = tmp_path / "x"
    (ws / "brief").mkdir(parents=True)
    (ws / "brief" / "brief.md").write_text(
        "Pico-style castellated edge pins.\n", encoding="utf-8")
    doc = _doc(ws)
    doc.castellation_line({"castellated_pads": 12,
                           "castellated_refs": ["J1", "J2"]}, None)
    assert "12 castellated pad(s) on J1, J2" in doc.body[-1]
    assert doc.warnings == []

    doc = _doc(ws)
    doc.castellation_line({"castellated_pads": 0}, None)
    assert "none" in doc.body[-1]
    assert len(doc.warnings) == 1 and "brief/brief.md" in doc.warnings[0]

    # a brief that records the deviation is not a claim
    (ws / "brief" / "brief.md").write_text(
        "Edge pins ship as plain PTH, not castellated.\n", encoding="utf-8")
    doc = _doc(ws)
    doc.castellation_line(None, {"spec_snapshot": {"castellated_pads": 0}})
    assert doc.warnings == []


# ------------------------------------------------------------ rp2040-mini

def test_rp2040_mini_has_no_castellated_pads_today():
    ws = real_board("rp2040-mini")
    pcbs = sorted(p for p in ws.rglob("*.kicad_pcb")
                  if "state_snapshots" not in p.parts
                  and not p.name.startswith("_"))
    assert pcbs, f"no board file under {ws}"
    pcb = next((p for p in pcbs if p.parent.name == "route"), pcbs[-1])
    assert castellation.count(pcb) == 0


def test_count_and_refs_are_a_text_scan(tmp_path):
    """The order and BOM legs count a board geom cannot load."""
    assert castellation.refs(FIX) == ["J1", "J2"]
    stub = tmp_path / "stub.kicad_pcb"
    stub.write_text("(kicad_pcb)\n", encoding="utf-8")
    assert castellation.count(stub) == 0 and castellation.refs(stub) == []


def test_half_round_end_counts_from_its_arc():
    # a stadium: two 180 deg ends whose edges never meet, so the corner
    # distance is measured to the arc itself
    from shapely.geometry import LineString as L
    stadium = L([(5, 5), (25, 5)]).buffer(2, quad_segs=8)
    runs, corners, arcs = castellation._runs(stadium)
    assert len(runs) == 2 and len(arcs) == 2
    assert all(isinstance(k, L) for k in corners)
