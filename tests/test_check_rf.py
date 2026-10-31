"""check_rf (hive-hwde #335): stubs, electrical length and phase, band
isolation, shield cans and reference cutouts, on synthetic boards; the
planted faults on rf4 are tests/golden/manifest.d/check_rf.yaml's."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
import check_rf  # noqa: E402
import constraints_lint  # noqa: E402
import geom  # noqa: E402
import impedance  # noqa: E402
import keepout_gen  # noqa: E402
import verify_all  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "golden" / "mutations"))
import rf4_rf  # noqa: E402

LAYERS2 = '(0 "F.Cu" signal) (2 "B.Cu" signal)'
LAYERS4 = ('(0 "F.Cu" signal) (4 "In1.Cu" signal) (6 "In2.Cu" signal) '
           '(2 "B.Cu" signal)')


def _pcb(tmp_path, body: str, layers: str = LAYERS2, name="rf") -> Path:
    p = tmp_path / f"{name}.kicad_pcb"
    p.write_text(f"""(kicad_pcb (version 20260206) (generator "test")
  (general (thickness 1.6))
  (layers {layers} (25 "Edge.Cuts" user))
  (setup)
  (gr_rect (start 0 0) (end 60 40) (stroke (width 0.1)) (fill no)
    (layer "Edge.Cuts"))
{body})
""", encoding="utf-8")
    return p


def _fp(ref, at, pads, layer="F.Cu", lib="t:R"):
    """pads: [(num, x, y, w, h, net)] in the footprint's frame."""
    ps = " ".join(
        f'(pad "{n}" smd rect (at {x} {y}) (size {w} {h}) '
        f'(layers "{layer}") (net "{net}"))' for n, x, y, w, h, net in pads)
    return (f'  (footprint "{lib}" (at {at[0]} {at[1]}) (layer "{layer}")\n'
            f'    (property "Reference" "{ref}" (at 0 0 0) (layer "F.SilkS"))\n'
            f'    (property "Value" "{ref}" (at 0 0 0) (layer "F.Fab"))\n'
            f'    {ps})\n')


def _seg(a, b, net, layer="F.Cu", w=0.3):
    return (f'  (segment (start {a[0]} {a[1]}) (end {b[0]} {b[1]}) '
            f'(width {w}) (layer "{layer}") (net "{net}"))\n')


def _via(at, net, size=0.6, drill=0.3):
    return (f'  (via (at {at[0]} {at[1]}) (size {size}) (drill {drill}) '
            f'(layers "F.Cu" "B.Cu") (net "{net}"))\n')


def _plane(layer, net="GND", x0=0):
    """A plane over the board; its fill starts at x0 (cleared left of it)."""
    pts = "(pts (xy 0 0) (xy 60 0) (xy 60 40) (xy 0 40))"
    fill = f"(pts (xy {x0} 0) (xy 60 0) (xy 60 40) (xy {x0} 40))"
    return (f'  (zone (net "{net}") (layer "{layer}") (polygon {pts})\n'
            f'    (filled_polygon (layer "{layer}") {fill}))\n')


def _line(a=(10, 20), b=(40, 20), net="/RF"):
    """Two parts joined by one track: A.1 at a, B.1 at b."""
    return (_fp("A", a, [("1", 0, 0, 1, 1, net)])
            + _fp("B", b, [("1", 0, 0, 1, 1, net)]) + _seg(a, b, net))


def _run(tmp_path, pcb, cons):
    c = tmp_path / "constraints.json"
    c.write_text(json.dumps(cons), encoding="utf-8")
    payload, _ = check_rf.run(["--pcb", str(pcb), "--constraints", str(c)])
    return payload, [v["kind"] for v in payload["violations"]]


# ------------------------------------------------------------- media

@pytest.mark.parametrize("w,h,er", [(0.5, 0.254, 3.66), (1.0, 0.5, 4.5),
                                    (2.9, 1.5, 4.5)])
