"""#481 antenna decks and #371 check_antenna for a board's own antenna copper.

The fast half is the deck and readers, on hand-written files. The live half
(test-slow, @pytest.mark.slow) runs openEMS and nf2ff on the known answers."""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import check_antenna  # noqa: E402
from lib import emantenna as ea  # noqa: E402
from lib import emsolve as em  # noqa: E402
from lib import solvers  # noqa: E402

needs_openems = pytest.mark.skipif(
    solvers.tool_path("openems") is None or solvers.tool_path("nf2ff") is None,
    reason="no openEMS tree")

F = 1e9
LAM = ea.C0 / F * 1e3


def dipole_geo(length=LAM / 2, w=2.0, g=2.0):
    arms = [("L1", "arm", ea._rect(-length / 2, -w / 2, -g / 2, w / 2)),
            ("L1", "arm", ea._rect(g / 2, -w / 2, length / 2, w / 2))]
    return ea.free_geo({"L1": 0.0}, copper=arms, grounds=()), ea.Feed(-g / 2, 0, "L1", None, g / 2, 0, w)


def patch_geo(W=38.0, L=29.5, h=1.6, er=4.4, G=70.0, feed_x=-6.0, tand=None):
    """kb/em-solvers.md's patch: L along x, W along y, on a G x G board."""
    return (ea.free_geo({"F.Cu": h, "B.Cu": 0.0}, slabs=[(0.0, h, er, tand)],
                        copper=[("F.Cu", "patch", ea._rect(-L / 2, -W / 2, L / 2, W / 2)),
                                ("B.Cu", "GND", ea._rect(-G / 2, -G / 2, G / 2, G / 2))],
                        outline=ea._rect(-G / 2, -G / 2, G / 2, G / 2)),
            ea.Feed(feed_x, 0.0, "F.Cu", "B.Cu"))


# ---------------------------------------------------------------- the deck

def test_feed_parse_both_forms():
    a = ea.Feed.parse("3,4")
    assert (a.x, a.y, a.layer, a.ref, a.planar) == (3.0, 4.0, "F.Cu", None, False) and a.axis == 2
    b = ea.Feed.parse("3,4,F.Cu,In1.Cu")
    assert b.ref == "In1.Cu" and not b.planar
    c = ea.Feed.parse("3,4,5,4,B.Cu,1.5")
    assert c.planar and c.axis == 0 and c.layer == "B.Cu" and c.w == 1.5
    with pytest.raises(ValueError):
        ea.Feed.parse("3,4,5,6").axis


def test_pulse_with_dc_is_refused():
    geo, feed = dipole_geo()
    with pytest.raises(ValueError, match="DC"):
        ea.deck(geo, feed, 1e9, 0.8e9, [1e9])


def test_deck_has_pml_nf2ff_and_a_port_in_the_air():
    geo, feed = dipole_geo()
    csx, where = ea.deck(geo, feed, F, F / 2, [F], fine=2.0)
    xml = csx.xml()
    assert 'xmin="PML_8"' in xml and 'zmax="PML_8"' in xml
    for face in ea.FACES:
        assert f'Name="nf2ff_E_{face}"' in xml and f'Name="nf2ff_H_{face}"' in xml
    assert xml.count("<FD_Samples>") == 12 and 'DumpType="10"' in xml and 'DumpType="11"' in xml
    lines = {a: sorted(csx.lines[a]) for a in "xyz"}
    # nothing but air in the PML, and the box clear of the structure
    for a, (lo, hi) in zip("xy", [(-LAM / 4, LAM / 4), (-1, 1)]):
        assert lines[a][ea.PML_CELLS] < lo and lines[a][-ea.PML_CELLS - 1] > hi
    assert not csx.sheets_off_mesh()


def test_halving_fine_really_refines_the_antenna():
    """ae4cf's trap on emboard (#616): 0.4 -> 0.2 went 80x63 -> 88x74. Here
    the cell over the antenna's copper is the fine asked for."""
    geo, feed = dipole_geo()
    cuts = []
    for fine in (2.0, 1.0):
        csx, _ = ea.deck(geo, feed, F, F / 2, [F], fine=fine)
        xs = sorted(csx.lines["x"])
        inner = [q - p for p, q in zip(xs, xs[1:]) if -60 < p and q < 60]
        assert max(inner) <= fine * 1.4     # merge_lines leaves up to 4/3 by a pinned line
        cuts.append(len([x for x in xs if -LAM / 4 <= x <= LAM / 4]))
    assert cuts[1] > 1.7 * cuts[0]


