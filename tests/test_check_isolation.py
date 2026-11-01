"""check_isolation + isotables (#353). Known answers: hand-computed gaps and
detours on synthetic boards, table rows at their edges, and TI SLUAAR5's worked
interpolations (the only published ones found)."""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
import check_isolation as ci  # noqa: E402
import checklib  # noqa: E402
import isotables as it  # noqa: E402

BARRIER = {"name": "b1", "between": ["A", "B"], "standard": "iec60664-1",
           "grade": "basic", "working_v_rms": 250, "impulse_v": 2500,
           "pollution_degree": 2, "material_group": "IIIa", "altitude_m": 2000}
DOMAINS = {"A": {"nets": ["GND_A", "A_*"]}, "B": {"nets": ["GND_B", "B_*"]}}
RATING = {"grade": "basic", "working_v_rms": 400, "impulse_v": 4000,
          "source": "test"}


def iso(**over):
    barrier = {**BARRIER, **over.pop("barrier", {})}
    return {"isolation": {"domains": DOMAINS, "barriers": [barrier], **over}}


def rect(x0, y0, x1, y1):
    return f"(pts (xy {x0} {y0}) (xy {x1} {y0}) (xy {x1} {y1}) (xy {x0} {y1}))"


def zone(net, x0, y0, x1, y1, layer="F.Cu"):
    return (f'(zone (net "{net}") (net_name "{net}") (layer "{layer}") '
            f'(hatch edge 0.5) (connect_pads (clearance 0)) (min_thickness 0.2) '
            f'(fill yes) (polygon {rect(x0, y0, x1, y1)}) '
            f'(filled_polygon (layer "{layer}") {rect(x0, y0, x1, y1)}))\n')


def slot(x0, y0, x1, y1):
    return (f'(gr_rect (start {x0} {y0}) (end {x1} {y1}) (stroke (width 0.1)) '
            f'(fill no) (layer "Edge.Cuts"))\n')


def part(ref, x, y, nets, *, dx=4.0):
    pads = "".join(
        f'(pad "{i + 1}" smd rect (at {i * dx} 0) (size 1 1) (layers "F.Cu") '
        f'(net "{n}"))\n' for i, n in enumerate(nets))
    return (f'(footprint "t:P" (at {x} {y}) (layer "F.Cu") '
            f'(property "Reference" "{ref}") {pads})\n')


def board(tmp_path, body, inner=False, thick=None):
    layers = '(0 "F.Cu" signal) '
    if inner:
        layers += '(4 "In1.Cu" signal) (6 "In2.Cu" signal) '
    layers += '(2 "B.Cu" signal) (25 "Edge.Cuts" user)'
    nets = "".join(f'(net {i} "{n}")\n' for i, n in enumerate(
        ["", "GND_A", "GND_B", "A_S", "B_S", "X", "A_T", "B_T"]))
    # outline first and largest: the slot rectangles come after it
    text = (f'(kicad_pcb (version 20260206) (generator "test")\n'
            f'(general (thickness {thick or 1.6}))\n(layers {layers})\n(setup)\n'
            f'{nets}'
            f'(gr_rect (start -50 -50) (end 50 50) (stroke (width 0.1)) (fill no) '
            f'(layer "Edge.Cuts"))\n{body})\n')
    p = tmp_path / "iso.kicad_pcb"
    p.write_text(text, encoding="utf-8")
    return p


def facts_layer(facts, layer="F.Cu"):
    return next(r for r in facts["barriers"][0]["layers"] if r.get("layer") == layer)


def kinds(viols):
    return sorted({v["kind"] for v in viols})


# -------------------------------------------------------------------- geometry

def pads_8mm():
    """Two 10 mm wide pours with an 8.000 mm gap: y 0 and y 8."""
    return zone("GND_A", -5, -10, 5, 0) + zone("GND_B", -5, 8, 5, 18)


def test_straight_gap_reads_8_for_both(tmp_path):
    viols, facts = ci.check(board(tmp_path, pads_8mm()), iso())
    row = facts_layer(facts)
    assert row["clearance_mm"] == pytest.approx(8.000, abs=1e-3)
    assert row["creepage_mm"] == pytest.approx(8.000, abs=1e-3)
    assert not [v for v in viols if v["severity"] == "error"]


