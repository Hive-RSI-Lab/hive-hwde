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

import cmath
import re
import json
import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

from shapely.geometry import Polygon

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
    d = where["ports"][1]["dist"]
    assert d[1] - d[0] == pytest.approx(d[2] - d[1])    # equal spacing: port_vi's formula needs it
    assert d[0] < d[1] < d[2] < 0                       # out from the face: the probes are on the board's side of it
    box = {a["Name"]: b for _, a, _, b in m_csx.props}
    for j in range(3):
        x = box[f"p1u{j}"][0][1][0]
        assert d[j] == pytest.approx(where["ports"][1]["x"] - x)     # the jack's outside is toward -x
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


def test_a_launch_reads_s_at_the_jack_face_from_probes_on_the_board_side():
    """A known reciprocal S at the two planes; a line's probes outside its plane
    and a coax's inside its. Two loads on the coax, as launch_study has them."""
    import numpy as np
    freqs = [1e9 * i for i in range(1, 7)]
    f_max = 6e9
    z = [54.0, 50.6]
    eps = [3.0, 2.16]
    dist = [[4.5, 6.0, 7.5], [-5.8, -5.2, -4.6]]
    where = {"ports": [{"dist": dist[0]}, {"dist": dist[1]}]}

    def beta(k, f):
        return 2 * math.pi * f * math.sqrt(eps[k]) / eb.C0

    def s_of(f):
        p = 0.8 * np.exp(-1j * f * 2e-10)
        return [[0.2 + 0.1j, p], [p, -0.1 + 0.05j]]

    def read(g2):
        out = [([], []), ([], [])]
        for f in freqs:
            s = s_of(f)
            b2 = s[1][0] / (1 - s[1][1] * g2)      # a1 = 1 at 50 ohm
            a2 = g2 * b2
            waves = [(1.0, s[0][0] + s[0][1] * a2), (a2, b2)]
            for k, (a50, b50) in enumerate(waves):
                v0, i0 = a50 + b50, (a50 - b50) / 50.0
                ain, aout = (v0 + z[k] * i0) / 2, (v0 - z[k] * i0) / 2    # going in; coming out
                g = 1j * beta(k, f)
                dk = [q * 1e-3 for q in dist[k]]
                mids = [(dk[0] + dk[1]) / 2, (dk[1] + dk[2]) / 2]
                u = [ain * cmath.exp(g * q) + aout * cmath.exp(-g * q) for q in dk]
                c = [(ain * cmath.exp(g * q) - aout * cmath.exp(-g * q)) / z[k] for q in mids]
                out[k][0].append(u)
                out[k][1].append(c)
        return [tuple([[row[j] for row in rows] for j in range(n)] for rows, n in ((o[0], 3), (o[1], 2))) for o in out]
    reads = {0: read(0.02), 1: read(-0.95)}
    runs, _, _, _ = ev._solve_feeds(reads, where, freqs, f_max, every_run=True)
    got = eb.sparams(runs, 2)
    for m, f in enumerate(freqs):
        s = s_of(f)
        for i in range(2):
            for j in range(2):
                assert abs(got[m][i][j] - s[i][j]) < 2e-3, f"{f} S{i + 1}{j + 1}"
    with pytest.raises(ValueError, match="rise"):
        eb.feed_vi(reads[0][1], dist[1][::-1], freqs, {"z": [z[1]] * 6, "eps_eff": [eps[1]] * 6})


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


