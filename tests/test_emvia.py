"""lib/emvia.py (#479, #599 item 3): vias and launches in 3D.

Hermetic: the geometry and its refusals, the deck's mesh (no collapse, stub
barrel, an open floor, the planes' stitch), the readers on S from closed-form
circuits, the rule, the Palace deck's text.

Live (test-slow, skipped without the IIC-OSIC tree), known answers on small
synthetic vias. Tolerances are from the refinement study in docs/solvers.md:
  - a coaxial via (a barrel in a ring of 8 rods) against
    (60 / sqrt(er)) [ln(R / a) + ln(R / (n r)) / n] and its transit time;
  - a stub's notch, with a blind twin that has none;
  - Palace driven on the same via within about 1 dB of openEMS in |S21|.
"""
from __future__ import annotations

import json
import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
import check_via_stubs  # noqa: E402
import viastub  # noqa: E402
from lib import emboard as eb  # noqa: E402
from lib import emvia as ev  # noqa: E402
from lib import solvers  # noqa: E402

HAVE_TREE = solvers.toolchain_root() is not None
live = pytest.mark.skipif(not HAVE_TREE, reason="no IIC-OSIC-TOOLS tree")


def stack4():
    """Microstrip on 0.2 mm each side of a 2 mm core, er 4.3: the coax via's."""
    return {"layers": ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"], "z": {"B.Cu": 0.0, "In2.Cu": 0.2, "In1.Cu": 2.2, "F.Cu": 2.4},
            "slabs": [[2.2, 2.4, 4.3], [0.2, 2.2, 4.3], [0.0, 0.2, 4.3]]}


def stack5():
    """F.Cu, In1, In2 0.2 and 0.3 apart, In3 0.3 under, then a 2.1 mm slab: a
    stub from In2 2.4 mm long."""
    z = {"B.Cu": 0.0, "In3.Cu": 2.1, "In2.Cu": 2.4, "In1.Cu": 2.7, "F.Cu": 2.9}
    ls = ["F.Cu", "In1.Cu", "In2.Cu", "In3.Cu", "B.Cu"]
    return {"layers": ls, "z": z, "slabs": [[z[ls[i + 1]], z[ls[i]], 4.3] for i in range(4)]}


def coax_spec():
    return ev.ViaSpec(stack=stack4(), in_layer="F.Cu", out_layer="B.Cu", w_in=0.38, w_out=0.38, returns=ev.ring(8, 1.0, 22.5),
                      run=2.0, half_y=2.0, ret_top="In1.Cu", ret_bot="In2.Cu")


def stub_spec(blind=False):
    return ev.ViaSpec(stack=stack5(), in_layer="F.Cu", out_layer="In2.Cu", w_in=0.36, w_out=0.28, returns=ev.ring(4, 1.0, 45),
                      run=2.0, half_y=2.5, barrel_bot="In2.Cu" if blind else None)


# ---------------------------------------------------------------- geometry

def test_synth_draws_planes_with_antipads_and_two_ports():
    from shapely.geometry import Point, Polygon
    from shapely.ops import unary_union
    geo, ports = ev.synth(coax_spec())
    by = {}
    for c in geo["copper"]:
        by.setdefault((c["layer"], c["net"]), []).append(Polygon(c["pts"]))
    plane = unary_union(by[("In1.Cu", "GND")])
    assert not plane.contains(Point(0, 0)) and not plane.contains(Point(0.45, 0))
    assert plane.contains(Point(0.55, 0.05))
    assert unary_union(by[("F.Cu", "SIG")]).contains(Point(-1.5, 0))
    assert ("F.Cu", "GND") not in by      # the returns stop at the planes here
    assert [p.layer for p in ports] == ["F.Cu", "B.Cu"]
    assert ports[0].plane == -2.0 and ports[1].plane == 2.0
    assert geo["drills"][0]["top"] == "F.Cu" and geo["drills"][0]["bot"] == "B.Cu"
    assert len(geo["drills"]) == 9 and geo["drills"][1]["top"] == "In1.Cu"


def test_synth_refuses_a_return_pad_on_the_line():
    spec = coax_spec()
    spec.ret_top, spec.ret_bot = None, None     # through: the ring's pads now reach F.Cu
    with pytest.raises(ValueError, match="shorts the signal"):
        ev.synth(spec)