def test_closed_form_eeff_matches_the_field_solver(w, h, er):
    cf = impedance.microstrip_eeff_closed_form(w, h, 0.035, er)
    fs = impedance.field_microstrip(w, h, 0.035, er, mask=None)["eps_eff"]
    assert abs(cf - fs) / fs < 0.005


def test_no_rf_constraints_passes_with_nothing_checked(tmp_path):
    payload, kinds = _run(tmp_path, _pcb(tmp_path, _line()), {})
    assert payload["status"] == "pass" and kinds == []


# ------------------------------------------------------------- stubs

def test_open_stub_off_a_tee_fails_at_its_dead_end(tmp_path):
    pcb = _pcb(tmp_path, _line() + _seg((25, 20), (25, 5), "/RF"))
    payload, kinds = _run(tmp_path, pcb, {"rf": [{"net": "/RF",
                                                   "f_ghz": 2.4}]})
    assert kinds == ["rf_stub"]
    v = payload["violations"][0]
    assert v["pos"] == [25.0, 5.0] and v["net"] == "/RF"
    assert v["length_mm"] == pytest.approx(15.0, abs=0.01)


def test_short_stub_and_tuned_stub_pass(tmp_path):
    short = _pcb(tmp_path, _line() + _seg((25, 20), (25, 19), "/RF"),
                 name="s")
    assert _run(tmp_path, short, {"rf": [{"net": "/RF", "f_ghz": 2.4}]})[1] \
        == []
    long_ = _pcb(tmp_path, _line() + _seg((25, 20), (25, 5), "/RF"),
                 name="l")
    assert _run(tmp_path, long_, {"rf": [{"net": "/RF", "f_ghz": 2.4,
                                          "allow_stubs": True}]})[1] == []
    # the same 15 mm is under lambda/20 at 100 MHz
    assert _run(tmp_path, long_, {"rf": [{"net": "/RF", "f_ghz": 0.1}]})[1] \
        == []


def test_shunt_branch_to_a_pad_is_not_a_stub(tmp_path):
    body = _line() + _fp("C", (25, 5), [("1", 0, 0, 1, 1, "/RF")]) + \
        _seg((25, 20), (25, 5), "/RF")
    assert _run(tmp_path, _pcb(tmp_path, body),
                {"rf": [{"net": "/RF", "f_ghz": 2.4}]})[1] == []


def _layer_hop(f):
    body = (_fp("A", (10, 20), [("1", 0, 0, 1, 1, "/RF")])
            + _fp("B", (40, 20), [("1", 0, 0, 1, 1, "/RF")])
            + _seg((10, 20), (20, 20), "/RF") + _via((20, 20), "/RF")
            + _seg((20, 20), (30, 20), "/RF", "In1.Cu")
            + _via((30, 20), "/RF") + _seg((30, 20), (40, 20), "/RF"))
    return body, {"rf": [{"net": "/RF", "f_ghz": f}]}


def test_via_barrel_past_its_last_layer_is_a_stub_at_mmwave(tmp_path):
    body, cons = _layer_hop(24.0)
    payload, kinds = _run(tmp_path, _pcb(tmp_path, body, LAYERS4), cons)
    assert kinds == ["rf_via_stub", "rf_via_stub"]
    assert payload["violations"][0]["layers"] == ["In1.Cu", "B.Cu"]
    body, cons = _layer_hop(0.9)
    assert _run(tmp_path, _pcb(tmp_path, body, LAYERS4, "lo"), cons)[1] == []


# ------------------------------------------------------------- length, phase