def test_slot_raises_creepage_by_the_hand_detour(tmp_path):
    """A 2 mm slot, 40 long, y 3..5. From A's corner (5, 0) to the slot's corner
    (20, 3) is hypot(15, 3); along the slot's end is 2; the same again to B's
    corner. The slot is air, so clearance stays 8."""
    expect = 2 * math.hypot(15, 3) + 2
    viols, facts = ci.check(board(tmp_path, pads_8mm() + slot(-20, 3, 20, 5)), iso())
    row = facts_layer(facts)
    assert row["clearance_mm"] == pytest.approx(8.000, abs=1e-3)
    assert row["creepage_mm"] == pytest.approx(expect, abs=1e-3)
    assert facts["barriers"][0]["holes_with_credit"] == 1


def test_slot_shorter_than_the_pours_earns_nothing(tmp_path):
    """A slot within the pours' width: the path walks past its end, straight."""
    viols, facts = ci.check(board(tmp_path, pads_8mm() + slot(-2, 3, 2, 5)), iso())
    assert facts_layer(facts)["creepage_mm"] == pytest.approx(8.000, abs=1e-3)


def test_slot_under_x_earns_nothing(tmp_path):
    """Issue's planted fault: gap 3 mm, 0.8 mm slot, PD2 (X = 1 mm): no credit."""
    body = zone("GND_A", -5, -10, 5, 0) + zone("GND_B", -5, 3, 5, 13) + \
        slot(-20, 1.1, 20, 1.9)
    viols, facts = ci.check(board(tmp_path, body), iso(barrier={
        "grade": "reinforced", "working_v_rms": 250}))
    row = facts_layer(facts)
    assert row["creepage_mm"] == pytest.approx(3.000, abs=1e-3)
    assert facts["barriers"][0]["holes_ignored"] == 1
    bad = [v for v in viols if v["kind"] == "iso_creepage"]
    assert bad and bad[0]["required_mm"] == pytest.approx(5.0)
    assert bad[0]["creepage_mm"] == pytest.approx(3.0, abs=1e-3)


def test_slot_of_exactly_x_counts(tmp_path):
    """Clause 4.2: "equal to or larger than" X follows the contour."""
    body = zone("GND_A", -5, -10, 5, 0) + zone("GND_B", -5, 3, 5, 13) + \
        slot(-20, 1, 20, 2)
    _, facts = ci.check(board(tmp_path, body), iso())
    expect = 2 * math.hypot(15, 1) + 1
    assert facts_layer(facts)["creepage_mm"] == pytest.approx(expect, abs=1e-3)


def test_slot_dividing_the_board_has_no_surface_path(tmp_path):
    _, facts = ci.check(board(tmp_path, pads_8mm() + slot(-50, 3, 50, 5)), iso())
    row = facts_layer(facts)
    assert row["creepage_mm"] is None and row["clearance_mm"] == pytest.approx(8.0)


def _lines(pts, head="gr", extra='(stroke (width 0.1) (type solid))'):
    return "".join(
        f'({head}_line (start {a[0]} {a[1]}) (end {b[0]} {b[1]}) {extra} '
        f'(layer "Edge.Cuts"))\n' for a, b in zip(pts, pts[1:] + pts[:1]))


SLOT_DETOUR = 2 * math.hypot(15, 3) + 2


def test_slot_drawn_as_lines(tmp_path):
    body = pads_8mm() + _lines([(-20, 3), (20, 3), (20, 5), (-20, 5)])
    _, facts = ci.check(board(tmp_path, body), iso())
    assert facts_layer(facts)["creepage_mm"] == pytest.approx(SLOT_DETOUR, abs=1e-3)


