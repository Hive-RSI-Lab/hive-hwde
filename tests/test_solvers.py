"""lib/solvers.py, lib/emsolve.py, lib/thermalfv.py and sim_solve.py.

Hermetic: the decks each solver reads are well formed and put their probes
where the reader looks; the output parsers read the solvers' real output
(samples copied from runs on the Hive's IIC-OSIC-TOOLS 2026.09 tree); the
loader command a tool runs under is built from the tree; the thermal and
electro-thermal solver meets its closed forms (sim_solve's own cases).

Live (skipped without the tree): every solver's validation case, the
full-wave ones in test-slow (tests/conftest.py SLOW).
"""
from __future__ import annotations

import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import sim_solve  # noqa: E402
from lib import emsolve as em  # noqa: E402
from lib import solvers  # noqa: E402

HAVE_TREE = solvers.toolchain_root() is not None
live = pytest.mark.skipif(not HAVE_TREE, reason="no IIC-OSIC-TOOLS tree")


# ---------------------------------------------------------------- runner

def _fake_tree(tmp_path: Path) -> Path:
    t = tmp_path / "iic"
    (t / "lib64").mkdir(parents=True)
    (t / "lib64/ld-linux-x86-64.so.2").write_text("")
    exe = t / solvers.TOOLS["openems"][0]
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    return t


def test_command_runs_through_the_image_loader(tmp_path, monkeypatch):
    t = _fake_tree(tmp_path)
    monkeypatch.setenv("HWDE_EDA_TOOLCHAIN", str(t))
    argv, env = solvers.command("openems", ["deck.xml"])
    assert argv[0] == str(t / "lib64/ld-linux-x86-64.so.2")
    assert argv[1] == "--library-path"
    libs = argv[2].split(":")
    # the image's real lapack/blas before the /etc/alternatives symlinks
    assert libs[0].endswith("openblas-pthread")
    assert argv[3:] == [str(t / solvers.TOOLS["openems"][0]), "deck.xml"]
    assert env["OPAL_PREFIX"] == str(t / "usr")
    assert env["OMPI_MCA_ess_singleton_isolated"] == "1"


def test_missing_tool_and_bad_override(tmp_path, monkeypatch):
    t = _fake_tree(tmp_path)
    monkeypatch.setenv("HWDE_EDA_TOOLCHAIN", str(t))
    assert solvers.tool_path("palace") is None
    assert solvers.version("palace") is None
    with pytest.raises(solvers.SolverError):
        solvers.command("palace", [])
    monkeypatch.setenv("HWDE_EDA_TOOLCHAIN", str(tmp_path / "nowhere"))
    with pytest.raises(solvers.SolverError):
        solvers.toolchain_root()


def test_validate_reports_missing_not_failed(tmp_path, monkeypatch):
    monkeypatch.setenv("HWDE_EDA_TOOLCHAIN", str(_fake_tree(tmp_path)))
    res = sim_solve.validate({"palace_cavity", "fasthenry_bar"})
    assert {c["status"] for c in res["cases"]} == {"missing"}
    assert res["ok"]
    assert not sim_solve.validate({"fasthenry_bar"}, strict=True)["ok"]


# ---------------------------------------------------------------- ngspice init (XSPICE)

def test_ngspice_gets_hwdes_spinit_naming_the_trees_code_models(tmp_path, monkeypatch):
    t = _fake_tree(tmp_path)
    exe = t / solvers.TOOLS["ngspice"][0]
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    lib = t / solvers.NGSPICE_LIB
    lib.mkdir(parents=True)
    for cm in ("digital.cm", "analog.cm", "tlines.cm"):
        (lib / cm).write_text("")
    (lib / "r3_cmc.osdi").write_text("")
    monkeypatch.setenv("HWDE_EDA_TOOLCHAIN", str(t))
    monkeypatch.setattr(solvers, "CACHE", tmp_path / "cache")
    _, env = solvers.command("ngspice", ["-b", "x.cir"])
    spinit = (Path(env["SPICE_SCRIPTS"]) / "spinit").read_text()
    loads = [ln.split(None, 1)[1] for ln in spinit.splitlines() if ln.startswith("codemodel ")]
    assert loads == [str(lib / c) for c in ("analog.cm", "digital.cm", "tlines.cm")]
    assert "codemodel /foss/" not in spinit and "osdi" not in spinit
    assert [p.name for p in solvers.codemodels(t)] == ["analog.cm", "digital.cm", "tlines.cm"]
    # other tools keep the tree's own scripts dir
    exe2 = t / solvers.TOOLS["xyce"][0]
    exe2.parent.mkdir(parents=True)
    exe2.write_text("")
    assert solvers.command("xyce", [])[1]["SPICE_SCRIPTS"].endswith("share/ngspice/scripts")