def test_electrical_length_limits(tmp_path):
    pcb = _pcb(tmp_path, _line())
    payload, kinds = _run(tmp_path, pcb, {"rf": [{"net": "/RF",
                                                   "f_ghz": 2.4}]})
    assert kinds == []
    cons = {"rf": [{"net": "/RF", "f_ghz": 2.4, "max_deg": 10}]}
    payload, kinds = _run(tmp_path, pcb, cons)
    assert kinds == ["rf_length"]
    deg = payload["violations"][0]["deg"]
    eeff = check_rf.Medium(geom.load_board(pcb)).eeff("F.Cu", 0.3)
    assert deg == pytest.approx(360 * 30 * eeff ** 0.5 * 2.4 / 299.792458,
                                abs=0.05)
    ok = {"rf": [{"net": "/RF", "f_ghz": 2.4, "length_deg": deg,
                  "tol_deg": 1}]}
    assert _run(tmp_path, pcb, ok)[1] == []
    off = {"rf": [{"net": "/RF", "f_ghz": 2.4, "length_deg": deg + 10}]}
    assert _run(tmp_path, pcb, off)[1] == ["rf_length"]


def test_phase_group_spread(tmp_path):
    body = _line() + _line((10, 30), (45, 30), "/RF2").replace(
        '"A"', '"A2"').replace('"B"', '"B2"')
    pcb = _pcb(tmp_path, body)
    rf = [{"net": "/RF", "f_ghz": 2.4}, {"net": "/RF2", "f_ghz": 2.4}]
    payload, kinds = _run(tmp_path, pcb, {"rf": rf, "rf_phase": [
        {"nets": ["/RF", "/RF2"], "tol_deg": 5}]})
    assert kinds == ["rf_phase_mismatch"]
    assert payload["violations"][0]["net"] == "/RF2"
    assert _run(tmp_path, pcb, {"rf": rf, "rf_phase": [
        {"nets": ["/RF", "/RF2"], "tol_deg": 60}]})[1] == []


def test_three_pad_net_needs_a_path(tmp_path):
    body = _line() + _fp("C", (25, 5), [("1", 0, 0, 1, 1, "/RF")]) + \
        _seg((25, 20), (25, 5), "/RF")
    pcb = _pcb(tmp_path, body)
    _, kinds = _run(tmp_path, pcb, {"rf": [{"net": "/RF", "f_ghz": 2.4,
                                            "max_deg": 200}]})
    assert kinds == ["rf_path_open"]
    payload, kinds = _run(tmp_path, pcb, {"rf": [
        {"net": "/RF", "f_ghz": 2.4, "max_deg": 200, "path": ["A.1", "B.1"]}]})
    assert kinds == []
    assert payload["checked"][0]["nets"][0]["path"] == ["A.1", "B.1"]


# ------------------------------------------------------------- isolation

def _two_bands(gap, vias=()):
    body = _line((10, 20), (40, 20), "/X") + _line(
        (10, 20 + gap), (40, 20 + gap), "/K").replace(
        '"A"', '"A2"').replace('"B"', '"B2"')
    body += "".join(_via(v, "GND") for v in vias)
    blocks = [{"name": "x", "f_ghz": 10.5, "nets": ["/X"]},
              {"name": "k", "f_ghz": 24.15, "nets": ["/K"]}]
    return body, blocks


def test_blocks_too_close_without_a_wall_fail(tmp_path):
    body, blocks = _two_bands(1.5)
    payload, kinds = _run(tmp_path, _pcb(tmp_path, body),
                          {"rf_blocks": blocks})
    assert kinds == ["rf_isolation"]
    assert payload["violations"][0]["layer"] == "F.Cu"


def test_a_via_wall_at_lambda_over_10_isolates_them(tmp_path):
    # lambda_g/10 at 24.15 GHz in er 4.5 is 0.59 mm; vias every 0.5 mm
    wall = [(8 + 0.5 * k, 20.75) for k in range(70)]
    body, blocks = _two_bands(1.5, wall)
    assert _run(tmp_path, _pcb(tmp_path, body), {"rf_blocks": blocks})[1] \
        == []
    sparse = [(8 + 2.0 * k, 20.75) for k in range(18)]
    body, blocks = _two_bands(1.5, sparse)
    assert _run(tmp_path, _pcb(tmp_path, body, name="sp"),
                {"rf_blocks": blocks})[1] == ["rf_isolation"]