def test_synth_refuses_a_barrel_that_misses_the_out_layer():
    with pytest.raises(ValueError, match="doesn't reach"):
        ev.synth(ev.ViaSpec(stack=stack5(), in_layer="F.Cu", out_layer="B.Cu", w_in=0.3, w_out=0.3, barrel_bot="In2.Cu"))


def test_stub_info_is_the_barrel_past_the_exit_and_the_viastub_closed_form():
    geo, _ = ev.synth(stub_spec())
    s = ev.stub_info(geo)
    assert s["length_mm"] == pytest.approx(2.4) and s["to"] == "B.Cu" and s["from"] == "In2.Cu"
    assert s["closed_ghz"] == pytest.approx(viastub.f_res_ghz(2.4, 4.3), abs=1e-3)
    assert ev.stub_info(ev.synth(stub_spec(blind=True))[0])["length_mm"] == 0.0


def test_stack_named_reads_a_stackups_build():
    st = ev.stack_named("JLC04161H-1080B")
    assert st["layers"] == ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]
    assert st["z"]["B.Cu"] == 0 and st["z"]["F.Cu"] == pytest.approx(0.2444 + 1.065 + 0.2444)
    assert st["slabs"][0][2] == 3.91 and st["slabs"][1][2] == 4.38
    with pytest.raises(ValueError):
        ev.stack_named("nope")


# ---------------------------------------------------------------- deck

def test_deck_has_no_timestep_collapse_and_reports_cells_and_dt():
    geo, ports = ev.synth(coax_spec())
    csx, where, st = ev.build(geo, ports, 20e9, 0, fine=0.1)
    assert st["cells"] == where["cells"] and st["cells"] < 400000
    assert min(st["min_cell_mm"]["x"], st["min_cell_mm"]["y"]) >= 0.1 / 4
    assert st["dt_courant_s"] > 3e-14
    ET.fromstring(csx.xml())


def test_inside_the_zone_the_mesh_is_the_fine_grid_with_the_barrel_on_it():
    """Round antipads staircase on a uniform grid; the barrels' faces stay lines."""
    geo, ports = ev.synth(coax_spec())
    fine = 0.05
    csx = ev.build(geo, ports, 20e9, 0, fine=fine)[0]
    xs = sorted(csx.lines["x"])
    inner = [q - p for p, q in zip(xs, xs[1:]) if -0.9 <= p and q <= 0.9]
    assert inner and max(inner) <= 1.4 * fine and min(inner) >= fine / 4
    half = 0.3 * math.sqrt(math.pi) / 4
    assert min(abs(x - half) for x in xs) < fine / 3
    # a real collapse is refused
    csx = eb.em.Csx(1e-3)
    csx.add_lines("x", [0, 0.001, 1]); csx.add_lines("y", [0, 1]); csx.add_lines("z", [0, 1])
    with pytest.raises(ValueError, match="timestep collapse"):
        ev.mesh_stats(csx, fine=0.1)


def test_the_floor_is_open_so_a_stub_is_not_shorted():
    geo, ports = ev.synth(stub_spec())
    csx, where, _ = ev.build(geo, ports, 25e9, 0, fine=0.1)
    assert where["floor"] == "open"
    assert csx.bc[4] == "MUR"
    assert min(csx.lines["z"]) < 0


def test_a_stripline_feed_gets_posts_between_its_planes():
    geo, ports = ev.synth(stub_spec())
    csx, _, _ = ev.build(geo, ports, 25e9, 0, fine=0.1)
    names = [a["Name"] for _, a, _, _ in csx.props]
    assert "feed_posts1" in names       # In2 between In1 and In3
    assert "feed_posts0" not in names   # F.Cu has nothing above
    boxes = next(b for _, a, _, b in csx.props if a["Name"] == "feed_posts1")
    z = {round(b[1][2], 4) for b in boxes} | {round(b[2][2], 4) for b in boxes}
    assert z == {2.1, 2.7}


