"""lib/emboard.py (#455): a board's copper as an openEMS N-port deck.

Hermetic: the stack's z from the board's stackup, extraction per layer and
net (barrels, lumped parts), the deck's guards against the three openEMS
traps hit on hw-lab vna-verify (sheet planes off the mesh are refused, MUR
not PML, a pulse without DC), ports found from the copper, the readers on
synthetic probe data, and the closed forms.

Live (test-slow, skipped without the IIC-OSIC tree): known answers on a
synthetic 2-layer coupon, 0.5 mm of er 4.4 under a 50 ohm (quasi-static)
line:
  - a 30 mm thru matches the ABCD of the line with impedance.py's Z0 and
    eps_eff: |S21| within 0.1 dB, Z0 back from S within 3%, |S11| under
    -25 dB from f_max/10 to f_max; the S21 phase within 1.5% of beta*l and
    eps_eff within 2.5% from f_max/5 (below, beta*l is too small to read);
  - an open stub 13 mm from the line's centre notches within 2% of
    c / (4 (L + dL_open) sqrt(eps_eff));
  - a planted mutant (the line drawn twice as wide) reads Z0 more than 20%
    off 50 ohm: the thru's own check fails on it.
"""
from __future__ import annotations

import cmath
import json
import math
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import sim_solve  # noqa: E402
from lib import emboard as eb  # noqa: E402
from lib import emsolve as em  # noqa: E402
from lib import impedance as imp  # noqa: E402
from lib import solvers  # noqa: E402

HAVE_TREE = solvers.toolchain_root() is not None
live = pytest.mark.skipif(not HAVE_TREE, reason="no IIC-OSIC-TOOLS tree")

H, ER = 0.5, 4.4
W50 = 0.9592          # impedance.solve_width(50, 0.5, 1e-4, 4.4, mask=None)


def coupon(path: Path, w=W50, length=30.0, stub=None, ymid=10.0, extra="") -> list:
    """A microstrip thru on F.Cu over a filled B.Cu ground (2 layers, H of
    ER), optionally with an open stub `stub` mm long from the line's centre
    at its middle. Returns the region to cut."""
    segs = [f'(segment (start 0 {ymid}) (end {length} {ymid}) (width {w}) (layer "F.Cu") (net 1))']
    if stub:
        segs.append(f'(segment (start {length / 2} {ymid}) (end {length / 2} {ymid + stub}) '
                    f'(width {w}) (layer "F.Cu") (net 1))')
    y1 = ymid + (stub or 0) + 10
    fill = f'(pts (xy -10 0) (xy {length + 10} 0) (xy {length + 10} {y1}) (xy -10 {y1}))'
    path.write_text(f'''(kicad_pcb (version 20240108) (generator test)
  (general (thickness {H + 0.07}))
  (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user))
  (setup (stackup (layer "F.Cu" (type "copper") (thickness 0.035))
    (layer "dielectric 1" (type "core") (thickness {H}) (material "FR4") (epsilon_r {ER}))
    (layer "B.Cu" (type "copper") (thickness 0.035))))
  (net 0 "") (net 1 "RF") (net 2 "GND")
  {chr(10).join(segs)}
  {extra}
  (zone (net 2) (net_name "GND") (layer "B.Cu") (hatch edge 0.5)
    (connect_pads (clearance 0.2)) (min_thickness 0.2)
    (fill yes) (polygon {fill})
    (filled_polygon (layer "B.Cu") {fill}))
)
''')
    return [0, 0, length, y1]


def stub_coupon(path: Path, w=2.853, h=1.53, er=4.5, length=10.0, stub=16.07) -> list:
    """A square-ended open stub `stub` mm from the line's edge, as rf-kit's F5
    is drawn: a line `length` long on another laminate, the stub a filled zone
    of the line's net (a track's round end would add w/2 of arc)."""
    global H, ER
    keep = H, ER
    H, ER = h, er
    x0, x1, y0, y1 = length / 2 - w / 2, length / 2 + w / 2, 10 + w / 2, 10 + w / 2 + stub
    pts = f'(pts (xy {x0} {y0}) (xy {x1} {y0}) (xy {x1} {y1}) (xy {x0} {y1}))'
    extra = (f'(zone (net 1) (net_name "RF") (layer "F.Cu") (hatch edge 0.5) (fill yes) (polygon {pts}) '
             f'(filled_polygon (layer "F.Cu") {pts}))')
    try:
        reg = coupon(path, w=w, length=length, extra=extra)
    finally:
        H, ER = keep
    return [reg[0], reg[1], reg[2], y1 + 6]