def test_wall_true_demands_the_wall_even_when_far(tmp_path):
    body, blocks = _two_bands(12)
    pcb = _pcb(tmp_path, body)
    assert _run(tmp_path, pcb, {"rf_blocks": blocks})[1] == []
    _, kinds = _run(tmp_path, pcb, {"rf_blocks": blocks, "rf_isolation": [
        {"a": "x", "b": "k", "wall": True}]})
    assert kinds == ["rf_isolation_wall"]


# ------------------------------------------------------------- shields

def _can(vias_on=("n", "s", "e", "w"), part_at=(30, 20), height=None):
    """A 20 x 20 mm can frame SH1 (four 0.5 mm GND strips round 20..40 x
    10..30), GND vias every 1 mm along its sides, U1 inside."""
    strips = [("1", 10, 0, 20.5, 0.5, "GND"), ("2", 10, 20, 20.5, 0.5, "GND"),
              ("3", 0, 10, 0.5, 20.5, "GND"), ("4", 20, 10, 0.5, 20.5, "GND")]
    body = _fp("SH1", (20, 10), strips, lib="t:CAN")
    side = {"n": [(20 + k, 10) for k in range(21)],
            "s": [(20 + k, 30) for k in range(21)],
            "w": [(20, 10 + k) for k in range(1, 20)],
            "e": [(40, 10 + k) for k in range(1, 20)]}
    for s in vias_on:
        body += "".join(_via(v, "GND", 0.4, 0.2) for v in side[s])
    lib = f"t:QFN_H{height}" if height else "t:QFN"
    body += _fp("U1", part_at, [("1", -1, 0, 0.5, 0.5, "/RF"),
                                ("2", 1, 0, 0.5, 0.5, "GND")], lib=lib)
    return body


SHIELD = {"name": "lna", "ref": "SH1", "refs": ["U1"], "f_ghz": 5.0}


def test_a_closed_can_passes(tmp_path):
    payload, kinds = _run(tmp_path, _pcb(tmp_path, _can()),
                          {"shields": [SHIELD]})
    assert kinds == []
    fact = payload["checked"][0]["shields"][0]
    assert fact["ring_covered"] == 1.0 and fact["fence_vias"] == 80


def test_can_fence_with_a_side_missing_fails(tmp_path):
    payload, kinds = _run(tmp_path, _pcb(tmp_path, _can(("n", "s", "w"))),
                          {"shields": [SHIELD]})
    assert kinds == ["shield_fence_gap"]
    assert payload["violations"][0]["pos"][0] == pytest.approx(40, abs=0.3)


def test_declared_can_with_no_ring_fails(tmp_path):
    body = _can()
    cons = {"shields": [{"name": "lna", "rect": [20, 10, 40, 30],
                         "net": "GNDX", "refs": ["U1"]}]}
    _, kinds = _run(tmp_path, _pcb(tmp_path, body), cons)
    assert "shield_ring_gap" in kinds and "shield_fence_gap" in kinds


def test_part_outside_the_can_fails(tmp_path):
    _, kinds = _run(tmp_path, _pcb(tmp_path, _can(part_at=(45, 20))),
                    {"shields": [SHIELD]})
    assert kinds == ["shield_part_outside"]


def test_tall_part_inside_the_can(tmp_path):
    cons = {"shields": [dict(SHIELD, max_height_mm=1.5)]}
    assert _run(tmp_path, _pcb(tmp_path, _can(height=0.9)), cons)[1] == []
    _, kinds = _run(tmp_path, _pcb(tmp_path, _can(height=2.5), name="t"),
                    cons)
    assert kinds == ["shield_tall_part"]
    _, kinds = _run(tmp_path, _pcb(tmp_path, _can(), name="u"), cons)
    assert kinds == ["shield_height_unknown"]
    over = {"shields": [dict(SHIELD, max_height_mm=1.5,
                             heights={"U1": 0.8})]}
    assert _run(tmp_path, _pcb(tmp_path, _can(), name="v"), over)[1] == []