def test_stub_and_blind_decks_differ_only_in_the_barrel():
    a = ev.build(*ev.synth(stub_spec()), 25e9, 0, fine=0.1)[0]
    b = ev.build(*ev.synth(stub_spec(True)), 25e9, 0, fine=0.1)[0]

    def signal_barrel(csx):
        boxes = next(q for _, n, _, q in csx.props if n["Name"] == "barrels")
        return next(p for p in boxes if abs(p[1][0] + p[2][0]) < 1e-6 and abs(p[1][1] + p[2][1]) < 1e-6)
    assert signal_barrel(a)[1][2] == 0.0
    assert signal_barrel(b)[1][2] == pytest.approx(2.4)


def test_launch_deck_has_the_coax_probes_and_two_states():
    spec = ev.LaunchSpec(stack=ev.stack_named("JLC04161H-1080B"), layer="F.Cu", w=0.4544, pad_w=1.2, pad_l=4.8, pad_in=2.7,
                         gap=0.45, antipad=0.35, gnd_y=2.55, run=7.0)
    m_csx, where, st = ev.launch_build(spec, 6e9, fine=0.15)
    s_csx, _, _ = ev.launch_build(spec, 6e9, fine=0.15, shorted=True)
    assert [p["layer"] for p in where["ports"]] == ["F.Cu", "coax"]
    assert where["ports"][1]["dist"][0] > where["ports"][1]["dist"][2] or where["ports"][1]["dist"][0] != where["ports"][1]["dist"][1]
    d = where["ports"][1]["dist"]
    assert d[0] - d[1] == pytest.approx(d[1] - d[2])    # equal spacing: port_vi's formula needs it
    names = {a["Name"] for _, a, _, _ in m_csx.props}
    assert {"p1u0", "p1u1", "p1u2", "p1i0", "p1i1", "ptfe", "sma"} <= names
    box_count = lambda c: sum(len(b) for _, a, _, b in c.props if a["Name"] == "sma")
    assert box_count(s_csx) == box_count(m_csx) + 1     # the plate
    assert st["cells"] > 0 and st["dt_courant_s"] > 0
    assert "Cylinder" in m_csx.xml()
    ET.fromstring(m_csx.xml())


# ---------------------------------------------------------------- readers

def _freqs(n=401, top=30e9):
    return [top * (i + 1) / n for i in range(n)]


def _stub_s(freqs, z0, length, er):
    """A shunt open stub on a 50 ohm line, exact."""
    import cmath
    out = []
    for f in freqs:
        y = 1j * math.tan(2 * math.pi * f * length * 1e-3 * math.sqrt(er) / ev.C0) / z0
        s21 = 2 / (2 + 50 * y)
        out.append([[s21 - 1, s21], [s21, s21 - 1]])
        assert cmath.isfinite(s21)
    return out


def test_find_notch_reads_a_quarter_wave_stub():
    f = _freqs()
    n = ev.find_notch(f, _stub_s(f, 50, 2.4, 4.3))
    assert n["f_hz"] / 1e9 == pytest.approx(ev.stub_notch_ghz(2.4, 4.3), rel=0.002)


def test_find_notch_is_none_on_a_line():
    f = _freqs()
    assert ev.find_notch(f, eb.line_s(f, 50, 3.3, 10)) is None


def test_lc_from_s_reads_a_line_back():
    z0, er, length = 60.0, 4.0, 2.0
    f = _freqs(201, 10e9)
    r = ev.lc_from_s(f, eb.line_s(f, z0, er, length), 2e9)
    td = length * 1e-3 * 2 / ev.C0
    assert r["z_ohm"] == pytest.approx(z0, rel=0.01)
    assert r["delay_ps"] == pytest.approx(td * 1e12, rel=0.01)
    assert r["L_nH"] == pytest.approx(z0 * td * 1e9, rel=0.01)


def test_tdr_reads_a_section_of_another_impedance():
    df = 50e6
    f = [df * (i + 1) for i in range(800)]      # 40 GHz
    t = ev.tdr(f, eb.line_s(f, 70.0, 4.0, 20.0))
    assert ev.z_window(t, 40, 200)["z_max"] == pytest.approx(70.0, abs=2.0)
    assert t["rise_ps"] == pytest.approx(12.5)
    assert ev.z_window(ev.tdr(f, eb.line_s(f, 50.0, 4.0, 20.0)), 0, 300)["z_max"] == pytest.approx(50.0, abs=0.5)