def test_the_dielectric_is_the_outline_not_the_domain():
    geo, feed = patch_geo()
    csx, where = ea.deck(geo, feed, 2.4e9, 1.2e9, [2.4e9], fine=1.0)
    xml = csx.xml()
    assert "<LinPoly" in xml and 'Length="1.6"' in xml
    assert where["feed_ref"] == "B.Cu"


def test_a_lossy_deck_has_sheets_and_a_conductivity():
    geo, feed = patch_geo()
    csx, _ = ea.deck(geo, feed, 2.4e9, 1.2e9, [2.4e9], fine=1.0, loss=ea.Loss(tand=0.02))
    xml = csx.xml()
    assert "<ConductingSheet" in xml and 'Conductivity="58000000.0"' in xml
    kappa = 2 * math.pi * 2.4e9 * em.EPS0 * 4.4 * 0.02
    assert f'Kappa="{round(kappa, 12)!r}"' in xml


def test_a_feed_with_no_ground_under_it_is_refused():
    geo, _ = patch_geo(G=70.0)
    with pytest.raises(ValueError, match="no ground copper"):
        ea.deck(geo, ea.Feed(100.0, 100.0, "F.Cu"), 2.4e9, 1.2e9, [2.4e9], fine=1.0)


def test_cell_limit():
    geo, feed = dipole_geo()
    with pytest.raises(ValueError, match="over the limit"):
        ea.deck(geo, feed, F, F / 2, [F], fine=0.5, max_cells=1000)


def _write_probe(path, values):
    path.write_text("".join(f"{i * 1e-11:.6e}\t{v:.6e}\n" for i, v in enumerate(values)))


def test_steps_needed_reads_the_decay_of_the_record(tmp_path):
    """e^-(i/50) over 400 samples ends at 3.4e-4 of its peak: already rung
    down, or nearly, in a short record it takes the decay's own steps."""
    vals = [math.exp(-i / 50) for i in range(200)]     # last tenth starts at 3.4e-2 ... 1.8e-2
    _write_probe(tmp_path / "ut", vals)
    _write_probe(tmp_path / "it", vals)
    # 200 samples for 2000 steps: 1/e per 500 steps; 3.4e-2 -> 1e-3 is 3.5 e-folds
    got = ea._steps_needed(tmp_path, 2000)
    assert 2000 + 3.5 * 500 < got < 1.3 * (2000 + 3.6 * 500)


def test_steps_needed_with_nothing_to_read_doubles(tmp_path):
    _write_probe(tmp_path / "ut", [1.0] * 200)
    _write_probe(tmp_path / "it", [1.0] * 200)
    assert ea._steps_needed(tmp_path, 2000) == 4000


def test_patch_tm010_skips_the_low_q_box_modes():
    modes = [(2.19, 0.8), (2.30, 55.0), (3.5, 0.8), (4.4, 226.0)]
    assert ea.patch_tm010(modes) == (2.30, 55.0)


def test_nf2ff_input_names_all_faces_in_radians():
    text = ea.nf2ff_xml({"nf2ff_center": [0.0, 0.0, 0.0]}, [1e9], 45, 90)
    assert text.count("<Planes") == 6 and 'freq="1000000000.0"' in text
    th = [float(v) for v in text.split("<theta>")[1].split("</theta>")[0].split(",")]
    assert len(th) == 5 and abs(th[-1] - math.pi) < 1e-9


def test_s11_features_and_touchstone():
    fs = [2.0e9 + 0.01e9 * i for i in range(101)]
    # a series RLC resonant at 2.5 GHz: 50 ohm, Q 25
    z = [50 + 1j * 50 * 25 * (f / 2.5e9 - 2.5e9 / f) for f in fs]
    ft = ea.s11_features(fs, z)
    assert abs(ft["f_res"] - 2.5e9) < 1e6 and ft["s11_db"] < -60
    # -10 dB where |Gamma| = 0.316: |X| = 2 R 0.333, df = f0 0.333 / Q
    half = 2.5e9 * 0.3333 / 25
    assert abs(ft["bw_10db_hz"] - 2 * half) < 2.1e7
    assert abs(ft["x_zero_up"][0] - 2.5e9) < 2e6
    assert ea.touchstone(fs[:2], z[:2]).splitlines()[0] == "# Hz S RI R 50"


def _res(f_res, s11, width, eff=0.9, conv=None):
    return {"features": {"f_res": f_res, "s11_db": s11, "bw_10db_hz": width}, "band": [2.4e9, 2.5e9],
            "far": [{"f": 2.45e9, "efficiency": eff}], "rung_down": True, "tail": 1e-4,
            **({"convergence": conv} if conv else {})}