CPWG4 = '''(kicad_pcb (version 20240108) (generator test)
  (general (thickness 1.6))
  (layers (0 "F.Cu" signal) (1 "In1.Cu" signal) (2 "In2.Cu" signal) (31 "B.Cu" signal)
    (44 "Edge.Cuts" user))
  (setup (stackup (layer "F.Cu" (type "copper") (thickness 0.035))
    (layer "dielectric 1" (type "prepreg") (thickness 0.2) (epsilon_r 3.9))
    (layer "In1.Cu" (type "copper") (thickness 0.015))
    (layer "dielectric 2" (type "core") (thickness 1.0) (epsilon_r 4.4))
    (layer "In2.Cu" (type "copper") (thickness 0.015))
    (layer "dielectric 3" (type "prepreg") (thickness 0.2) (epsilon_r 3.9))
    (layer "B.Cu" (type "copper") (thickness 0.035))))
  (net 0 "") (net 1 "RF") (net 2 "GND")
  (segment (start 0 5) (end 20 5) (width 0.4) (layer "F.Cu") (net 1))
  (via (at 8 3.6) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net 2))
  (via (at 12 6.4) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net 2))
  (footprint "t:R0402" (layer "F.Cu") (at 15 8)
    (property "Reference" "R1")
    (pad "1" smd rect (at -0.5 0) (size 0.5 0.6) (layers "F.Cu") (net 1 "RF"))
    (pad "2" smd rect (at 0.5 0) (size 0.5 0.6) (layers "F.Cu") (net 2 "GND")))
  (zone (net 2) (net_name "GND") (layer "F.Cu") (hatch edge 0.5) (fill yes)
    (polygon (pts (xy 0 0) (xy 20 0) (xy 20 10) (xy 0 10)))
    (filled_polygon (layer "F.Cu") (pts (xy 0 0) (xy 20 0) (xy 20 4.45) (xy 0 4.45)))
    (filled_polygon (layer "F.Cu") (pts (xy 0 5.55) (xy 14 5.55) (xy 14 10) (xy 0 10)))
    (filled_polygon (layer "F.Cu") (pts (xy 16 5.55) (xy 20 5.55) (xy 20 10) (xy 16 10))))
  (zone (net 2) (net_name "GND") (layer "In1.Cu") (hatch edge 0.5) (fill yes)
    (polygon (pts (xy 0 0) (xy 20 0) (xy 20 10) (xy 0 10)))
    (filled_polygon (layer "In1.Cu") (pts (xy 0 0) (xy 20 0) (xy 20 10) (xy 0 10))))
  (zone (net 2) (net_name "GND") (layer "B.Cu") (hatch edge 0.5) (fill yes)
    (polygon (pts (xy 0 0) (xy 20 0) (xy 20 10) (xy 0 10)))
    (filled_polygon (layer "B.Cu") (pts (xy 0 0) (xy 20 0) (xy 20 10) (xy 0 10))))
)
'''


@pytest.fixture
def cpwg4(tmp_path):
    p = tmp_path / "cpwg.kicad_pcb"
    p.write_text(CPWG4)
    return p


# ---------------------------------------------------------------- stack, extract

def test_stack_z_from_the_board_stackup(cpwg4):
    g = eb.extract(cpwg4, [0, 0, 20, 10])
    st = eb.Stack.from_dict(g["stack"])
    assert st.layers == ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]
    assert st.z == pytest.approx({"B.Cu": 0.0, "In2.Cu": 0.2, "In1.Cu": 1.2, "F.Cu": 1.4})
    assert [s[2] for s in st.slabs] == [3.9, 4.4, 3.9]
    assert st.gap("F.Cu", "In1.Cu") == pytest.approx(0.2)
    assert st.er_between("F.Cu", "B.Cu") == pytest.approx((0.2 * 3.9 * 2 + 1.0 * 4.4) / 1.4)


def test_extract_copper_per_layer_and_net_barrels_and_parts(cpwg4):
    g = eb.extract(cpwg4, [0, 0, 20, 10], parts={"R1": 49.9})
    keys = {(c["layer"], c["net"]) for c in g["copper"]}
    assert {("F.Cu", "RF"), ("F.Cu", "GND"), ("In1.Cu", "GND"), ("B.Cu", "GND")} <= keys
    in2 = [c for c in g["copper"] if c["layer"] == "In2.Cu"]
    assert len(in2) == 2 and all(c["net"] == "GND" for c in in2)     # the vias' rings only
    assert len(g["drills"]) == 2 and all(d["top"] == "F.Cu" and d["bot"] == "B.Cu" for d in g["drills"])
    (r,) = g["lumped"]
    assert r["axis"] == "x" and r["lo"] == pytest.approx(14.75) and r["hi"] == pytest.approx(15.25)
    assert r["w"] == pytest.approx(0.6) and r["r"] == 49.9 and r["layer"] == "F.Cu"
    json.dumps(g)       # plain data


# ---------------------------------------------------------------- deck

def _deck(cpwg4, **kw):
    g = eb.extract(cpwg4, [0, 0, 20, 10], parts={"R1": 49.9})
    return eb.deck(g, ["0,5,-x", "20,5,+x"], 6e9, **kw)