def test_slot_in_a_rotated_footprint(tmp_path):
    """The same slot, drawn in a footprint turned 90 deg: local (x, y) lands at
    board (y, -x) under R(-90), so it reads the same once placed."""
    pts = [(-3, -20), (-3, 20), (-5, 20), (-5, -20)]
    slot_fp = ('(footprint "t:S" (at 0 0 90) (layer "F.Cu") (property "Reference" "S1") '
               + _lines(pts, "fp") + ')\n')
    viols, facts = ci.check(board(tmp_path, pads_8mm() + slot_fp), iso())
    assert facts_layer(facts)["creepage_mm"] == pytest.approx(SLOT_DETOUR, abs=1e-3)


def test_npth_hole_counts_as_a_hole(tmp_path):
    hole = ('(footprint "t:H" (at 0 4) (layer "F.Cu") (property "Reference" "H1") '
            '(pad "" np_thru_hole circle (at 0 0) (size 40 40) (drill 40) '
            '(layers "*.Cu")))\n')
    body = zone("GND_A", -2, -30, 2, -21) + zone("GND_B", -2, 29, 2, 40) + hole
    _, facts = ci.check(board(tmp_path, body), iso())
    row = facts_layer(facts)
    assert row["clearance_mm"] == pytest.approx(50.0, abs=1e-3)
    assert row["creepage_mm"] > 50.0        # round a 40 mm disc


# --------------------------------------------------------------------- crossing

def test_pour_touching_across_the_barrier_names_both_nets(tmp_path):
    body = zone("GND_A", -5, -10, 5, 0) + zone("GND_B", -5, 0, 5, 10)
    viols, _ = ci.check(board(tmp_path, body), iso())
    hit = [v for v in viols if v["kind"] == "iso_touching"]
    assert len(hit) == 1 and hit[0]["severity"] == "error"
    assert {hit[0]["net"], hit[0]["other_net"]} == {"GND_A", "GND_B"}
    assert "GND_A" in hit[0]["msg"] and "GND_B" in hit[0]["msg"]


def test_low_voltage_domains_are_judged_where_creepage_passes(tmp_path):
    """The issue's complaint: nets under 30 V on each side are all check_creepage
    can see. The barrier's own voltage is what's judged here."""
    import check_creepage
    body = zone("GND_A", -5, -10, 5, 0) + zone("GND_B", -5, 0.2, 5, 10)
    p = board(tmp_path, body)
    c = iso(barrier={"grade": "reinforced"})
    c["voltages"] = [{"net": "GND_A", "voltage": 0}, {"net": "GND_B", "voltage": 5}]
    (tmp_path / "c.json").write_text(json.dumps(c))
    old, _ = check_creepage.run(["--pcb", str(p), "--constraints", str(tmp_path / "c.json")])
    assert old["violations"] == []
    viols, _ = ci.check(p, c)
    assert {"iso_clearance", "iso_creepage"} <= set(kinds(viols))


def test_via_of_the_other_domain_fails(tmp_path):
    via = '(via (at 0 1) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net "B_S"))\n'
    viols, _ = ci.check(board(tmp_path, zone("GND_A", -5, -10, 5, 0) + via), iso())
    hit = [v for v in viols if v["kind"] == "iso_clearance"]
    assert hit and hit[0]["other_net"] == "B_S" and hit[0]["net"] == "GND_A"
    assert hit[0]["spacing_mm"] == pytest.approx(0.7, abs=1e-2)


def test_copper_in_no_domain_shortens_the_barrier(tmp_path):
    """An island in the 8 mm gap: 3 + 3 mm, under 630 V's 6.3 mm (PD2, IIIa)."""
    z = zone("X", -1, 3, 1, 5)
    viols, _ = ci.check(board(tmp_path, pads_8mm() + z),
                        iso(barrier={"working_v_rms": 630}))
    hit = [v for v in viols if v["kind"] == "iso_floating_bridge"]
    assert hit and hit[0]["net"] == "X"
    assert hit[0]["spacing_mm"] == pytest.approx(6.0, abs=1e-3)
    assert hit[0]["required_mm"] == pytest.approx(6.3)


def test_separate_netless_islands_do_not_add(tmp_path):
    """One island beside each pour: each is 8 mm's worth of path on its own."""
    def island(ref, y):
        return (f'(footprint "t:N" (at 0 {y}) (layer "F.Cu") (property "Reference" "{ref}") '
                f'(pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu")))\n')
    body = pads_8mm() + island("N1", 1.0) + island("N2", 7.0)
    viols, _ = ci.check(board(tmp_path, body), iso(barrier={"working_v_rms": 630}))
    hit = [v for v in viols if v["kind"] == "iso_floating_bridge"]
    # each island: 0.5 + 6.5 = 7.0, over 6.3: no finding
    assert hit == []