def test_findings_against_band_floor_and_mesh():
    assert ea.findings(_res(2.45e9, -20, 1e8), (2.4e9, 2.5e9), 0.5) == []
    # off the band by less than its own width: still in reach
    assert ea.findings(_res(2.55e9, -20, 1e8), (2.4e9, 2.5e9)) == []
    kinds = {f["kind"] for f in ea.findings(_res(2.7e9, -20, 1e8), (2.4e9, 2.5e9))}
    assert kinds == {"antenna_detuned"}
    kinds = {f["kind"] for f in ea.findings(_res(2.45e9, -6, 0.0), (2.4e9, 2.5e9))}
    assert kinds == {"antenna_detuned"}
    kinds = {f["kind"] for f in ea.findings(_res(2.45e9, -20, 1e8, eff=0.3), (2.4e9, 2.5e9), 0.5)}
    assert kinds == {"antenna_low_efficiency"}
    bad = {"converged": False, "moved": {"f_res_pct": 4}}
    assert {f["kind"] for f in ea.findings(_res(2.45e9, -20, 1e8, conv=bad))} == {"antenna_not_converged"}


# ---------------------------------------------------------------- #371

def _pcb(tmp_path, body: str, name="ant.kicad_pcb") -> Path:
    p = tmp_path / name
    p.write_text(f"""(kicad_pcb (version 20260206) (generator "test")
  (general (thickness 1.6))
  (layers (0 "F.Cu" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user))
  (setup)
  (net 1 "ANT") (net 2 "GND") (net 3 "SIG")
  (gr_rect (start 0 0) (end 60 40) (stroke (width 0.1)) (fill no)
    (layer "Edge.Cuts"))
{body})
""", encoding="utf-8")
    return p


def _tr(x0, y0, x1, y1, net, layer="F.Cu", w=1.0):
    return (f'  (segment (start {x0} {y0}) (end {x1} {y1}) (width {w}) (layer "{layer}") '
            f'(net {net}))\n')


def _gnd(y0=10, y1=40, layer="B.Cu"):
    pts = f"(pts (xy 0 {y0}) (xy 60 {y0}) (xy 60 {y1}) (xy 0 {y1}))"
    return (f'  (zone (net 2) (net_name "GND") (layer "{layer}") (polygon {pts})\n'
            f'    (filled_polygon (layer "{layer}") {pts}))\n')


IFA = (_tr(10, 4, 30, 4, 1) + _tr(10, 4, 10, 10.5, 1) + _tr(13, 4, 13, 10.5, 1))
ROW = {"name": "A4", "nets": ["ANT"], "layer": "F.Cu", "feed": [13, 10.2], "short": [10, 10.2],
       "ground_nets": ["GND"]}


def _check(pcb, *rows):
    c = pcb.parent / "c.json"
    c.write_text(json.dumps({"pcb_antennas": list(rows)}))
    payload, _ = check_antenna.run(["--pcb", str(pcb), "--constraints", str(c)])
    return payload, {v["kind"] for v in payload["violations"]}


def test_clear_ifa_passes_and_reports_its_measures(tmp_path):
    pay, kinds = _check(_pcb(tmp_path, IFA + _gnd()), {**ROW, "edge_max_mm": 4.0,
                                                       "ground_edge": {"height_mm": 6.5, "tol_mm": 0.3}})
    assert kinds == set(), pay["violations"]
    fact = pay["checked"][0]["pcb_antennas"][0]
    assert fact["height_mm"] == pytest.approx(6.5, abs=0.05)
    assert fact["edge_mm"] == pytest.approx(3.5, abs=0.01)
    assert "sim_solve" in " ".join(pay["not_judged"])


@pytest.mark.parametrize("body,layer", [
    (_gnd(y0=5), "B.Cu"),                        # ground under the arm
    (_gnd() + _tr(15, 7, 25, 7, 3, "B.Cu"), "B.Cu"),   # a track under it
    (_gnd() + _tr(15, 7, 25, 7, 3), "F.Cu"),     # a track beside it, in the box
])
def test_other_copper_in_the_keepout_fails(tmp_path, body, layer):
    pay, kinds = _check(_pcb(tmp_path, IFA + body), ROW)
    assert kinds == {"pcb_antenna_copper"}
    assert {v["layer"] for v in pay["violations"]} == {layer}
    assert all(v["severity"] == "error" for v in pay["violations"])


def test_keepout_margin_reaches_a_near_track(tmp_path):
    body = IFA + _gnd() + _tr(32, 2, 32, 8, 3)
    assert _check(_pcb(tmp_path, body), ROW)[1] == set()
    assert _check(_pcb(tmp_path, body), {**ROW, "keepout_mm": 3.0})[1] == {"pcb_antenna_copper"}