def test_deck_pins_every_sheet_plane_and_refuses_one_off_the_mesh(cpwg4):
    csx, where = _deck(cpwg4)
    zs = sorted(csx.lines["z"])
    for z in (0.0, 0.2, 1.2, 1.4):
        assert any(abs(v - z) < 1e-9 for v in zs)
    assert csx.sheets_off_mesh() == []
    root = ET.fromstring(csx.xml())
    elev = {float(p.get("Elevation")) for p in root.iter("Polygon")}
    assert elev == {1.4, 1.2, 0.2, 0.0}
    # trap 1: a sheet whose plane lost its mesh line is refused, not dropped
    csx.lines["z"].discard(1.2)
    assert ("copper", "z", 1.2) in csx.sheets_off_mesh()
    with pytest.raises(ValueError, match="off every mesh line"):
        csx.xml()


def test_deck_uses_mur_and_a_pulse_without_dc(cpwg4):
    csx, _ = _deck(cpwg4)
    root = ET.fromstring(csx.xml())
    bc = root.find(".//BoundaryCond").attrib
    assert not any("PML" in v for v in bc.values())                  # trap 2
    assert bc["zmin"] == "PEC" and bc["xmin"] == "MUR"
    ex = root.find(".//Excitation").attrib
    assert ex["Type"] == "10" and float(ex["f0"]) == 6e9            # trap 3: f0 = f_max
    fn = ex["Function"]
    # the derivative of a Gaussian integrates to zero: no DC
    tau = 1 / (2 * math.pi * 6e9 / 2.5)
    vals = [eval(fn.replace("^", "**").replace("exp", "math.exp"), {"math": math, "t": k * tau / 50})
            for k in range(0, 1000)]
    assert abs(sum(vals)) < 1e-5 * sum(abs(v) for v in vals)   # the pulse starts at 5 tau
    assert max(vals) > 0.5


def test_ports_find_net_width_reference_and_carry_the_feed(cpwg4):
    csx, where = _deck(cpwg4)
    p0, p1 = where["ports"]
    assert p0["net"] == "RF" and p0["w"] == pytest.approx(0.4, abs=0.01)
    assert p0["ref"] == "In1.Cu" and p0["h"] == pytest.approx(0.2) and p0["er"] == pytest.approx(3.9)
    root = ET.fromstring(csx.xml())
    # the feed carries F.Cu signal + coplanar ground and the In1 plane out
    # past the region: copper beyond x < 0 on F.Cu and In1
    def reaches(z):
        return min(float(v.get("X1")) for p in root.iter("Polygon") if float(p.get("Elevation")) == z
                   for v in p.iter("Vertex"))
    assert reaches(1.4) < -5 and reaches(1.2) < -5
    names = {e.get("Name") for e in root.iter() if e.get("Name")}
    assert {"src0", "port0", "port1", "R_R1", "barrels"} <= names and "src1" not in names
    assert {f"p{k}{t}{j}" for k in (0, 1) for t, n in (("u", 3), ("i", 2)) for j in range(n)} <= names
    # coplanar ground stitched to In1 along each feed, plus the 2 vias
    barrels = [b for p in root.iter("Metal") if p.get("Name") == "barrels" for b in p.iter("Box")]
    assert len(barrels) > 2 + 4
    for pr in where["ports"]:
        d = pr["dist"]
        assert d[1] - d[0] == pytest.approx(d[2] - d[1], abs=1e-5)


def test_port_errors_name_the_problem(cpwg4):
    g = eb.extract(cpwg4, [0, 0, 20, 10])
    with pytest.raises(ValueError, match="crosses the region edge"):
        eb.deck(g, ["0,4.6,-x", "20,5,+x"], 6e9)      # in the gap beside the strip
    with pytest.raises(ValueError, match="DIR"):
        eb.Port.parse("0,5,x")


def test_mesh_smoothing_keeps_pins_and_grades():
    ls = eb.merge_lines([0, 0.01, 0.5, 0.52, 3], 0.05, pinned=[0.52])
    assert 0.52 in ls and 0.5 not in ls and 0.01 not in ls
    sm = eb.smooth_lines([0, 0.1, 0.2, 10], 1.0, 1.4)
    d = [b - a for a, b in zip(sm, sm[1:])]
    assert max(d) <= 1.0 + 1e-9
    assert all(b <= 1.4 * a + 1e-6 for a, b in zip(d, d[1:]))


# ---------------------------------------------------------------- readers

def _write_probe(path, phasor, f, t):
    import numpy as np
    v = np.real(phasor * np.exp(2j * math.pi * f * t)) * np.hanning(t.size)
    path.write_text("% t v\n" + "\n".join(f"{x:.6e}\t{y:.12e}" for x, y in zip(t, v)))