def test_tdr_refuses_a_grid_that_is_not_uniform():
    with pytest.raises(ValueError, match="uniform"):
        ev.tdr([1e9, 2e9, 4e9], [[[0, 1], [1, 0]]] * 3)


def test_cascade_and_flip_on_lines():
    f = [5e9]
    a, b = eb.line_s(f, 50.0, 4.0, 3.0)[0], eb.line_s(f, 50.0, 4.0, 7.0)[0]
    whole = eb.line_s(f, 50.0, 4.0, 10.0)[0]
    got = ev.cascade(a, b)
    for i in range(2):
        for j in range(2):
            assert abs(got[i][j] - whole[i][j]) < 1e-9
    assert ev.flip([[1, 2], [3, 4]]) == [[4, 3], [2, 1]]


def test_two_load_runs_recover_s_exactly():
    """launch_study's method: a source on one port and two loads on the other."""
    s = [[0.2 + 0.1j, 0.8 - 0.2j], [0.8 - 0.2j, -0.1 + 0.05j]]

    def run(g2):
        a1 = 1.0 + 0j
        b2 = s[1][0] * a1 / (1 - s[1][1] * g2)
        a2 = g2 * b2
        return [[(a1, s[0][0] * a1 + s[0][1] * a2), (a2, b2)]]
    got = eb.sparams({0: run(0.02), 1: run(-0.95)}, 2)[0]
    for i in range(2):
        for j in range(2):
            assert abs(got[i][j] - s[i][j]) < 1e-9


def test_refined_runs_twice_and_gates_on_emboards_tolerance():
    f = _freqs(201)
    calls = []

    def run(length):
        def go(refine):
            calls.append(refine)
            ln = 2.4 if refine == 1.0 else length
            return {"freqs": f, "S": _stub_s(f, 50, ln, 4.3), "stats": {"cells": 10, "dt_courant_s": 1e-13}}
        return go
    good = ev.refined(run(2.4), 30e9)
    assert calls == [1.0, 1.5] and good["refine"]["converged"] and good["refine"]["ds"] == 0
    assert not ev.refined(run(2.6), 30e9)["refine"]["converged"]


def test_refine_divides_every_cell_of_a_via_deck():
    geo, ports = ev.synth(coax_spec())
    a = ev.build(geo, ports, 20e9, 0, fine=0.1)[2]
    b = ev.build(geo, ports, 20e9, 0, fine=0.1, refine=1.5)[2]
    assert b["fine"] == pytest.approx(0.1 / 1.5, abs=1e-4)
    for i in range(3):
        assert b["mesh"][i] > a["mesh"][i]
    assert b["dt_courant_s"] < a["dt_courant_s"]


# ---------------------------------------------------------------- closed forms, the rule

def test_cage_closed_form():
    z = ev.cage_z0(1.0, 0.15, 8, 1.0, 0.15)
    assert z == pytest.approx(60 * (math.log(1 / 0.15) + math.log(1 / 1.2) / 8), rel=1e-9)
    lc = ev.cage_lc(4.3, 2.0, 0.15, 8, 1.0, 0.15)
    assert lc["L_nH"] / lc["C_pF"] * 1e3 == pytest.approx(lc["z_ohm"] ** 2, rel=1e-6)


def test_johnson_graham_figures():
    # the formula in inches, from millimetres
    assert ev.via_c_johnson_graham(4.4, 0.0625 * 25.4, 0.028, 0.050) == pytest.approx(1.41 * 4.4 * 0.0625 * 0.028 / 0.022, rel=1e-6)
    # L: h = 0.063 in, d = 0.016 in: 5.08 * 0.063 * (ln 15.75 + 1) = 1.2 nH
    assert ev.via_l_johnson_graham(0.063 * 25.4, 0.016 * 25.4) == pytest.approx(1.2, abs=0.01)


def test_judge_notch_under_twice_the_knee():
    kinds = [f["kind"] for f in ev.judge(15.0, 10.0, [(1, 0)], ev.lambda_mm(10, 4.3))]
    assert "via_em_stub_notch" in kinds
    assert not ev.judge(25.0, 10.0, [(1, 0)], ev.lambda_mm(10, 4.3))