def test_board_floor_raises_never_lowers():
    r = requirement(min_clearance_mm=8.0, min_creepage_mm=8.0)
    assert r["clearance"]["mm"] == 8.0 and r["creepage"]["mm"] == 8.0
    assert "min_clearance_mm" in r["clearance"]["basis"]
    assert requirement(min_clearance_mm=0.1)["clearance"]["mm"] == pytest.approx(1.5)
    r = requirement(min_clearance_mm=8.0)
    assert r["creepage"]["mm"] == 8.0       # creepage never under clearance


def test_non_rated_part_bridging_fails(tmp_path):
    body = pads_8mm() + part("R1", 0, 2, ["A_S", "B_S"])
    viols, _ = ci.check(board(tmp_path, body), iso())
    hit = [v for v in viols if v["kind"] == "iso_part_unrated"]
    assert hit and hit[0]["refs"] == ["R1"] and hit[0]["severity"] == "error"
    assert hit[0]["nets"] == ["A_S", "B_S"]


def test_rated_part_passes_and_its_pin_gap_is_reported(tmp_path):
    """U1's pads 7 mm centre to centre, 1 mm wide: a 6.000 mm pin gap."""
    body = pads_8mm() + part("U1", 10, 2, ["A_S", "B_S"], dx=7.0)
    viols, facts = ci.check(board(tmp_path, body),
                            iso(parts={"U1": RATING}))
    assert "iso_part_unrated" not in kinds(viols)
    assert "iso_part_rating_short" not in kinds(viols)
    row = facts["barriers"][0]["parts"][0]
    assert row["ref"] == "U1" and row["covers"] is True
    assert row["pin_gap_mm"] == pytest.approx(6.0, abs=1e-3)


def test_rated_part_with_tight_pins_warns(tmp_path):
    body = pads_8mm() + part("U1", 10, 2, ["A_S", "B_S"], dx=2.0)
    viols, _ = ci.check(board(tmp_path, body), iso(parts={"U1": RATING}))
    hit = [v for v in viols if v["kind"] == "iso_part_pin_gap"]
    assert hit and hit[0]["severity"] == "warning"
    assert hit[0]["spacing_mm"] == pytest.approx(1.0, abs=1e-3)


@pytest.mark.parametrize("rating, word", [
    ({**RATING, "grade": "functional"}, "grade"),
    ({**RATING, "working_v_rms": 100}, "working_v_rms"),
    ({**RATING, "impulse_v": 1500}, "impulse_v"),
    ({"grade": "basic", "impulse_v": 4000}, "no working_v_rms"),
    ({**RATING, "creepage_mm": 1.0}, "creepage_mm"),
])
def test_rating_that_does_not_cover_the_barrier_fails(tmp_path, rating, word):
    body = pads_8mm() + part("U1", 10, 2, ["A_S", "B_S"], dx=7.0)
    viols, _ = ci.check(board(tmp_path, body), iso(parts={"U1": rating}))
    hit = [v for v in viols if v["kind"] == "iso_part_rating_short"]
    assert hit and word in hit[0]["msg"]


def test_rating_must_cover_a_declared_withstand(tmp_path):
    body = pads_8mm() + part("U1", 10, 2, ["A_S", "B_S"], dx=7.0)
    viols, _ = ci.check(board(tmp_path, body), iso(
        barrier={"withstand_v_rms": 5000}, parts={"U1": RATING}))
    assert "no withstand_v_rms" in " ".join(
        v["msg"] for v in viols if v["kind"] == "iso_part_rating_short")


# ---------------------------------------------------------- inner / solid rules