def test_port_vi_moves_v_and_i_to_the_plane_whatever_the_feed_z(tmp_path):
    """Probe data of a 30 ohm feed carrying a known incident and reflected
    wave: V and I come back at the plane, and the 50 ohm waves from them."""
    import numpy as np
    f, eps, zf = 2e9, 3.0, 30.0
    g = 1j * 2 * math.pi * f * math.sqrt(eps) / em.C0
    a, b = 1.0 * cmath.exp(0.3j), 0.4 * cmath.exp(-1.0j)
    t = np.arange(0, 60e-9, 2e-12)
    s = [4.5, 6.0, 7.5]
    where = {"ports": [{"dist": s}]}
    for j, sj in enumerate(s):
        x = sj * 1e-3
        _write_probe(tmp_path / f"p0u{j}", a * cmath.exp(g * x) + b * cmath.exp(-g * x), f, t)
    for j, sj in enumerate([5.25, 6.75]):
        x = sj * 1e-3
        _write_probe(tmp_path / f"p0i{j}", (a * cmath.exp(g * x) - b * cmath.exp(-g * x)) / zf, f, t)
    (r,) = eb.port_vi(tmp_path, where, 0, [f])
    assert abs(r["z"]) == pytest.approx(zf, rel=1e-3)
    # (the DFT of a windowed record scales both alike)
    assert abs(r["i"] / r["v"] - (a - b) / zf / (a + b)) < 1e-5
    ((a50, b50),), = eb.waves(tmp_path, where, [f])
    gl = ((a + b) - 50 * (a - b) / zf) / ((a + b) + 50 * (a - b) / zf)
    assert abs(b50 / a50 - gl) < 1e-3


def test_port_vi_gamma_survives_a_tail_at_a_small_beta_d(tmp_path):
    """#585: a 600 MHz wave over 1.5 mm probes (beta*d 0.03) with a little
    noise. A second difference of the voltages reads noise like this as a
    loss of order 1 /m on a lossless line, and sigma_max 1.002 on a thru."""
    import numpy as np
    f, eps, zf = 0.6e9, 3.34, 50.0
    g = 1j * 2 * math.pi * f * math.sqrt(eps) / em.C0
    t = np.arange(0, 40e-9, 2e-11)
    env = np.hanning(t.size)    # long enough that the -f image stays out of the DFT
    s = [4.5, 6.0, 7.5]
    where = {"ports": [{"dist": s}]}
    rng = np.random.default_rng(585)

    def write(name, phasor, scale=1.0):
        v = np.real(phasor * np.exp(2j * math.pi * f * t)) * env + 1e-3 * scale * rng.standard_normal(t.size)
        (tmp_path / name).write_text("% t v\n" + "\n".join(f"{x:.6e}\t{y:.12e}" for x, y in zip(t, v)))

    for j, sj in enumerate(s):
        write(f"p0u{j}", cmath.exp(g * sj * 1e-3))
    for j, sj in enumerate([5.25, 6.75]):
        write(f"p0i{j}", cmath.exp(g * sj * 1e-3) / zf, 1 / zf)     # as in a record: the same to both
    (r,) = eb.port_vi(tmp_path, where, 0, [f])
    assert r["gamma"].imag == pytest.approx(g.imag, rel=0.01)   # acosh: 4 % off


def test_deck_sets_its_own_run_length(cpwg4):
    """#585: openEMS tests endCriteria only every 4 s of wall clock, so the
    energy can't be what ends a run, or the load picks the record's length."""
    a, _ = _deck(cpwg4)
    assert a.fdtd["endCriteria"] == eb.RUN_ALL_STEPS
    assert 'endCriteria="1e-300"' in a.xml()
    assert a.fdtd["NumberOfTimesteps"] < eb.STEPS_MAX
    assert a.fdtd["NumberOfTimesteps"] == _deck(cpwg4)[0].fdtd["NumberOfTimesteps"]


def test_record_steps_cover_the_pulse_and_the_trips():
    lines = {"x": [0.0, 40.0], "y": [0.0, 30.0], "z": [0.0, 1.0]}
    n = eb.record_steps(lines, 6e9, 4.0)
    dt = 1e-3 / (em.C0 * math.sqrt(1 / 40 ** 2 + 1 / 30 ** 2 + 1))
    want = 10 * 2.5 / (2 * math.pi * 6e9) + eb.RECORD_TRANSITS * 50e-3 * 2 / em.C0
    assert n * dt == pytest.approx(want, rel=1e-3)
    assert eb.record_steps({**lines, "z": [0.0, 1e-3]}, 6e9, 4.0) == eb.STEPS_MAX


def _ring(path, tail):
    """Port probes that peak at 1 and hold `tail` in the record's last tenth."""
    for k in (0, 1):
        for j in range(3):
            rows = [(i * 1e-11, 1.0 if i == 5 else (tail if i >= 90 else 0.0)) for i in range(100)]
            (path / f"p{k}u{j}").write_text("% t v\n" + "\n".join(f"{a:.6e}\t{b:.12e}" for a, b in rows))