def test_judge_return_far_and_none():
    lam = ev.lambda_mm(10, 4.3)     # 14.5 mm, so a limit of 1.45
    assert [f["kind"] for f in ev.judge(None, 10.0, [(2.0, 0)], lam)] == ["via_return_far"]
    assert ev.judge(None, 10.0, [(1.0, 0)], lam) == []
    assert [f["kind"] for f in ev.judge(None, 10.0, [], lam)] == ["via_no_return"]
    assert ev.judge(5.0, None, [], lam) == []      # nothing without a frequency


# ---------------------------------------------------------------- from a board

LAYERS = """(layers (0 "F.Cu" signal) (4 "In1.Cu" signal) (6 "In2.Cu" signal)
 (2 "B.Cu" signal) (25 "Edge.Cuts" user))"""
STACK = """(setup (stackup
 (layer "F.Cu" (type "copper") (thickness 0.035))
 (layer "dielectric 1" (type "prepreg") (thickness 0.2) (epsilon_r 4.3))
 (layer "In1.Cu" (type "copper") (thickness 0.0175))
 (layer "dielectric 2" (type "core") (thickness 1.095) (epsilon_r 4.3))
 (layer "In2.Cu" (type "copper") (thickness 0.0175))
 (layer "dielectric 3" (type "prepreg") (thickness 0.2) (epsilon_r 4.3))
 (layer "B.Cu" (type "copper") (thickness 0.035))))"""


def _zone(layer, net="GND"):
    pts = "(pts (xy 0 0) (xy 40 0) (xy 40 20) (xy 0 20))"
    return (f'(zone (net 2) (net_name "{net}") (layer "{layer}") (hatch edge 0.5) '
            f'(connect_pads yes (clearance 0.2)) (min_thickness 0.2) (fill yes) (polygon {pts}) '
            f'(filled_polygon (layer "{layer}") {pts}))')


def _board(path, ret_at=None, angled=False):
    end = "(end 28 16)" if angled else "(end 28 10)"
    ret = (f'(via (at {ret_at[0]} {ret_at[1]}) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net 2))' if ret_at else "")
    path.write_text(
        f'(kicad_pcb (version 20240108) (generator "test") (general (thickness 1.6)) {LAYERS} {STACK} '
        f'(net 0 "") (net 1 "/SIG") (net 2 "GND") '
        f'(gr_rect (start 0 0) (end 40 20) (layer "Edge.Cuts") (stroke (width 0.1) (type solid))) '
        f'(segment (start 5 10) (end 20 10) (width 0.3) (layer "F.Cu") (net 1)) '
        f'(segment (start 20 10) {end} (width 0.3) (layer "B.Cu") (net 1)) '
        f'(via (at 20 10) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net 1)) {ret} '
        f'{_zone("In1.Cu")} {_zone("In2.Cu")})', encoding="utf-8")
    return path


def test_from_board_finds_the_via_and_its_two_ports(tmp_path):
    pcb = _board(tmp_path / "b.kicad_pcb", ret_at=(21, 11))
    geo, ports = ev.from_board(pcb, 20, 10)
    assert sorted((p.dir, p.layer) for p in ports) == [("+x", "B.Cu"), ("-x", "F.Cu")]
    assert geo["via"]["net"] == "/SIG" and geo["via"]["used"] == ["F.Cu", "B.Cu"]
    assert geo["via"]["returns"] == [[1.0, 1.0]]
    csx, where, st = ev.build(geo, ports, 15e9, 0, fine=0.1)
    assert where["ports"][0]["ref"] == "In1.Cu" and where["ports"][1]["ref"] == "In2.Cu"
    assert where["floor"] == "open" and st["cells"] > 0


def test_from_board_says_why_it_refuses(tmp_path):
    with pytest.raises(ValueError, match="no via within"):
        ev.from_board(_board(tmp_path / "a.kicad_pcb"), 5, 5)
    with pytest.raises(ValueError, match="no region up to"):
        ev.from_board(_board(tmp_path / "b.kicad_pcb", angled=True), 20, 10)