def test_inner_gap_is_judged_by_the_declared_rule(tmp_path):
    body = zone("GND_A", -5, -10, 5, 0, "In1.Cu") + zone("GND_B", -5, 0.5, 5, 10, "In1.Cu")
    p = board(tmp_path, body, inner=True)
    viols, facts = ci.check(p, iso(barrier={"inner_min_mm": 1.0, "solid_min_mm": 0.1}))
    hit = [v for v in viols if v["kind"] == "iso_inner"]
    assert hit and hit[0]["severity"] == "error" and hit[0]["table_verified"] is True
    assert hit[0]["required_mm"] == 1.0 and hit[0]["spacing_mm"] == pytest.approx(0.5)
    assert facts_layer(facts, "In1.Cu")["kind"] == "inner"


def test_inner_gap_undeclared_is_a_warning_marked_unread(tmp_path):
    """250 V, printed wiring PD1 = 0.56: the column is read, the use of it isn't."""
    body = zone("GND_A", -5, -10, 5, 0, "In1.Cu") + zone("GND_B", -5, 0.5, 5, 10, "In1.Cu")
    viols, _ = ci.check(board(tmp_path, body, inner=True), iso())
    hit = [v for v in viols if v["kind"] == "iso_inner"]
    assert hit and hit[0]["severity"] == "warning"
    assert hit[0]["table_verified"] is False and hit[0]["required_mm"] == pytest.approx(0.56)
    assert "not read" in hit[0]["msg"]


def test_inner_copper_gets_no_air_or_surface_finding(tmp_path):
    body = zone("GND_A", -5, -10, 5, 0, "In1.Cu") + zone("GND_B", -5, 0.5, 5, 10, "In1.Cu")
    viols, _ = ci.check(board(tmp_path, body, inner=True), iso())
    assert not {"iso_clearance", "iso_creepage"} & set(kinds(viols))


def test_through_laminate_declared_rule(tmp_path):
    """A pour over a pour on the two faces of a 1.6 mm board: 1.53 of laminate."""
    body = zone("GND_A", -5, -10, 5, 10) + zone("GND_B", -5, -10, 5, 10, "B.Cu")
    viols, facts = ci.check(board(tmp_path, body), iso(barrier={"solid_min_mm": 2.0}))
    hit = [v for v in viols if v["kind"] == "iso_solid"]
    assert hit and hit[0]["severity"] == "error"
    assert hit[0]["spacing_mm"] == pytest.approx(1.53, abs=1e-3)   # less 2 x 35 um copper
    row = next(r for r in facts["barriers"][0]["layers"] if r["kind"] == "through_laminate")
    assert row["plan_gap_mm"] == 0.0


def test_basic_grade_has_no_thickness_rule(tmp_path):
    body = zone("GND_A", -5, -10, 5, 10) + zone("GND_B", -5, -10, 5, 10, "B.Cu")
    viols, _ = ci.check(board(tmp_path, body, thick=0.2), iso())
    assert "iso_solid" not in kinds(viols)


def test_mask_gets_no_credit(tmp_path):
    """The requirement is the bare-surface one, and the report says so."""
    _, facts = ci.check(board(tmp_path, pads_8mm()), iso())
    assert facts["barriers"][0]["mask_credit"] is False


# ------------------------------------------------------------------ declaration

def test_no_block_is_not_judged(tmp_path):
    viols, facts = ci.check(board(tmp_path, pads_8mm()), {})
    assert viols == [] and facts["judged"] is False


@pytest.mark.parametrize("key", ["working_v_rms", "pollution_degree",
                                 "material_group", "altitude_m", "grade"])
def test_every_barrier_value_is_declared(tmp_path, key):
    b = {k: v for k, v in BARRIER.items() if k != key}
    c = {"isolation": {"domains": DOMAINS, "barriers": [b]}}
    with pytest.raises(checklib.CheckError, match=key):
        ci.check(board(tmp_path, pads_8mm()), c)


def test_no_impulse_is_refused(tmp_path):
    b = {k: v for k, v in BARRIER.items() if k != "impulse_v"}
    with pytest.raises(checklib.CheckError, match="impulse_v"):
        ci.check(board(tmp_path, pads_8mm()),
                 {"isolation": {"domains": DOMAINS, "barriers": [b]}})