def test_record_tail(tmp_path):
    where = {"ports": [{}, {}]}
    _ring(tmp_path, 2e-3)
    assert eb.record_tail(tmp_path, where) == pytest.approx(2e-3, rel=1e-6)


def test_study_doubles_a_record_that_is_still_ringing(tmp_path, monkeypatch):
    pcb = tmp_path / "t.kicad_pcb"
    geo = eb.extract(pcb, coupon(pcb))
    seen = []

    def run(tool, args, cwd=None, timeout=None):
        seen.append(int(re.search(r'NumberOfTimesteps="(\d+)"', (Path(cwd) / "deck.xml").read_text()).group(1)))
        _ring(Path(cwd), 1e-2 if len(seen) == 1 else 1e-5)

    class Done(Exception):
        pass

    def read_feed(*a, **k):
        raise Done

    monkeypatch.setattr(solvers, "run", run)
    monkeypatch.setattr(eb, "read_feed", read_feed)
    with pytest.raises(Done):
        eb.study(geo, ["0,10,-x", "30,10,+x"], 6e9, 0.6e9, 3, symmetric=True)
    assert seen == [seen[0], 2 * seen[0]]


def _stubdeck(tmp_path, **kw):
    pcb = tmp_path / "s.kicad_pcb"
    geo = eb.extract(pcb, stub_coupon(pcb))
    return eb.deck(geo, ["0,10,-x", "10,10,+x"], 5e9, **kw), geo


def test_no_line_inside_a_thirds_cell(tmp_path):
    """#616: a line inside the cell around a conductor's edge widens the sheet
    (rf-kit's F5 notch read 2.319 GHz for 2.49)."""
    for r in (1.0, 1.5, 2.25):
        (csx, where), _ = _stubdeck(tmp_path, refine=r)
        f = where["fine"]
        ys = sorted(csx.lines["y"])
        for edge, s_in in ((10 - 2.853 / 2, 1), (10 + 2.853 / 2, -1), (10 + 2.853 / 2 + 16.07, -1)):
            a, b = sorted((edge + s_in * f / 3, edge - s_in * 2 * f / 3))
            assert any(abs(v - a) < 2e-6 for v in ys) and any(abs(v - b) < 2e-6 for v in ys), (r, edge)
            assert not [v for v in ys if a + 1e-5 < v < b - 1e-5], (r, edge)


def test_refine_refines_every_axis(tmp_path):
    (_, base), geo = _stubdeck(tmp_path)
    prev = base["mesh"]
    for r in (1.5, 2.25):
        _, w = eb.deck(geo, ["0,10,-x", "10,10,+x"], 5e9, refine=r)
        assert all(q > p for q, p in zip(w["mesh"], prev)), (r, w["mesh"], prev)
        assert w["fine"] == pytest.approx(base["fine"] / r)
        prev = w["mesh"]
    assert 5 < prev[0] * prev[1] * prev[2] / (base["mesh"][0] * base["mesh"][1] * base["mesh"][2]) < 20


def _probes_on_a_line(z0, ee, dist, freqs, a, b, sg=1.0):
    """What a feed's probes read of waves a (in) and b (out) at its plane."""
    s = [q * 1e-3 for q in dist]
    si = [(s[0] + s[1]) / 2, (s[1] + s[2]) / 2]
    us = [[], [], []]
    ii = [[], []]
    for n, f in enumerate(freqs):
        g = 1j * 2 * math.pi * f * math.sqrt(ee) / em.C0
        for j, q in enumerate(s):
            us[j].append(a[n] * cmath.exp(g * q) + b[n] * cmath.exp(-g * q))
        for j, q in enumerate(si):
            ii[j].append(sg * (a[n] * cmath.exp(g * q) - b[n] * cmath.exp(-g * q)) / z0)
    return us, ii


@pytest.mark.parametrize("sg", [1.0, -1.0])
def test_fit_feed_and_feed_vi_recover_the_line_and_its_waves(sg):
    freqs = [0.1e9 + 0.05e9 * k for k in range(100)]
    dist = [6.0, 9.0, 12.0]
    a = [1.0 + 0.1j] * 100
    b = [0.3 * cmath.exp(0.4j * k) for k in range(100)]
    read = _probes_on_a_line(52.0, 3.5, dist, freqs, a, b, sg)
    line = eb.fit_feed([read], dist, freqs, freqs[-1])
    assert line["z"] == pytest.approx([52.0] * 100, rel=1e-3)
    assert line["eps_eff"] == pytest.approx([3.5] * 100, rel=1e-3)
    vi = eb.feed_vi(read, dist, freqs, line)
    for n in (20, 50, 90):
        assert vi[n]["v"] == pytest.approx(a[n] + b[n], rel=1e-2)
        assert vi[n]["i"] == pytest.approx((a[n] - b[n]) / 52.0, rel=1e-2)