def test_check_via_stubs_flags_a_far_return_via(tmp_path):
    cons = {"high_speed": [{"net": "/SIG", "f_max_ghz": 10}]}
    far, _ = check_via_stubs.check(_board(tmp_path / "a.kicad_pcb", ret_at=(24, 10)), cons)
    assert [v for v in far if v["kind"] == "via_return_far"]
    near, facts = check_via_stubs.check(_board(tmp_path / "b.kicad_pcb", ret_at=(21, 10)), cons)
    assert not [v for v in near if v["kind"].startswith("via_return") or v["kind"] == "via_no_return"]
    assert facts["vias"][0]["return_via_mm"] == 1.0
    none, _ = check_via_stubs.check(_board(tmp_path / "c.kicad_pcb"), cons)
    assert [v for v in none if v["kind"] == "via_no_return"]
    unjudged, facts = check_via_stubs.check(_board(tmp_path / "d.kicad_pcb", ret_at=(24, 10)), {"backdrill": []})
    assert not unjudged


# ---------------------------------------------------------------- Palace deck

def test_palace_geo_names_every_group_and_refuses_nothing_silently():
    g = ev.palace_geo(coax_spec(), fine=0.2)
    for name in ('"pec", 1', '"outer", 2', '"ends", 3', '"port0", 11', '"port1", 12', '"slab0", 1', '"air", 100'):
        assert name in g
    assert g.count("Cylinder(") == 9
    assert "BooleanFragments" in g and "BooleanDifference" in g


def test_palace_config_magnetic_end_walls_and_port_directions():
    c = ev.palace_config("via.msh", coax_spec(), 2.0, 20.0, 2.0)
    b = c["Boundaries"]
    assert b["PMC"]["Attributes"] == [3] and b["Absorbing"]["Attributes"] == [2]
    assert [p["Direction"] for p in b["LumpedPort"]] == ["+Z", "-Z"]
    assert b["LumpedPort"][0]["Excitation"] is True
    assert [m["Permittivity"] for m in c["Domains"]["Materials"]] == [4.3, 4.3, 4.3, 1.0]


def test_palace_s_reads_a_port_csv(tmp_path):
    (tmp_path / "port-S.csv").write_text(
        "        f (GHz),             |S[1][1]| (dB),        arg(S[1][1]) (deg.),"
        "             |S[2][1]| (dB),        arg(S[2][1]) (deg.)\n"
        " 2.0e+00, -25.5, +160.0, -0.1, -31.0\n 4.0e+00, -21.0, +150.0, -0.2, -62.0\n")
    r = ev.palace_s(tmp_path)
    assert r["f_ghz"] == [2.0, 4.0] and r["s11_db"] == [-25.5, -21.0] and r["s21_db"] == [-0.1, -0.2]
    assert r["s21_deg"] == [-31.0, -62.0]