def test_feed_off_the_copper_and_open_short(tmp_path):
    pcb = _pcb(tmp_path, IFA + _gnd())
    pay, kinds = _check(pcb, {**ROW, "feed": [20, 20]})
    assert "pcb_antenna_feed" in kinds
    assert any("feed point" in v["msg"] for v in pay["violations"])
    pcb = _pcb(tmp_path, IFA + _gnd(y0=15, y1=40))
    pay, kinds = _check(pcb, ROW)
    assert kinds == {"pcb_antenna_feed"} and "short does not reach" in pay["violations"][0]["msg"]


def test_height_and_board_edge(tmp_path):
    pcb = _pcb(tmp_path, IFA + _gnd())
    assert _check(pcb, {**ROW, "ground_edge": {"height_mm": 5.0}})[1] == {"pcb_antenna_ground_edge"}
    assert _check(pcb, {**ROW, "edge_max_mm": 1.0})[1] == {"pcb_antenna_inboard"}


def test_a_row_naming_no_copper_is_an_error(tmp_path):
    pay, kinds = _check(_pcb(tmp_path, _gnd()), {**ROW, "nets": ["NOPE"]})
    assert kinds == {"pcb_antenna_missing"}


REF = {"source": "test ref", "anchor": [3.0, 6.5], "ground_mm": [30.0, 60.0],
       "polys": [[[-0.5, 3.5], [20.5, 3.5], [20.5, 4.5], [-0.5, 4.5]],
                 [[-0.5, 4.0], [0.5, 4.0], [0.5, 11.0], [-0.5, 11.0]],
                 [[2.5, 4.0], [3.5, 4.0], [3.5, 11.0], [2.5, 11.0]]]}


def test_copied_geometry_same_and_different(tmp_path):
    row = {**ROW, "reference": REF, "feed": [13, 10.5], "short": [10, 10.5]}
    # feed (13, 10.5) sits where the anchor should: shift the reference to it
    ref = {**REF, "anchor": [3.0, 10.5]}
    pay, kinds = _check(_pcb(tmp_path, IFA + _gnd()), {**row, "reference": ref})
    assert kinds == set(), pay["violations"]
    assert pay["checked"][0]["pcb_antennas"][0]["geometry_diff_pct"] < 5
    short_arm = _tr(10, 4, 24, 4, 1) + _tr(10, 4, 10, 10.5, 1) + _tr(13, 4, 13, 10.5, 1)
    pay, kinds = _check(_pcb(tmp_path, short_arm + _gnd()), {**row, "reference": ref})
    assert kinds == {"pcb_antenna_geometry"}
    assert pay["violations"][0]["diff_pct"] > 5


def test_small_ground_against_the_reference(tmp_path):
    row = {**ROW, "reference": {**REF, "ground_mm": [80.0, 100.0], "polys": None}}
    assert _check(_pcb(tmp_path, IFA + _gnd()), row)[1] == {"pcb_antenna_ground_small"}


def test_named_reference_is_read_and_an_unknown_name_refused(tmp_path):
    pcb = _pcb(tmp_path, IFA + _gnd())
    pay, _ = _check(pcb, {**ROW, "reference": "ti-an043"})
    assert "AN043" in pay["checked"][0]["pcb_antennas"][0]["reference"]
    c = tmp_path / "c.json"
    c.write_text(json.dumps({"pcb_antennas": [{**ROW, "reference": "nope"}]}))
    with pytest.raises(check_antenna.checklib.CheckError, match="nope"):
        check_antenna.run(["--pcb", str(pcb), "--constraints", str(c)])


def _sim(tmp_path, f_res=2.45e9, s11=-20, eff=0.8, conv=True):
    p = tmp_path / "sim.json"
    p.write_text(json.dumps({"features": {"f_res": f_res, "s11_db": s11, "bw_10db": [f_res - 5e7, f_res + 5e7]},
                             "band": [2.4e9, 2.5e9], "at_band": {"efficiency": eff},
                             "convergence": {"converged": conv}}))
    return p


def test_the_solve_is_judged_when_given(tmp_path):
    pcb = _pcb(tmp_path, IFA + _gnd())
    good = {**ROW, "sim": str(_sim(tmp_path)), "band_hz": [2.4e9, 2.5e9], "min_efficiency": 0.5}
    assert _check(pcb, good)[1] == set()
    assert _check(pcb, {**good, "sim": str(_sim(tmp_path, f_res=2.9e9))})[1] == {"pcb_antenna_detuned"}
    assert _check(pcb, {**good, "sim": str(_sim(tmp_path, eff=0.2))})[1] == {"pcb_antenna_low_efficiency"}
    assert _check(pcb, {**good, "sim": str(_sim(tmp_path, conv=False))})[1] == {"pcb_antenna_sim_unconverged"}
    pay, kinds = _check(pcb, {**good, "sim": "missing.json"})
    assert kinds == set() and "missing.json not found" in " ".join(pay["not_judged"])