def test_fit_feed_is_not_moved_by_a_null():
    """#616: the far port at a notch. The wave nearly vanishes over a band and
    what is left is not the line; the per-frequency ratios read nonsense
    there, and the fit must not follow them."""
    import random
    rnd = random.Random(1)
    freqs = [0.1e9 + 0.025e9 * k for k in range(200)]
    dist = [6.0, 9.0, 12.0]
    a = [0j] * 200
    b = [1.0 - 0.999 * math.exp(-(((f - 2.5e9) / 0.2e9) ** 2)) + 0j for f in freqs]
    us, ii = _probes_on_a_line(50.0, 3.6, dist, freqs, a, b)
    noise = lambda: 3e-3 * complex(rnd.gauss(0, 1), rnd.gauss(0, 1))
    us = [[v + noise() for v in u] for u in us]
    ii = [[v + noise() / 50 for v in i] for i in ii]
    line = eb.fit_feed([(us, ii)], dist, freqs, freqs[-1])
    assert max(abs(v - 50.0) for v in line["z"][40:]) < 1.0
    assert max(abs(v - 3.6) for v in line["eps_eff"][40:]) < 0.05


def _driven_and_far(far_gain):
    """A feed on a 52 ohm line. Driven it stands near a stopband, its ratios
    reading a different line; `far_gain(f)` is the wave reaching the far feed."""
    freqs = [0.625e9 + 0.025e9 * k for k in range(176)]
    dist = [6.0, 9.0, 12.0]
    driven = _probes_on_a_line(38.0, 3.0, dist, freqs, [1.0 + 0j] * 176, [0.7 + 0j] * 176)
    far = _probes_on_a_line(52.0, 3.5, dist, freqs, [0j] * 176, [far_gain(f) + 0j for f in freqs])
    return freqs, dist, driven, far


def test_feed_lines_prefers_the_undriven_run():
    freqs, dist, driven, far = _driven_and_far(lambda f: 1.0)
    lines = eb.feed_lines({0: [driven, far], 1: [far, driven]}, [dist, dist], freqs, freqs[-1])
    for ln in lines:
        assert ln["z"] == pytest.approx([52.0] * 176, rel=1e-3)
        assert ln["eps_eff"] == pytest.approx([3.5] * 176, rel=1e-3)


def test_feed_lines_gives_a_mirror_one_line():
    freqs, dist, driven, far = _driven_and_far(lambda f: 1.0)
    a, b = eb.feed_lines({0: [driven, far]}, [dist, dist], freqs, freqs[-1], symmetric=True)
    assert a["z"] == pytest.approx([52.0] * 176, rel=1e-3)
    assert b["z"] == a["z"]


def test_feed_lines_excited_once_without_a_mirror_keeps_each_ports_own_read():
    """Not symmetric: the feeds may differ, so the far line can't serve port 0."""
    freqs, dist, driven, far = _driven_and_far(lambda f: 1.0)
    a, b = eb.feed_lines({0: [driven, far]}, [dist, dist], freqs, freqs[-1])
    assert a["z"] == pytest.approx([38.0] * 176, rel=1e-3)
    assert b["z"] == pytest.approx([52.0] * 176, rel=1e-3)


def test_feed_lines_does_not_trust_a_far_feed_with_a_sliver_of_band():
    """A bandpass: the far feed only sees the line over 10 % of the band, so
    its fit can't stand alone. All runs go in, as fit_feed would take them."""
    freqs, dist, driven, far = _driven_and_far(lambda f: 1.0 if 2.5e9 < f < 3.0e9 else 1e-5)
    lines = eb.feed_lines({0: [driven, far], 1: [far, driven]}, [dist, dist], freqs, freqs[-1])
    want = eb.fit_feed([far, driven], dist, freqs, freqs[-1])
    assert lines[0]["z"] == pytest.approx(want["z"])
    assert lines[1]["z"] == pytest.approx(want["z"])


def _notch_s(freqs, f0):
    s21 = [(f - f0) / (f - f0 + 0.1e9j) for f in freqs]
    s11 = [v - 1 for v in s21]
    return [[[a, b], [b, a]] for a, b in zip(s11, s21)]


def test_compare_and_notches_known_answers():
    freqs = [1e9 + 1e7 * k for k in range(301)]
    a = _notch_s(freqs, 2.4e9)
    assert eb.notches(freqs, [s[1][0] for s in a]) == pytest.approx([2.4e9])
    same = eb.compare(freqs, a, a)
    assert (same["ds"], same["notch_pct"]) == (0.0, 0.0)
    moved = eb.compare(freqs, _notch_s(freqs, 2.3e9), a)
    assert moved["notch_pct"] == pytest.approx(100 * 0.1 / 2.4, rel=1e-4)
    gone = [[[0j, 1 + 0j], [1 + 0j, 0j]] for _ in freqs]
    assert eb.compare(freqs, gone, a)["notch_pct"] == 100.0
    assert eb.compare(freqs, a, gone)["notch_pct"] == 100.0