def test_cli_via_deck_only(tmp_path, capsys):
    import sim_solve
    rc = sim_solve.main(["via", "--stackup", "JLC04161H-1080B", "--w-in", "0.45", "--w-out", "0.45", "--returns", "4",
                         "--return-r", "1.2", "--f-max", "10e9", "--deck-only", str(tmp_path)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert len(out["decks"]) == 2 and out["decks"][0]["cells"] > 0 and out["decks"][0]["dt_courant_s"] > 0
    assert (tmp_path / "port1" / "deck.xml").is_file()


# ---------------------------------------------------------------- live

@live
def test_live_coax_via_matches_the_cage_closed_form():
    spec = coax_spec()
    res = ev.study(*ev.synth(spec), 20e9, points=191, fine=0.1, symmetric=True, shift=[2.0, 2.0], threads=3, timeout=1500)
    lc = ev.lc_from_s(res["freqs"], res["S_shifted"], 5e9)
    want = ev.cage_lc(4.3, 2.4, 0.15, 8, 1.0, 0.15)
    assert lc["z_ohm"] == pytest.approx(want["z_ohm"], rel=0.12)
    assert lc["delay_ps"] == pytest.approx(want["delay_ps"], rel=0.08)
    assert all(r["rang_down"] for r in res["runs"])
    assert ev.find_notch(res["freqs"], res["S"]) is None


@live
def test_live_stub_notch_and_its_blind_twin():
    closed = ev.stub_notch_ghz(2.4, 4.3)
    stub = ev.study(*ev.synth(stub_spec()), 25e9, points=121, fine=0.1, threads=3, timeout=1500)
    n = ev.find_notch(stub["freqs"], stub["S"])
    assert n is not None
    assert 0.6 * closed < n["f_hz"] / 1e9 <= 1.02 * closed      # the pads load it: an upper bound
    blind = ev.study(*ev.synth(stub_spec(True)), 25e9, points=121, fine=0.1, threads=3, timeout=1500)
    assert ev.find_notch(blind["freqs"], blind["S"]) is None


@live
def test_live_palace_driven_agrees_with_openems(tmp_path):
    spec = coax_spec()
    fd = ev.study(*ev.synth(spec), 20e9, f_min=2e9, points=10, fine=0.1, symmetric=True, threads=3, timeout=1500)
    pal = ev.palace_run(spec, tmp_path, 2.0, 20.0, 2.0, fine=0.2, coarse=1.0, air=1.5, order=1, timeout=1500)
    a = ev.s21_db(fd["S"])
    for i, f in enumerate(pal["f_ghz"]):
        if f <= 10.0:
            assert abs(pal["s21_db"][i] - a[i]) < 1.0, f"{f} GHz"


def test_planes_run_into_the_boundary_but_a_board_edge_stays_open():
    # A plane that ends in open edges a few mm from the via is a patch
    # resonator: the deck rang for ns at a frequency set by its own region.
    geo, _ = ev.synth(coax_spec())
    from lib import emsolve as em
    csx = em.Csx()
    n = ev.plane_bleed(csx, geo)
    assert n == 2 * 4                                 # the two inner planes, four sides each
    boxes = next(b for kind, attrs, inner, b in csx.props if attrs["Name"] == "plane_bleed")
    assert {p1[2] for _, p1, _ in boxes} == {geo["stack"]["z"]["In1.Cu"], geo["stack"]["z"]["In2.Cu"]}
    # copper pulled back from the region's edge (a board outline) isn't extended
    spec = ev.LaunchSpec(stack=stack4(), layer="F.Cu", w=0.4, pad_w=1.2, pad_l=4.8, pad_in=2.7, gap=0.6,
                         antipad=0.35, gnd_y=2.55)
    lg, _ = ev.launch_synth(spec)
    csx = em.Csx()
    ev.plane_bleed(csx, lg)
    boxes = next(b for kind, attrs, inner, b in csx.props if attrs["Name"] == "plane_bleed")
    assert all(max(p1[0], p2[0]) > 0 for _, p1, p2 in boxes)      # nothing goes off toward the coax


def test_run_to_tail_stops_when_the_next_pass_wont_fit(monkeypatch, tmp_path):
    calls = []

    class R:
        stdout = stderr = ""

    def fake(tool, args, cwd, timeout):
        calls.append(timeout)
        (tmp_path / "p0u0").write_text("")
        return R()
    monkeypatch.setattr(solvers, "run", fake)
    monkeypatch.setattr(eb, "record_tail", lambda *a: 0.04)         # never rings down
    clock = iter(range(0, 1000000, 40))
    monkeypatch.setattr(ev.time, "time", lambda: float(next(clock)))

    class C:
        fdtd = {"NumberOfTimesteps": 1000}

        def xml(self):
            return ""
    rec = ev._run_to_tail(C(), {}, tmp_path, 300.0, 1)
    assert not rec["rang_down"]
    assert 1 <= len(calls) <= 4 and all(t <= 300.0 for t in calls)


def test_feed_posts_sit_exactly_on_mesh_lines():
    """A zero-width wire even 1e-7 mm off a line is an "unused primitive" in
    openEMS: the post silently isn't there."""
    csx, _, _ = ev.build(*ev.synth(stub_spec()), 25e9, 0, fine=0.12)
    posts = [b for _, attrs, _, boxes in csx.props if "feed_posts" in str(attrs) for b in boxes]
    assert len(posts) > 2
    for _, p1, p2 in posts:
        assert p1[0] in csx.lines["x"] and p1[1] in csx.lines["y"]
        assert p1[2] in csx.lines["z"] and p2[2] in csx.lines["z"]