def test_xspice_decks_and_their_known_answers():
    logic = sim_solve.xspice_logic_deck()
    for model in ("adc_bridge", "d_and", "d_dff", "dac_bridge"):
        assert model in logic
    # the counter reads n mod 16 just after clock edge n
    assert {k: e for k, (_, e) in sim_solve.XS_CNT.items()} == {
        "cnt5": 5, "cnt11": 11, "cnt16": 0, "cnt21": 5}
    assert [e for _, e in sim_solve.XS_AND.values()] == [0, 0, 0, 1]
    sd = sim_solve.sigma_delta_deck(0.25)
    assert "vin in 0 dc 0.25" in sd and f"to={10 + sim_solve.SD_CLOCKS}u" in sd
    assert "pre_osdi /x/r.osdi" in sim_solve.osdi_deck("/x/r.osdi")
    # a synthetic engine that gets everything right passes; one bit wrong fails
    good = {k: e for k, (_, e) in {**sim_solve.XS_AND, **sim_solve.XS_CNT}.items()}
    good.update(tdr=6e-6, tqr=6e-6 + sim_solve.XS_DELAY, tdf=8e-6, tqf=8e-6 + sim_solve.XS_DELAY)

    def engine(meas):
        return lambda tag, deck: meas if tag == "logic" else {
            "dens": float(deck.split("vin in 0 dc ")[1].split()[0])}
    assert sim_solve._xspice_case("x", "ngspice", engine(good))["status"] == "pass"
    bad = dict(good, cnt16=16.0)
    c = sim_solve._xspice_case("x", "ngspice", engine(bad))
    assert c["status"] == "fail" and c["checks"][0]["measured"] == 7 / 8
    late = dict(good, tqr=good["tqr"] + 1e-6)   # one clock late
    assert sim_solve._xspice_case("x", "ngspice", engine(late))["status"] == "fail"


# ---------------------------------------------------------------- decks

def test_microstrip_deck_is_csxcad_xml_with_probes_on_the_mesh():
    csx, where = em.microstrip_line(1.5, 0.8, 4.0, f_max=4e9)
    root = ET.fromstring(csx.xml())
    assert root.tag == "openEMS"
    bc = root.find("FDTD/BoundaryCond").attrib
    assert bc["zmin"] == "PEC" and bc["xmax"] == "PML_8"
    probes = {p.get("Name"): p for p in root.iter("ProbeBox")}
    assert set(probes) == {"u1", "u2", "u3", "i1", "i2"}
    xs = [float(v) for v in root.find("ContinuousStructure/RectilinearGrid/XLines").text.split(",")]
    for x in where["x"]:                # voltage probes sit on E lines
        assert any(abs(x - m) < 1e-9 for m in xs)
    for x in where["xi"]:               # current probes halfway, on H lines
        assert not any(abs(x - m) < 1e-9 for m in xs)
    zs = [float(v) for v in root.find("ContinuousStructure/RectilinearGrid/ZLines").text.split(",")]
    assert sum(1 for z in zs if z <= 0.8 + 1e-9) - 1 >= 6     # cells across h
    assert where["cells"] < 400_000     # stays a minutes-long solve