def test_net_in_two_domains_is_refused(tmp_path):
    c = {"isolation": {"domains": {"A": {"nets": ["GND_*"]}, "B": {"nets": ["GND_B"]}},
                       "barriers": [BARRIER]}}
    with pytest.raises(checklib.CheckError, match="GND_B"):
        ci.check(board(tmp_path, pads_8mm()), c)


def test_unknown_domain_is_refused(tmp_path):
    c = iso(barrier={"between": ["A", "C"]})
    with pytest.raises(checklib.CheckError, match="C"):
        ci.check(board(tmp_path, pads_8mm()), c)


def test_off_the_table_is_refused(tmp_path):
    c = iso(barrier={"working_v_rms": 20000})
    with pytest.raises(checklib.CheckError, match="table"):
        ci.check(board(tmp_path, pads_8mm()), c)


def test_undeclared_net_warns_and_floating_silences_it(tmp_path):
    body = pads_8mm() + zone("X", 30, 30, 40, 40)
    viols, _ = ci.check(board(tmp_path, body), iso())
    assert "iso_net_undeclared" in kinds(viols)
    viols, _ = ci.check(board(tmp_path, body), iso(floating=["X"]))
    assert "iso_net_undeclared" not in kinds(viols)


def test_rated_part_not_on_board_warns(tmp_path):
    viols, _ = ci.check(board(tmp_path, pads_8mm()), iso(parts={"U9": RATING}))
    assert "iso_part_absent" in kinds(viols)


def test_cli_exit_codes(tmp_path):
    (tmp_path / "c.json").write_text(json.dumps(iso()))
    ok = board(tmp_path, pads_8mm())
    assert ci.main(["--pcb", str(ok), "--constraints", str(tmp_path / "c.json"),
                    "--out", str(tmp_path / "r.json")]) == 0
    got = json.loads((tmp_path / "r.json").read_text())
    assert got["script"] == "check_isolation"
    (tmp_path / "bad.json").write_text(json.dumps(
        {"isolation": {"domains": DOMAINS, "barriers": [{"name": "x"}]}}))
    assert ci.main(["--pcb", str(ok), "--constraints", str(tmp_path / "bad.json"),
                    "--out", str(tmp_path / "r2.json")]) == 2


# ---------------------------------------------------------------------- tables

def table_rows(name):
    return it.tables()["iec60664_1"][name]


@pytest.mark.parametrize("name", ["clearance_impulse", "clearance_peak",
                                  "creepage", "altitude"])
def test_every_row_says_verified_and_where_from(name):
    known = it.tables()["sources"]
    for row in table_rows(name):
        assert isinstance(row["verified"], bool) and row["source"] in known


def test_rows_never_fall_as_the_voltage_rises():
    for name, x, ys in (("clearance_impulse", "kv", ["pd1", "pd2", "pd3"]),
                        ("clearance_peak", "kv", ["mm"]),
                        ("creepage", "v", ["pd1", "pd2_I", "pd2_II", "pd2_III",
                                           "pd3_I", "pd3_II", "pd3_III"])):
        rows = table_rows(name)
        assert [r[x] for r in rows] == sorted(r[x] for r in rows)
        for y in ys:
            vals = [r[y] for r in rows]
            assert vals == sorted(vals), (name, y)


def test_material_groups_only_get_wider():
    for r in table_rows("creepage"):
        assert r["pd2_I"] <= r["pd2_II"] <= r["pd2_III"]
        assert r["pd3_I"] <= r["pd3_II"] <= r["pd3_III"]


@pytest.mark.parametrize("v, group, pd, mm", [
    (10, "I", 2, 0.4),            # first row
    (5, "I", 2, 0.4),             # under it: reads the first row
    (32, "IIIa", 2, 0.53),        # last row before groups split at 40 V
    (40, "IIIa", 2, 1.1),
    (250, "I", 2, 1.25),
    (250, "II", 2, 1.8),
    (250, "IIIa", 2, 2.5),
    (250, "IIIb", 2, 2.5),        # one column in the table
    (250, "IIIa", 3, 4.0),
    (250, "IIIa", 1, 0.56),
    (1000, "IIIa", 2, 10),
    (10000, "IIIa", 2, 100),      # last row read
])
def test_creepage_at_row_edges(v, group, pd, mm):
    got = it.creepage_basic(v, pd, group)
    assert got["mm"] == pytest.approx(mm)
    assert got["verified"] is True and got["interpolated"] is False