@live
def test_live_launch_reads_a_matched_coax_and_a_passive_launch():
    spec = ev.LaunchSpec(stack=ev.stack_named("JLC04161H-1080B"), layer="F.Cu", w=0.4544, pad_w=1.2, pad_l=4.8, pad_in=2.7,
                         gap=0.6, antipad=0.35, gnd_y=2.55, run=7.0)
    res = ev.launch_study(spec, 6e9, f_min=2e8, points=59, threads=3, timeout=1800)
    assert all(r["rang_down"] for r in res["runs"])
    assert all(r["unused_prims"] == 0 for r in res["runs"])
    for z in res["z_coax"][len(res["z_coax"]) // 4:]:
        assert z.real == pytest.approx(50.0, rel=0.05)
    assert res["imbalance"] < 0.01                           # what balance() took out
    assert ev.power_lost(res["freqs"], res["S"])["worst"] < 0.03     # 1.2 % per launch at 6 GHz, any mesh
    for f, s in zip(res["freqs"], res["S"]):
        assert abs(s[1][0]) ** 2 + abs(s[0][0]) ** 2 < 1.005, f"{f / 1e9} GHz"      # passive
        assert abs(s[1][0]) > 0.9, f"{f / 1e9} GHz"
        assert abs(s[0][0]) < 0.2, f"{f / 1e9} GHz"


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


def test_launch_coax_bore_is_round_with_no_air_in_its_corners():
    """A square bore read 55 ohm at every mesh, against the 50 its radii give."""
    spec = ev.LaunchSpec(stack=ev.stack_named("JLC04161H-1080B"), layer="F.Cu", w=0.4544, pad_w=1.2, pad_l=4.8, pad_in=2.7,
                         gap=0.6, antipad=0.35, gnd_y=2.55, run=7.0)
    csx, _, st = ev.launch_build(spec, 6e9, refine=0.5)
    r_o, x_end = spec.sma["r_o"], st["x_end"]
    z_ax = st["z_top"] + spec.sma["pin_r"]
    boxes = next(q for _, n, _, q in csx.props if n["Name"] == "sma")
    def inside(p, x, y, z):
        lo, hi = p[1], p[2]
        return all(min(a, b) <= v <= max(a, b) for a, b, v in zip(lo, hi, (x, y, z)))
    # the square's corner, outside r_o, inside the bore a tube would leave
    c = 0.9 * r_o
    assert math.hypot(c, c) > r_o
    assert any(inside(p, x_end / 2, c, z_ax + c) for p in boxes if p[0] != "cyl")


def test_launch_planes_bleed_at_the_sides_but_not_at_the_board_edge():
    """Tied only at two launch vias, planes ending inside the domain ring for ns and grow under MUR."""
    spec = ev.LaunchSpec(stack=stack4(), layer="F.Cu", w=0.45, pad_w=1.2, pad_l=4.8, pad_in=2.7, gap=0.6,
                         antipad=0.35, gnd_y=2.55, run=7.0)
    csx, _, _ = ev.launch_build(spec, 6e9)
    boxes = next(b for kind, attrs, inner, b in csx.props if attrs["Name"] == "plane_bleed")
    assert boxes
    assert not [b for b in boxes if min(b[1][0], b[2][0]) < -1e-6 and abs(b[1][1]) < spec.half_y and abs(b[2][1]) < spec.half_y]
    assert any(max(b[1][0], b[2][0]) > spec.run for b in boxes)


def test_launch_short_is_a_plate_inside_the_domain_not_a_sheet_on_its_face():
    # On the MUR face the plate does nothing: both runs read the same, and S = B A^-1 is singular.
    spec = ev.LaunchSpec(stack=stack4(), layer="F.Cu", w=0.4, pad_w=1.2, pad_l=4.8, pad_in=2.7, gap=0.6,
                         antipad=0.35, gnd_y=2.55)
    ctx = ev.launch_build(spec, 6e9, shorted=True, refine=0.5)
    csx = ctx[0]
    xs = sorted(csx.lines["x"])
    plate = [b for kind, attrs, inner, boxes in csx.props for b in boxes if b[0] == 70]
    assert len(plate) == 1
    a, b = sorted((plate[0][1][0], plate[0][2][0]))
    assert a == pytest.approx(xs[0]) and b == pytest.approx(xs[1], abs=1e-6)
    open_csx = ev.launch_build(spec, 6e9, shorted=False, refine=0.5)[0]
    assert not [b for kind, attrs, inner, boxes in open_csx.props for b in boxes if b[0] == 70]


def test_a_line_drawn_as_a_zone_is_a_port():
    # zcoupon draws its lines as zones, not tracks: 0 tracks, and from_board saw no line.
    from shapely.geometry import box
    strip = SimpleNamespace(fills={"F.Cu": [box(-20, -0.2272, 0.3, 0.2272)]})
    back = SimpleNamespace(fills={"B.Cu": [box(-0.3, -0.2272, 20, 0.2272)]})
    bg = SimpleNamespace(tracks_of=lambda net: [], zones_of=lambda net: [strip, back])
    ports, why = ev._edge_crossings(bg, "/L", (-3, -3, 3, 3))
    assert why == ""
    assert sorted((p.dir, p.layer, p.x, p.y, p.w) for p in ports) == [
        ("+x", "B.Cu", 3, 0, 0.4544), ("-x", "F.Cu", -3, 0, 0.4544)]
    # A taper at the edge is no line to feed: refused, and says so.
    taper = SimpleNamespace(fills={"F.Cu": [Polygon([(-20, -1), (-3, -.2), (0, -.2), (0, .2), (-3, .2), (-20, 1)])]})
    bg = SimpleNamespace(tracks_of=lambda net: [], zones_of=lambda net: [taper, back])
    ports, why = ev._edge_crossings(bg, "/L", (-3, -3, 3, 3))
    assert ports == [] and "straight" in why


def test_palace_sheets_cannot_take_a_tag_occ_gave_a_volume_face():
    """Disk(38) on a face OCC had numbered 38 was lost, and with it a port: Palace
    then knew no attribute 11. gmsh writes the mesh anyway, exiting 1."""
    for spec in (coax_spec(), stub_spec()):
        geo = ev.palace_geo(spec)
        tags = [int(t) for t in re.findall(r"^(?:Disk|Rectangle|BooleanDifference)\((\d+)\)", geo, re.M)]
        assert tags and min(tags) > ev.SHEET_TAG
        assert len(tags) == len(set(tags))


def test_palace_run_refuses_a_mesh_from_a_geo_with_errors(tmp_path, monkeypatch):
    from lib import solvers

    def fake(tool, args, cwd, timeout):
        (Path(cwd) / "via.msh").write_text("$MeshFormat")
        return SimpleNamespace(returncode=1, stdout="Error: surface with tag 42 already exists", stderr="")
    monkeypatch.setattr(solvers, "run", fake)
    with pytest.raises(solvers.SolverError, match="tag 42"):
        ev.palace_run(coax_spec(), tmp_path, 2.0, 10.0, 2.0)


def test_palace_return_pad_keeps_out_of_a_barrel_running_through_it():
    """A sheet across a barrel's void leaves triangles on no tet: MFEM aborts in STable3D."""
    spec = stub_spec()
    geo = ev.palace_geo(spec)
    x, y = spec.returns[0][:2]
    assert re.search(rf"Disk\(\d+\) = \{{{x},{y},{spec.stack['z'][spec.out_layer]},{spec.ret_drill / 2}\}};", geo)


def test_power_lost_is_what_the_two_ports_do_not_account_for():
    from lib import emvia as ev
    a = ev.power_lost([1e9, 2e9], [[[0.1, 0.99], [0.99, 0.1]], [[0.3, 0.8], [0.8, 0.3]]])
    assert a["ghz"] == 2.0
    assert abs(a["worst"] - (1 - 0.09 - 0.64)) < 1e-4
    assert ev.power_lost([1e9], [[[0.6, 0.8], [0.8, 0.6]]])["worst"] == 0.0


def test_power_lost_and_balance_see_through_a_scale_error_between_two_ports():
    # Measured on the launch: |S21| .984, |S12| 1.002 at 4.4 GHz, and |S21|^2 read
    # that as 2.5 % lost on one mesh and 1.65 % on the next; the product held .72 %.
    from lib import emvia as ev
    th = 0.3
    s21 = complex(math.cos(th), -math.sin(th)) * 0.99
    true = [[0.1 + 0j, s21], [s21, 0.1j]]
    k = 0.98
    bent = [[true[0][0], s21 / k], [s21 * k, true[1][1]]]
    want = ev.power_lost([1e9], [true])["worst"]
    assert ev.power_lost([1e9], [bent])["worst"] == pytest.approx(want, abs=1e-4)
    fixed, off = ev.balance([bent])
    assert fixed[0][1][0] == pytest.approx(s21) and fixed[0][0][1] == pytest.approx(s21)
    assert off == pytest.approx(k * k - 1, abs=1e-6) or off == pytest.approx(1 - k * k, abs=1e-6)
    # and the branch of the root follows S21 when its phase is past 90 degrees
    s21 = complex(-0.8, -0.5)
    assert ev.balance([[[0j, s21 / k], [s21 * k, 0j]]])[0][0][1][0] == pytest.approx(s21)