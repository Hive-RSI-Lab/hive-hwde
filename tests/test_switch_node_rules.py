"""rules_gen's switch-node rules (#924, #923 point 6) and a power entry's
min_width_mm: a buck's SW/LX net declared with "switch_node" gets no vias,
one layer, and a keep-away from listed parts; an undeclared switch pin's net
gets a width at its regulator's rating. The live tests plant each fault on a
real board and run KiCad DRC on rules_gen's own .kicad_dru."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import board_init  # noqa: E402
import check_current  # noqa: E402
import env  # noqa: E402
import geom  # noqa: E402
import kc  # noqa: E402
import route_critical as rc  # noqa: E402
import rules_gen  # noqa: E402

CAP = yaml.safe_load((REPO / "reference" / "jlc_capabilities.yaml").read_text("utf-8")
                    )["design_rules"]["4layer_1oz"]
STACKS = yaml.safe_load((REPO / "reference" / "stackups.yaml").read_text("utf-8"))
STK4 = STACKS["stackups"][STACKS["defaults"][4]]
NETS = ["/LX", "/VOUT", "/MIC", "GND", "Net-(U2-SW)"]


def _w(a, dt=10.0):
    return round(check_current.required_width_mm(a, dt, 0.035), 4)


def _fp(ref, x, y, pads, value="X", layer="F.Cu"):
    """pads: [(num, dx, net, pinfunction)]."""
    body = "".join(
        f'    (pad "{n}" smd rect (at {dx} 0) (size 1 1) (layers "{layer}" '
        f'"{layer[0]}.Mask") (net "{net}")'
        + (f' (pinfunction "{pf}")' if pf else "")
        + f' (uuid "p-{ref}-{n}"))\n' for n, dx, net, pf in pads)
    return (f'  (footprint "t:{ref}" (layer "{layer}") (at {x} {y})\n'
            f'    (property "Reference" "{ref}" (at 0 -2 0) (layer "F.SilkS") '
            f'(uuid "r-{ref}"))\n'
            f'    (property "Value" "{value}" (at 0 2 0) (layer "F.Fab") '
            f'(uuid "v-{ref}"))\n{body}  )\n')


BASE = (_fp("U1", 10, 10, [("1", 0, "/LX", "SW"), ("2", -2, "GND", "GND")],
            value="TPS62130")
        + _fp("L1", 30, 10, [("1", 0, "/LX", ""), ("2", 3, "/VOUT", "")])
        + _fp("MK1", 20, 13, [("1", 0, "/MIC", "")]))


def _track(net, x0, y0, x1, y1, w=0.6, layer="F.Cu", tag="t"):
    return (f'  (segment (start {x0} {y0}) (end {x1} {y1}) (width {w}) '
            f'(layer "{layer}") (net "{net}") (uuid "{tag}-{x0}-{y0}-{layer}"))\n')


def _via(net, x, y, tag="v"):
    return (f'  (via (at {x} {y}) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") '
            f'(net "{net}") (uuid "{tag}-{x}-{y}"))\n')


def _write(tmp_path, body, name="sw"):
    pcb = tmp_path / f"{name}.kicad_pcb"
    decl = "".join(f'(net {i + 1} "{n}") ' for i, n in enumerate(NETS))
    pcb.write_text(f"""(kicad_pcb
  (version 20260206) (generator "test")
  (general (thickness 1.6))
  (layers (0 "F.Cu" signal) (4 "In1.Cu" signal) (6 "In2.Cu" signal) (2 "B.Cu" signal)
    (13 "F.Paste" user) (1 "F.Mask" user) (3 "B.Mask" user)
    (5 "F.SilkS" user "F.Silkscreen") (31 "F.CrtYd" user "F.Courtyard")
    (35 "F.Fab" user) (25 "Edge.Cuts" user))
  (setup
  )
  (net 0 "") {decl}
  (gr_rect (start 0 0) (end 50 30) (stroke (width 0.1)) (fill no) (layer "Edge.Cuts"))
{body})
""", encoding="utf-8")
    return pcb


def _gen(tmp_path, pcb, c):
    board_init.inject_stackup(pcb, STK4)
    pcb.with_suffix(".kicad_pro").write_text(
        json.dumps(board_init.build_pro(pcb.stem, CAP)), encoding="utf-8")
    cf = tmp_path / "constraints.json"
    cf.write_text(json.dumps(c), encoding="utf-8")
    out = tmp_path / f"{pcb.stem}-rg.json"
    assert rules_gen.main(["--constraints", str(cf), "--layers", "4",
                           "--out-dru", str(pcb.with_suffix(".kicad_dru")),
                           "--pro", str(pcb.with_suffix(".kicad_pro")),
                           "--pcb", str(pcb), "--out", str(out)]) == 0
    return json.loads(out.read_text())


SW = {"power": [{"net": "/LX", "current_a": 0.6, "dt_c": 10,
                 "min_width_mm": 0.5, "pdn": False,
                 "switch_node": {"keep_away": ["MK1"], "keep_away_mm": 2.0}}]}


# ---------------------------------------------------------------- pure

def test_min_width_mm_is_a_floor_never_a_ceiling():
    rules, facts = rules_gen.power_rules({"power": [
        {"net": "A", "current_a": 0.6, "min_width_mm": 0.5},
        {"net": "B", "current_a": 3.0, "min_width_mm": 0.5}]}, 0.035)
    by = {f["net"]: f for f in facts}
    assert _w(0.6) < 0.5 and by["A"]["min_width_mm"] == 0.5
    assert by["B"]["min_width_mm"] == _w(3.0) > 0.5
    assert {r.name: r.minv for r in rules}["aiee_pwr_width_A"] == 0.5


def test_route_critical_lays_min_width_and_takes_switch_nodes_first():
    specs = rc.power_specs({"power": [
        {"net": "+5V", "current_a": 1.0},
        {"net": "/LX", "current_a": 0.6, "min_width_mm": 0.5,
         "switch_node": True}]})
    assert [s["net"] for s in specs] == ["/LX", "+5V"]
    assert specs[0]["width_mm"] >= 0.5


def test_switch_node_rules_on_the_regulators_side(tmp_path):
    bg = geom.BoardGeom.from_file(_write(tmp_path, BASE))
    rules, report = rules_gen.build(SW, CAP, STK4, False, bg)
    by = {r.name: r for r in rules}
    assert by["aiee_sw_novia_LX"].constraint == "disallow via"
    assert by["aiee_sw_layer_LX"].condition == \
        "A.NetName == '/LX' && A.Layer != 'F.Cu'"
    away = by["aiee_sw_away_LX_MK1"]
    assert away.constraint == "clearance" and away.minv == 2.0
    assert "B.memberOfFootprint('MK1')" in away.condition
    (f,) = report["switch_nodes"]
    assert f["ref"] == "U1" and f["layer"] == "F.Cu" and f["width_mm"] == 0.5
    # the class carries the width so the router lays it
    assert any(p["pattern"] == "/LX" and p["netclass"] != "Default"
               for p in report["patterns"])


def test_a_keep_away_wildcard_is_refused(tmp_path):
    c = {"power": [dict(SW["power"][0], switch_node={"keep_away": ["MK*"]})]}
    with pytest.raises(SystemExit, match="wildcard"):
        rules_gen.build(c, CAP, STK4, False, None)


def test_an_undeclared_switch_pin_is_sized_and_warned(tmp_path):
    """#923 point 6, planted: U2 (an LM2596, rated 3 A) has its OUT pin on
    a net no power entry names. Without the inferred rule it stays at the
    signal floor; with it, it gets a width at 3 A and a class."""
    body = BASE + _fp("U2", 10, 20, [("2", 0, "Net-(U2-SW)", "OUT")],
                      value="LM2596S-5.0")
    bg = geom.BoardGeom.from_file(_write(tmp_path, body))
    rules, report = rules_gen.build(SW, CAP, STK4, False, bg)
    want = _w(3.0, check_current.INFERRED_DT_C)
    r = {r.name: r for r in rules}["aiee_pwr_width_Net__U2_SW"]
    assert r.minv == want
    assert any(f["net"] == "Net-(U2-SW)" and f.get("inferred")
               for f in report["switch_nodes"])
    assert any("Net-(U2-SW)" in w and "declare it" in w
               for w in report["switch_warnings"])
    # no via/layer rules on a node nobody declared
    assert not any(n.startswith("aiee_sw_") and "U2" in n for n in
                   (x.name for x in rules))


def test_a_declared_switch_node_with_no_switch_pin_is_warned(tmp_path):
    body = BASE.replace('(pinfunction "SW")', "")
    bg = geom.BoardGeom.from_file(_write(tmp_path, body))
    _, report = rules_gen.build(SW, CAP, STK4, False, bg)
    assert any("no pad on it is a switch pin" in w
               for w in report["switch_warnings"])


# ---------------------------------------------------------------- live DRC

def _drc(pcb):
    cli = env.find_kicad_cli()
    if cli is None:
        pytest.skip("needs kicad-cli")
    rep = kc.run_drc(cli, pcb)
    return [v for v in rep["violations"] if "aiee_sw_" in v.get("msg", "")
            or "aiee_pwr_" in v.get("msg", "")]


GOOD = _track("/LX", 10, 10, 30, 10)        # one F.Cu run, 0.6 mm, 3 mm off MK1


@pytest.mark.parametrize("plant, rule", [
    (_via("/LX", 20, 10), "aiee_sw_novia_LX"),
    (_track("/LX", 20, 10, 20, 20, layer="B.Cu", tag="b"), "aiee_sw_layer_LX"),
    (_track("/LX", 20, 10, 20, 11.6, tag="near"), "aiee_sw_away_LX_MK1"),
    (_track("/LX", 30, 10, 30, 20, w=0.3, tag="thin"), "aiee_pwr_width_LX"),
])
def test_live_drc_catches_each_planted_switch_node_fault(tmp_path, plant, rule):
    pcb = _write(tmp_path, BASE + GOOD)
    _gen(tmp_path, pcb, SW)
    assert _drc(pcb) == []
    bad = _write(tmp_path, BASE + GOOD + plant, name="bad")
    _gen(tmp_path, bad, SW)
    hits = _drc(bad)
    assert hits and all(rule in v["msg"] for v in hits), hits