def test_z0_from_probes_recovers_a_synthetic_line(tmp_path):
    """Write the probes a lossless 50 ohm line with a standing wave would
    give and check the reader returns 50 ohm and its eps_eff, whatever the
    reflection."""
    import numpy as np
    z0, eps, f = 50.0, 3.0, 1e9
    beta = 2 * math.pi * f * math.sqrt(eps) / em.C0
    A, B = 1.0, 0.4 * np.exp(0.7j)               # incident + a reflected wave
    t = np.arange(0, 200e-9, 5e-12)
    where = {"x": [10.0, 10.5, 11.0], "xi": [10.25, 10.75]}

    def write(name, phasor):
        v = np.real(phasor * np.exp(2j * math.pi * f * t)) * np.hanning(t.size)
        (tmp_path / name).write_text("% t v\n" + "\n".join(f"{a:.6e}\t{b:.9e}" for a, b in zip(t, v)))
    for n, x in zip(("u1", "u2", "u3"), where["x"]):
        xm = x * 1e-3
        write(n, A * np.exp(-1j * beta * xm) + B * np.exp(1j * beta * xm))
    for n, x in zip(("i1", "i2"), where["xi"]):
        xm = x * 1e-3
        write(n, (A * np.exp(-1j * beta * xm) - B * np.exp(1j * beta * xm)) / z0)
    row = em.microstrip_z0_from_probes(tmp_path, where, [f])[0]
    assert row["z0"] == pytest.approx(z0, rel=2e-3)
    assert row["eps_eff"] == pytest.approx(eps, rel=2e-3)


def test_cavity_modes_counts_te_and_tm():
    a, b, d = 40.0, 30.0, 20.0
    modes = em.cavity_modes(a, b, d, 4)
    # d is the short side, so TM110 (no field variation along z) is lowest
    tm110 = em.C0 / 2 * math.sqrt((1 / a) ** 2 + (1 / b) ** 2) * 1e3 / 1e9
    te101 = em.C0 / 2 * math.sqrt((1 / a) ** 2 + (1 / d) ** 2) * 1e3 / 1e9
    assert modes[0] == pytest.approx(tm110)
    assert modes[1] == pytest.approx(te101)
    assert modes[2] == pytest.approx(modes[3])     # TE111 / TM111


def test_palace_config_and_geo():
    cfg = em.palace_eigen_config("box.msh", 4, 5.0)
    assert cfg["Problem"]["Type"] == "Eigenmode"
    assert cfg["Boundaries"]["PEC"]["Attributes"] == [2]
    geo = em.box_mesh_geo(40, 30, 20, 6.0)
    assert "Box(1) = {0, 0, 0, 40, 30, 20}" in geo and "MshFileVersion = 2.2" in geo


ZC_SAMPLE = """Row 1:  n1  to  n2
Impedance matrix for frequency = 1000 1 x 1
    0.00492611  +4.38967e-05j
"""

FASTERCAP_SAMPLE = """Iteration number #2 ***************************
Capacitance matrix is:
Dimension 1 x 1
1  7.30197e-11
Weighted Frobenius norm of the difference between capacitance (auto option): 0.00178078
"""


def test_extraction_readers(tmp_path):
    (tmp_path / "Zc.mat").write_text(ZC_SAMPLE)
    f, z = em.read_zc(tmp_path / "Zc.mat")
    assert f == 1000 and z == complex(0.00492611, 4.38967e-05)
    assert em.read_fastercap("Capacitance matrix is:\nDimension 1 x 1\n1  1e-12\n" + FASTERCAP_SAMPLE) \
        == pytest.approx(7.30197e-11)
    deck = em.fasthenry_bar(10, 1, 0.035)
    assert ".units mm" in deck and "sigma=58000.0" in deck and ".external N1 N2" in deck
    cube = em.fastercap_cube(1.0, 2).splitlines()
    assert len(cube) == 1 + 6 * 4
    assert max(abs(float(v)) for ln in cube[1:] for v in ln.split()[2:]) == pytest.approx(1e-3)


def test_bar_inductance_formula():
    # Ruehli's partial self-inductance, 10 mm x 1 mm x 35 um: ~6.97 nH
    assert em.bar_partial_inductance(10, 1, 0.035) == pytest.approx(6.969e-9, rel=1e-3)


# ---------------------------------------------------------------- thermal