def test_study_converged_stops_when_two_runs_agree_and_fails_when_they_do_not(monkeypatch):
    freqs = [1e9 + 1e7 * k for k in range(301)]
    f0 = {1.0: 2.3e9, 1.5: 2.40e9, 2.25: 2.401e9, 3.375: 2.45e9}
    calls = []

    def study(geo, ports, f_max, f_min, points, refine=1.0, **kw):
        calls.append(refine)
        return {"freqs": freqs, "S": _notch_s(freqs, f0[refine]),
                "runs": [{"cells": int(1000 * refine ** 3), "mesh": [1, 1, 1], "seconds": 1.0}]}
    monkeypatch.setattr(eb, "study", study)
    res = eb.study_converged({}, [], 5e9)
    assert calls == [1.0, 1.5, 2.25]
    assert res["convergence"]["ok"] and res["S"] == _notch_s(freqs, 2.401e9)
    calls.clear()
    res = eb.study_converged({}, [], 5e9, refine_max=1.5)
    assert calls == [1.0, 1.5] and not res["convergence"]["ok"]
    calls.clear()
    res = eb.study_converged({}, [], 5e9, refine=2.25, refine_max=3.375)
    assert calls == [2.25, 3.375] and not res["convergence"]["ok"]


def test_sparams_from_waves_full_and_symmetric():
    import numpy as np
    S = np.array([[0.2 + 0.1j, 0.8 - 0.3j], [0.8 - 0.3j, 0.2 + 0.1j]])
    # run e: a = drive on e plus a little reflected from the other port's end
    runs = {}
    for e in (0, 1):
        a = np.array([1.0, 0.07j]) if e == 0 else np.array([0.05, 1.0 + 0j])
        b = S @ a
        runs[e] = [[(complex(a[k]), complex(b[k])) for k in range(2)]]
    full = eb.sparams(runs, 2)[0]
    assert np.allclose(full, S)
    sym = eb.sparams({0: runs[0]}, 2, symmetric=True)[0]
    assert np.allclose(sym, S)
    with pytest.raises(ValueError):
        eb.sparams({0: runs[0]}, 2)


# ---------------------------------------------------------------- closed forms

def test_line_s_and_line_from_s_round_trip():
    freqs = [0.5e9 * k for k in range(1, 13)]
    S = eb.line_s(freqs, 38.0, 3.1, 40.0)
    for s in S:      # lossless and reciprocal
        assert abs(s[0][0]) ** 2 + abs(s[1][0]) ** 2 == pytest.approx(1.0)
    back = eb.line_from_s(freqs, S, 40.0)
    assert all(r["z0"] == pytest.approx(38.0, rel=1e-9) for r in back)
    assert all(r["eps_eff"] == pytest.approx(3.1, rel=1e-9) for r in back)


def test_notch_and_open_end():
    ee = 3.331
    assert eb.notch_f(13.0, ee) == pytest.approx(em.C0 / (4 * 13e-3 * math.sqrt(ee)))
    # Hammerstad-Bekkadal: w/h ~1.9 on er 4.4 -> about 0.39 h
    assert eb.open_end_dl(W50, H, ee) / H == pytest.approx(0.39, abs=0.01)


def test_field_cpwg_limits():
    ms = imp.field_microstrip(0.43, 0.2444, 1e-4, 3.91, mask=None)
    wide = imp.field_cpwg(0.43, 20.0, 0.2444, 1e-4, 3.91)
    assert wide["z0"] == pytest.approx(ms["z0"], rel=1e-3)
    cp = imp.field_cpwg(0.43, 0.25, 0.2444, 1e-4, 3.91)
    # hw-lab vna-verify's bare CPWG: 53.06 ohm in its coupons.json
    assert cp["z0"] == pytest.approx(53.06, abs=0.1)
    assert cp["z0"] < ms["z0"] and cp["eps_eff"] < ms["eps_eff"]


def test_cli_board_deck_only(tmp_path):
    pcb = tmp_path / "t.kicad_pcb"
    reg = coupon(pcb)
    out = tmp_path / "d"
    rc = sim_solve.main(["board", "--pcb", str(pcb), "--region", ",".join(map(str, reg)),
                         "--port", "0,10,-x", "--port", "30,10,+x", "--symmetric",
                         "--deck-only", str(out), "--json"])
    assert rc == 0
    assert (out / "port0" / "deck.xml").exists() and not (out / "port1").exists()
    w = json.loads((out / "port0" / "where.json").read_text())
    assert w["ports"][0]["w"] == pytest.approx(W50, abs=1e-3) and w["floor"] == "pec"


# ---------------------------------------------------------------- live

F_MAX = 6e9
FREQS = [F_MAX * k / 20 for k in range(2, 21)]


def _run(tmp_path, name, points=len(FREQS), **kw):
    pcb = tmp_path / f"{name}.kicad_pcb"
    reg = coupon(pcb, **kw)
    geo = eb.extract(pcb, reg)
    res = eb.study(geo, ["0,10,-x", "30,10,+x"], F_MAX, FREQS[0], points, symmetric=True)
    if points == len(FREQS):
        assert res["freqs"] == pytest.approx(FREQS)
    return res