# ------------------------------------------------------------- cutouts

CUT = {"ref": "A", "pads": ["1"], "margin_mm": 0.5, "layers": ["In1.Cu"]}


def test_plane_under_a_declared_cutout_fails(tmp_path):
    body = _line() + _plane("In1.Cu")
    payload, kinds = _run(tmp_path, _pcb(tmp_path, body, LAYERS4),
                          {"rf_cutouts": [CUT]})
    assert kinds == ["rf_cutout_copper"]
    v = payload["violations"][0]
    assert v["layer"] == "In1.Cu" and v["refs"] == ["A"]
    # the 1 mm pad grown 0.5 mm, round-cornered: 4 - (4 - pi) / 4
    assert v["area_mm2"] == pytest.approx(3.785, abs=0.01)


def test_plane_cleared_under_the_cutout_passes(tmp_path):
    body = _line() + _plane("In1.Cu", x0=11)
    assert _run(tmp_path, _pcb(tmp_path, body, LAYERS4),
                {"rf_cutouts": [CUT]})[1] == []


def test_keepout_gen_draws_cutouts_and_layer_keepouts(tmp_path):
    bg = geom.BoardGeom.from_file(_pcb(tmp_path, _line(), LAYERS4))
    areas = keepout_gen.plan_cutouts({"rf_cutouts": [CUT]}, bg)
    assert len(areas) == 1 and areas[0]["layers"] == ["In1.Cu"]
    assert all(areas[0][k] for k in keepout_gen.KEPT)
    xs = [p[0] for p in areas[0]["poly"]]
    assert min(xs) == pytest.approx(9.0, abs=0.01)
    ko = keepout_gen.plan_areas({"placement": {"keepouts": [
        {"rect": [0, 0, 5, 5], "copper": True, "layers": ["In2.Cu"]}]}},
        bg.copper_layers)
    assert ko[0]["layers"] == ["In2.Cu"]
    with pytest.raises(keepout_gen.CheckError):
        keepout_gen.plan_areas({"placement": {"keepouts": [
            {"rect": [0, 0, 5, 5], "copper": True, "layers": ["In9.Cu"]}]}},
            bg.copper_layers)


# ------------------------------------------------------------- rf4

def test_rf4_golden_passes_under_its_rf_declarations(tmp_path):
    """The known-good RF board: rf4 as committed, with what its rf mutants
    declare, finds nothing, and measures its feed."""
    payload, kinds = _run(tmp_path, REPO / "tests" / "golden" / "rf4" /
                          "rf4.kicad_pcb", rf4_rf.constraints())
    assert kinds == [], payload["violations"]
    feed = payload["checked"][0]["nets"][0]
    assert feed["net"] == "/RF_FEED" and 25 < feed["deg"] < 40
    iso = payload["checked"][0]["isolation"][0]["layers"]["F.Cu"]
    assert iso["spacing_mm"] > iso["min_mm"]


# ------------------------------------------------------------- wiring

def test_check_rf_joins_verify_all():
    names = [c["name"] for c in verify_all.load_checks()]
    assert "check_rf" in names


def test_rf_sections_lint_clean(tmp_path):
    cons = rf4_rf.constraints()
    cons.update(shields=[SHIELD], rf_cutouts=[CUT], rf_phase=[
        {"nets": ["/RF1", "/RF_FEED"], "tol_deg": 5}],
        rf_isolation=[{"a": "radio", "b": "host", "wall": True}])
    p = tmp_path / "c.json"
    p.write_text(json.dumps(cons), encoding="utf-8")
    payload, _ = constraints_lint.run(["--file", str(p)])
    assert [v for v in payload["violations"]
            if v["severity"] == "error"] == [], payload["violations"]