@pytest.mark.parametrize("name", ["thermal_fin", "thermal_point", "electrothermal"])
def test_thermal_cases_meet_their_closed_forms(name, tmp_path):
    c = sim_solve.CASES[name][1](tmp_path)
    assert c["status"] == "pass", c


def test_strip_selfheat_closed_forms_agree():
    from lib import thermalfv as th
    R0, hA, I = 0.01, 0.05, 10.0
    th_i = th.strip_selfheat(I, R0, hA)
    V = I * R0 * (1 + th.ALPHA_CU * th_i)          # the volts that carry I hot
    assert th.strip_selfheat_v(V, R0, hA) == pytest.approx(th_i, rel=1e-9)
    assert th.strip_selfheat(1e3, R0, hA) == math.inf       # runaway


def test_harmonic_mean_resolves_copper_edges():
    """A copper strip into bare FR4 conducts like the FR4 at the joint, not
    like the average of the two."""
    import numpy as np
    from lib import thermalfv as th
    ks = np.array([[th.K_CU * 35e-6] * 5 + [th.K_FR4 * 1.6e-3] * 5])
    fixed = np.full(ks.shape, np.nan)
    fixed[0, 0], fixed[0, -1] = 1.0, 0.0
    T = th.solve_plane(ks, 0.0, np.zeros(ks.shape), 1e-3, fixed=fixed)
    # nearly all the drop is across the FR4 half
    assert T[0, 4] > 0.95 and T[0, 5] < T[0, 4]


# ---------------------------------------------------------------- live

@live
@pytest.mark.parametrize("name", ["circuit_ngspice", "circuit_xyce", "fasthenry_bar",
                                  "fastercap_cube", "circuit_xspice", "circuit_osdi",
                                  "circuit_xspice_kicad"])
def test_live_solver_validates(name):
    res = sim_solve.validate({name})
    c = res["cases"][0]
    assert c["status"] == "pass", c


@live
def test_live_palace_validates():
    c = sim_solve.validate({"palace_cavity"})["cases"][0]
    assert c["status"] == "pass", c


@live
def test_live_openems_validates():
    c = sim_solve.validate({"openems_microstrip"})["cases"][0]
    assert c["status"] == "pass", c


# ---------------------------------------------------------------- planar

def test_planar_deck_writes_polygons_ports_and_probes():
    poly = [[0, -1.4], [30, -1.4], [30, 1.4], [0, 1.4]]
    ports = [{"x": 0, "y": 0, "dir": "-x", "w": 2.8}, {"x": 30, "y": 0, "dir": "+x", "w": 2.8}]
    csx, where = em.planar_deck([poly], ports, 1.53, 4.5, 6e9, excite=1)
    root = ET.fromstring(csx.xml())
    polys = root.findall(".//Polygon")
    assert len(polys) == 3                        # the circuit + one feed per port
    assert all(p.get("NormDir") == "2" and float(p.get("Elevation")) == 1.53 for p in polys)
    assert len(polys[0].findall("Vertex")) == 4
    names = {p.get("Name") for p in root.iter() if p.get("Name")}
    assert {"src1", "port0", "port1"} <= names and "src0" not in names
    assert {f"p{k}u{j}" for k in (0, 1) for j in range(3)} <= names
    for pr in where["ports"]:
        d = pr["dist"]
        assert d[0] < d[1] < d[2]
    xs = [float(v) for v in root.find(".//XLines").text.split(",")]
    for edge in (0.0, 30.0):                      # copper edges have their own lines
        assert any(abs(x - edge) < 1e-6 for x in xs)


def test_planar_waves_recover_a_and_b(tmp_path):
    """Probes a line with a known incident and reflected wave would give:
    the reader returns both at the reference plane, whatever the reflection."""
    import numpy as np
    eps, f = 3.0, 2e9
    beta = 2 * math.pi * f * math.sqrt(eps) / em.C0
    a, b = 1.0 * np.exp(0.3j), 0.35 * np.exp(-1.1j)
    t = np.arange(0, 100e-9, 2e-12)
    where = {"ports": [{"dist": [5.0, 10.0, 15.0]}]}
    for j, s in enumerate(where["ports"][0]["dist"]):
        sm = s * 1e-3
        ph = a * np.exp(1j * beta * sm) + b * np.exp(-1j * beta * sm)   # V(s) = a e^gs + b e^-gs
        v = np.real(ph * np.exp(2j * math.pi * f * t)) * np.hanning(t.size)
        (tmp_path / f"p0u{j}").write_text("% t v\n" + "\n".join(f"{x:.6e}\t{y:.9e}" for x, y in zip(t, v)))
    (ga, gb), = em.planar_waves(tmp_path, where, 0, [f])
    assert abs(gb / ga - b / a) < 1e-3