def _thru_check(res, length=30.0) -> dict:
    qs = imp.field_microstrip(W50, H, 1e-4, ER, mask=None)
    ref = eb.line_s(FREQS, qs["z0"], qs["eps_eff"], length)
    ex = eb.line_from_s(FREQS, res["S"], length)
    db = lambda v: 20 * math.log10(abs(v))
    out = {"s21_db": max(abs(db(s[1][0]) - db(r[1][0])) for s, r in zip(res["S"], ref)),
           "s11_db": max(db(s[0][0]) for s in res["S"]),
           "phase_pct": 0.0, "z0_pct": 0.0, "ee_pct": 0.0}
    for f, s, e in zip(FREQS, res["S"], ex):
        bl = 2 * math.pi * f * math.sqrt(qs["eps_eff"]) / em.C0 * length * 1e-3
        # At beta*l = n*pi the line is invisible and Z0 is 0/0: this thru reads
        # 51.1 and 53.4 at 0.99 and 1.97 pi, 50.4-50.9 at the other 17 points.
        if abs(math.sin(bl)) > 0.2:
            out["z0_pct"] = max(out["z0_pct"], abs(e["z0"] - 50.0) / 50.0 * 100)
        if f < F_MAX / 5:
            continue      # beta*l under ~70 deg: phase and eps_eff are ill-conditioned there
        dph = cmath.phase(s[1][0] / cmath.exp(-1j * bl))
        out["phase_pct"] = max(out["phase_pct"], abs(dph) / bl * 100)
        out["ee_pct"] = max(out["ee_pct"], abs(e["eps_eff"] - qs["eps_eff"]) / qs["eps_eff"] * 100)
    out["ok"] = (out["s21_db"] < 0.1 and out["s11_db"] < -25 and out["phase_pct"] < 1.5
                 and out["z0_pct"] < 3 and out["ee_pct"] < 2.5)
    return out


@live
def test_live_board_thru_matches_the_line_closed_form(tmp_path):
    res = _run(tmp_path, "thru")
    c = _thru_check(res)
    assert c["ok"], c
    # #483: a matched thru is passive, reciprocal and causal by P370's check
    from lib import sparam
    q = sparam.check(res["freqs"], res["S"])
    assert q["ok"], sparam.summary(q)


@live
def test_live_board_run_is_the_same_under_any_load(tmp_path):
    """#585: bit for bit, so what a run reads doesn't hang on the tower's load."""
    a = _run(tmp_path, "thru")
    b = _run(tmp_path, "thru")
    assert a["S"] == b["S"]
    assert a["runs"][0]["tail"] <= eb.TAIL_MAX


@live
def test_live_board_open_stub_notch(tmp_path):
    L = 13.0
    res = _run(tmp_path, "notch", points=271, stub=L)          # 20 MHz steps
    qs = imp.field_microstrip(W50, H, 1e-4, ER, mask=None)
    want = eb.notch_f(L + eb.open_end_dl(W50, H, qs["eps_eff"]), qs["eps_eff"])
    d = [20 * math.log10(abs(s[1][0])) for s in res["S"]]
    f = res["freqs"][d.index(min(d))]
    assert min(d) < -20
    assert f == pytest.approx(want, rel=0.02), (f, want)


@live
def test_live_board_stub_settles_under_refinement(tmp_path):
    """#616's close: on a thick wide stub like rf-kit's F5 (w/L is large, so the
    closed form is no help past +-5 %), the notch settles on a mesh refined in
    steps, and the S is passive at each one."""
    from lib import sparam
    pcb = tmp_path / "f5.kicad_pcb"
    w, h, er, L = 2.853, 1.53, 4.5, 16.07
    geo = eb.extract(pcb, stub_coupon(pcb, w, h, er, 10.0, L))
    res = eb.study_converged(geo, ["0,10,-x", "10,10,+x"], 5e9, 1.25e8, 401)
    conv = res["convergence"]
    assert conv["ok"], conv["steps"]
    assert sparam.check(res["freqs"], res["S"])["ok"]
    qs = imp.field_microstrip(w, h, 1e-4, er, mask=None)
    dl = eb.open_end_dl(w, h, qs["eps_eff"])
    lo = eb.notch_f(L + w / 2 + dl, qs["eps_eff"])   # the stub measured from the line's centre
    hi = eb.notch_f(L + dl, qs["eps_eff"])           # and from its edge
    found = conv["steps"][-1]["compare"]["notches"][0]["f"]
    assert lo < found < 1.04 * hi, (lo, found, hi)


@live
def test_live_board_mutant_wrong_width_misses_z0(tmp_path):
    c = _thru_check(_run(tmp_path, "mutant", w=2 * W50))
    assert not c["ok"] and c["z0_pct"] > 20, c
