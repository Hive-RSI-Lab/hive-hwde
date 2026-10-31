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
                                  "fastercap_cube"])
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