def test_touchstone_two_port_is_column_major():
    cols = {0: [[0.1 + 0j, 0.9 + 0j]]}             # S11, S21 from port 1's run
    line = em.touchstone([1e9], cols, 2).splitlines()[-1].split()
    assert [float(v) for v in line[1:]] == [0.1, 0, 0.9, 0, 0, 0, 0, 0]


def test_planar_geom_cuts_custom_pads_and_skips_npth(tmp_path):
    pcb = tmp_path / "c.kicad_pcb"
    pcb.write_text('''(kicad_pcb (version 20240108) (generator test)
  (general (thickness 1.6))
  (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user))
  (setup (stackup (layer "F.Cu" (type "copper") (thickness 0.035))
    (layer "dielectric 1" (type "core") (thickness 1.53) (material "FR4") (epsilon_r 4.5))
    (layer "B.Cu" (type "copper") (thickness 0.035))))
  (net 0 "") (net 1 "A")
  (footprint "t:RF" (layer "F.Cu") (at 10 10)
    (property "Reference" "RFS1")
    (pad "1" smd custom (at 0 0) (size 0.2 0.2) (layers "F.Cu" "F.Mask") (net 1 "A")
      (options (clearance outline) (anchor rect))
      (primitives (gr_poly (pts (xy -5 -1) (xy 5 -1) (xy 5 1) (xy -5 1)) (width 0) (fill yes))))
    (pad "" np_thru_hole circle (at 0 4) (size 0.5 0.5) (drill 0.5) (layers "*.Cu" "*.Mask")))
)
''')
    g = sim_solve.planar_geom(str(pcb), [0, 0, 20, 20], [{"x": 5, "y": 10, "dir": "-x", "w": 2}])
    assert g["h"] == pytest.approx(1.53) and g["er"] == pytest.approx(4.5)
    assert len(g["polys"]) == 1
    xs = [p[0] for p in g["polys"][0]]
    assert min(xs) == pytest.approx(5) and max(xs) == pytest.approx(15)   # the polygon, not a 0.2 mm box


def test_no_holes_splits_a_ring():
    from shapely.geometry import Point
    ring = Point(0, 0).buffer(10).difference(Point(0, 0).buffer(8))
    pieces = sim_solve._no_holes(ring)
    assert len(pieces) >= 2 and all(not p.interiors for p in pieces)
    assert sum(p.area for p in pieces) == pytest.approx(ring.area, rel=1e-6)


@live
def test_live_planar_thru_line_is_transparent(tmp_path):
    geo = {"h": 1.53, "er": 4.5, "polys": [[[0, -1.4265], [30, -1.4265], [30, 1.4265], [0, 1.4265]]],
           "ports": [{"x": 0, "y": 0, "dir": "-x", "w": 2.853}, {"x": 30, "y": 0, "dir": "+x", "w": 2.853}]}
    res = sim_solve.planar(geo, [0], 6e9, f_min=1e9, points=11)
    for s11, s21 in zip(res["s"]["S11"], res["s"]["S21"]):
        assert abs(complex(*s21)) == pytest.approx(1.0, abs=0.02)
        assert abs(complex(*s11)) < 0.12        # -18 dB: the probe split's floor
    # #483: the solve checks its own S. That floor on a |S21| = 1 line makes
    # column 1 non-passive (sigma 1.11 at 5.9 GHz, 41 points): reported, not hidden
    q = res["quality"]
    assert q["terms"] == ["S11", "S21"] and q["sigma_max"] is not None
    assert q["levels"]["causality"] == "good"