def test_creepage_interpolates_as_ti_worked_it():
    """SLUAAR5 eq. 2-3: PD2 group I, 440 V -> 2.2 mm, 565 V -> 2.85 mm."""
    got = it.creepage_basic(440, 2, "I")
    assert got["mm"] == pytest.approx(2.2) and got["interpolated"] is True
    assert got["rows"] == [400, 500]
    assert it.creepage_basic(565, 2, "I")["mm"] == pytest.approx(2.85)


def test_creepage_just_past_a_row_moves_off_it():
    assert it.creepage_basic(250.01, 2, "IIIa")["mm"] > 2.5
    assert it.creepage_basic(320, 2, "IIIa")["mm"] == pytest.approx(3.2)


def test_above_the_table_is_refused():
    with pytest.raises(it.IsoTableError):
        it.creepage_basic(70000, 2, "I")
    with pytest.raises(it.IsoTableError):
        it.clearance_impulse(120, 2)
    with pytest.raises(it.IsoTableError):
        it.altitude_factor(21000)


def test_printed_wiring_column():
    assert it.creepage_basic(250, 2, "IIIa", "printed_wiring")["mm"] == pytest.approx(1.0)
    assert it.creepage_basic(250, 1, "IIIb", "printed_wiring")["mm"] == pytest.approx(0.56)
    with pytest.raises(it.IsoTableError, match="IIIb"):
        it.creepage_basic(250, 2, "IIIb", "printed_wiring")
    with pytest.raises(it.IsoTableError):
        it.creepage_basic(1250, 2, "I", "printed_wiring")


@pytest.mark.parametrize("kv, pd, mm", [
    (0.33, 1, 0.01), (0.33, 2, 0.2), (0.33, 3, 0.8),
    (1.0, 2, 0.2), (1.2, 2, 0.25), (1.5, 2, 0.5),
    (1.5, 3, 0.8), (2.0, 3, 1.0),
    (2.5, 2, 1.5), (4.0, 2, 3.0), (6.0, 2, 5.5), (8.0, 2, 8.0),   # SLUP419 Table 5
    (100, 2, 170),
])
def test_clearance_impulse_at_row_edges(kv, pd, mm):
    got = it.clearance_impulse(kv, pd)
    assert got["mm"] == pytest.approx(mm) and got["verified"] is True


def test_clearance_impulse_interpolates_between_rows():
    got = it.clearance_impulse(3.5, 2)
    assert got["mm"] == pytest.approx(2.5) and got["interpolated"] is True


def test_pd4_is_pd3_with_its_floor():
    assert it.clearance_impulse(1.0, 4)["mm"] == pytest.approx(1.6)
    assert it.clearance_impulse(4.0, 4)["mm"] == pytest.approx(3.0)


@pytest.mark.parametrize("kv, mm", [
    (0.33, 0.01), (1.5, 0.76), (2.5, 1.8), (4.0, 3.8), (6.0, 7.9), (50, 100)])
def test_clearance_peak_at_row_edges(kv, mm):
    assert it.clearance_peak(kv)["mm"] == pytest.approx(mm)


def test_clearance_peak_interpolates():
    assert it.clearance_peak(7.0)["mm"] == pytest.approx((7.9 + 11.0) / 2)


@pytest.mark.parametrize("m, f", [
    (0, 1.0), (2000, 1.0), (2001, 1.14), (3000, 1.14), (4000, 1.29),
    (4500, 1.48), (5000, 1.48), (20000, 14.5)])
def test_altitude_takes_the_next_row_up(m, f):
    assert it.altitude_factor(m)["factor"] == f


@pytest.mark.parametrize("basic, up", [
    (0.33, 0.5), (0.5, 0.8), (0.8, 1.5), (1.5, 2.5), (2.5, 4.0), (4.0, 6.0),
    (6.0, 8.0), (8.0, 12.0)])