def test_modules_still_work_and_nothing_to_judge_names_both(tmp_path):
    pay, _ = check_antenna.run(["--pcb", str(_pcb(tmp_path, _gnd()))])
    assert "pcb_antennas" in pay["nothing_to_judge"]


# ---------------------------------------------------------------- known answers (live)
# Tolerances are set from the runs recorded in docs/solvers.md.

@needs_openems
@pytest.mark.slow
def test_live_half_wave_dipole_directivity_and_z(tmp_path):
    """Half-wave strip dipole, w 2 mm (a = w/4 = 0.5 mm), in free space. Read
    at resonance, where a dipole this thick has about 70 ohm at L a little under
    lam/2; at L = lam/2 it is 90 + j50, not the thin wire's 73 + j42. D 2.15 dBi.
    A lossless deck radiates what it accepts, which is the check on the boundary
    and on the NF2FF scale together. Runs in 3 min on the loaded tower: 2 mm
    936.1 MHz, 70.4 ohm; 1 mm 939.1 MHz, 70.3 ohm, D within 0.01 dB."""
    geo, feed = dipole_geo()
    r = ea.study(geo, feed, (0.8e9, 1.2e9), pattern=[0.95e9], fine=2.0)
    assert r["rung_down"]
    row = r["far"][0]
    assert row["dmax_dbi"] == pytest.approx(2.15, abs=0.1)
    assert row["efficiency"] == pytest.approx(1.0, abs=0.02)
    ft = r["features"]
    assert ft["z_res"][0] == pytest.approx(73.0, rel=0.1)
    assert abs(ft["z_res"][1]) < 15
    assert ft["x_zero_up"][0] == pytest.approx(0.94e9, rel=0.015)


@needs_openems
@pytest.mark.slow
def test_live_patch_tm010_against_the_cavity_formula_and_mesh(tmp_path):
    """kb/em-solvers.md's patch: 2.394 GHz by cavity + Hammerstad, 2.328 by
    its own openEMS run. 2 mm and 1 mm land on the same frequency sample,
    2.332 GHz, S11 -15 dB. Before the thirds cells were kept clear (#622) these
    meshes read 2.226 and 2.274 GHz, so this is the test of that too."""
    geo, feed = patch_geo()
    r = ea.converge(geo, feed, (2.1e9, 2.6e9), fine=2.0, ratio=2.0, pattern=[2.3e9, 2.35e9, 2.4e9])
    f = r["features"]["f_res"]
    assert f == pytest.approx(2.394e9, rel=0.04)
    assert f == pytest.approx(2.328e9, rel=0.01)
    assert r["convergence"]["converged"], r["convergence"]
    assert ea.at_band(r)["dmax_dbi"] > 5.0


@pytest.mark.slow
@pytest.mark.skipif(solvers.tool_path("palace") is None or solvers.tool_path("gmsh") is None,
                    reason="no Palace or gmsh")
def test_live_palace_patch_is_a_second_solver(tmp_path):
    """Palace eigenmode on the quarter patch, order 2: 2.293 GHz, Q 55 at 2.5 mm,
    2.302 GHz, Q 55 at 1.6 mm, within 1.3 % of openEMS's 2.332 GHz S11 dip."""
    r = ea.palace_patch(tmp_path, lc=2.5)
    f, q = ea.patch_tm010(r["modes"])
    assert f == pytest.approx(2.332, rel=0.025)
    assert 30 < q < 100


def test_resolve_grounds_takes_the_sheet_slash_and_refuses_a_missing_net():
    nets = {"/GND_A9", "/A9_FEED", "GND"}
    assert ea._resolve_grounds(("GND_A9",), nets) == ("/GND_A9",)
    assert ea._resolve_grounds(("/GND",), nets) == ("GND",)
    with pytest.raises(ValueError, match="no copper"):
        ea._resolve_grounds(("GND_A8",), nets)


def test_areal_keeps_only_the_parts_a_geos_overlay_can_take():
    from shapely.geometry import GeometryCollection, LineString, box
    mixed = GeometryCollection([box(0, 0, 1, 1), LineString([(2, 0), (3, 0)])])
    assert check_antenna._areal(mixed).difference(box(0, 0, 0.5, 1)).area == pytest.approx(0.5)