def test_reinforced_is_one_preferred_step_up(basic, up):
    assert it.reinforced_impulse_kv(basic)["kv"] == up


def test_reinforced_off_the_series_or_at_its_top_is_160_percent():
    assert it.reinforced_impulse_kv(3.0)["kv"] == pytest.approx(4.8)
    assert it.reinforced_impulse_kv(12.0)["kv"] == pytest.approx(19.2)


def test_rated_impulse_table_edges():
    assert it.rated_impulse_v(230, "II")["v"] == 2500     # SLUP419's example
    assert it.rated_impulse_v(300, "II")["v"] == 2500
    assert it.rated_impulse_v(301, "II")["v"] == 4000
    assert it.rated_impulse_v(50, "I")["v"] == 330
    assert it.rated_impulse_v(1000, "IV")["v"] == 12000


def test_groove_widths():
    assert [it.groove_x_mm(p) for p in (1, 2, 3)] == [0.25, 1.0, 1.5]


def requirement(**over):
    return it.requirement({**BARRIER, **over})


def test_issue_known_answer_250v_pd2_reinforced():
    """#353's survey note: 250 V rms, PD2, group IIIb, reinforced -> 5.0 mm."""
    r = requirement(grade="reinforced", material_group="IIIb")
    assert r["creepage"]["mm"] == pytest.approx(5.0) and r["creepage"]["verified"]
    assert r["clearance"]["mm"] == pytest.approx(3.0)      # 2.5 -> 4 kV


def test_ti_worked_clearances():
    """SLUAAR5: 4000 V basic 3 mm; 6000 V reinforced row 5.5; 3 mm x 1.29 at 4000 m."""
    assert requirement(impulse_v=4000)["clearance"]["mm"] == pytest.approx(3.0)
    assert requirement(impulse_v=4000, grade="reinforced")["clearance"]["mm"] == pytest.approx(5.5)
    got = requirement(impulse_v=4000, altitude_m=4000)["clearance"]
    assert got["mm"] == pytest.approx(3.87) and got["altitude_factor"] == 1.29


def test_mains_and_category_stand_in_for_impulse():
    b = {k: v for k, v in BARRIER.items() if k != "impulse_v"}
    r = it.requirement({**b, "mains_v_rms": 230, "overvoltage_category": "III"})
    assert r["impulse_v"] == 4000 and r["clearance"]["mm"] == pytest.approx(3.0)


def test_creepage_is_never_under_the_clearance():
    r = requirement(working_v_rms=50, impulse_v=4000)
    assert r["creepage"]["mm"] == pytest.approx(3.0)
    assert "raised to the clearance" in r["creepage"]["basis"]


def test_5kv_withstand_drives_the_clearance_off_table_7a():
    """5 kV rms = 7.071 kV peak, 7a Case A between 6 kV 7.9 and 8 kV 11.0."""
    r = requirement(withstand_v_rms=5000)
    expect = 7.9 + (5 * math.sqrt(2) - 6.0) * (11.0 - 7.9) / 2.0
    assert r["clearance"]["mm"] == pytest.approx(expect, abs=1e-6)
    assert r["creepage"]["mm"] == pytest.approx(expect, abs=1e-6)


def test_62368_rows_say_what_was_read():
    r = requirement(standard="iec62368-1", impulse_v=2500)
    assert r["clearance"]["mm"] == pytest.approx(1.8)     # Table 10, via SLUP419
    assert r["clearance"]["verified"] is False            # altitude table unread
    assert r["creepage"]["verified"] is False             # Table 17 unread
    assert it.clearance_impulse(2.5, 2, "iec62368-1")["verified"] is True
    assert it.clearance_impulse(3.0, 2, "iec62368-1")["verified"] is False


def test_unread_finding_says_so(tmp_path):
    body = zone("GND_A", -5, -10, 5, 0) + zone("GND_B", -5, 1, 5, 10)
    viols, _ = ci.check(board(tmp_path, body), iso(barrier={"standard": "iec62368-1"}))
    hit = [v for v in viols if v["kind"] == "iso_creepage"]
    assert hit and hit[0]["table_verified"] is False and "not read" in hit[0]["msg"]
