"""rf_synth match / lib/rfmatch.py (#488): L / pi / T matching synthesis.

Known answers: Pozar, Microwave Engineering 4th ed., Example 5.1 (an L
network matching Z_L = 200 - j100 ohm to a 100 ohm line at 500 MHz: two
solutions, B = 2.9 mS / X = 122 ohm -> C = 0.92 pF, L = 38.8 nH; and
B = -6.9 mS / X = -122 ohm -> L = 46.1 nH, C = 2.61 pF). Cross-checks: a
scikit-rf cascade (the IIC-OSIC tree's) and an ngspice AC run of the
chosen network on a load .s1p give the closed form's return loss."""
from __future__ import annotations

import cmath
import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

import rf_synth  # noqa: E402
import rfmatch  # noqa: E402

F = 500e6


def _vals(net):
    return [(e["where"], e["kind"], e["value"]) for e in net["elements"]]


def test_pozar_5_1_both_solutions_and_printed_values():
    sols = rfmatch.l_sections(200 - 100j, 100, F)
    assert len(sols) == 2
    assert all(s["form"] == "shunt-series" for s in sols)
    got = sorted(_vals(s) for s in sols)
    # solution 1: shunt C 0.92 pF, series L 38.8 nH (Pozar rounds X to
    # 122 ohm; 122.47 ohm exact is 38.98 nH: within 0.5 %)
    s1 = next(v for v in got if v[0][1] == "C")
    assert s1[0][2] == pytest.approx(0.92e-12, rel=0.01)
    assert s1[1][:2] == ("series", "L")
    assert s1[1][2] == pytest.approx(38.8e-9, rel=0.01)
    # solution 2: shunt L 46.1 nH, series C 2.61 pF
    s2 = next(v for v in got if v[0][1] == "L")
    assert s2[0][2] == pytest.approx(46.1e-9, rel=0.005)
    assert s2[1][:2] == ("series", "C")
    assert s2[1][2] == pytest.approx(2.61e-12, rel=0.005)
    b = sorted(s["elements"][0]["b"] for s in sols)
    assert b[0] == pytest.approx(-6.9e-3, rel=0.01)
    assert b[1] == pytest.approx(2.9e-3, rel=0.01)


LOADS = [200 - 100j, 200 + 100j, 25 + 0j, 10 - 30j, 30 + 60j, 120 + 0j,
         49 + 5j]


@pytest.mark.parametrize("zl", LOADS)
def test_every_synthesised_network_matches_at_f0(zl):
    nets = rfmatch.l_sections(zl, 50, F)
    assert nets, f"no L-section for {zl}"
    # (+0.37: off the Q where R_v lands on R_L and an arm vanishes)
    q = 0.37 + max(2.0, 1.5 * rfmatch.q_min(zl, 50, "pi"),
                   1.5 * rfmatch.q_min(zl, 50, "T"))
    nets += rfmatch.pi_networks(zl, 50, F, q)
    nets += rfmatch.t_networks(zl, 50, F, q)
    for n in nets:
        g = rfmatch.gamma(rfmatch.zin(n["elements"], zl, F), 50)
        assert abs(g) < 1e-6, (zl, n)
        assert all(e["value"] > 0 for e in n["elements"])
    pis = [n for n in nets if n["topology"] == "pi"]
    ts = [n for n in nets if n["topology"] == "T"]
    assert len(pis) == 4 and len(ts) == 4
    assert all([e["where"] for e in n["elements"]] ==
               ["shunt", "series", "shunt"] for n in pis)
    assert all([e["where"] for e in n["elements"]] ==
               ["series", "shunt", "series"] for n in ts)


def test_l_section_conditions_rl_and_gl():
    # R_L < Z0 and G_L > 1/Z0 (25 ohm real): series-first only
    forms = {n["form"] for n in rfmatch.l_sections(25, 50, F)}
    assert forms == {"series-shunt"}
    # R_L > Z0: shunt-first only
    forms = {n["form"] for n in rfmatch.l_sections(120, 50, F)}
    assert forms == {"shunt-series"}
    # R_L < Z0 but G_L < 1/Z0 (30+j60: G = 30/4500): both
    forms = {n["form"] for n in rfmatch.l_sections(30 + 60j, 50, F)}
    assert forms == {"series-shunt", "shunt-series"}
    # already 50 + jX: one series element cancels X
    one = [n for n in rfmatch.l_sections(50 + 20j, 50, F)
           if len(n["elements"]) == 1]
    assert one and one[0]["elements"][0]["kind"] == "C"
    with pytest.raises(ValueError):
        rfmatch.l_sections(-5 + 1j, 50, F)


def test_pi_q_and_virtual_resistance():
    # 50 -> 200 ohm real, Q 5: R_v = 200 / 26; the loaded Q of the pi's
    # high side is sqrt(R_high / R_v - 1) = Q
    nets = rfmatch.pi_networks(200, 50, F, 5)
    assert nets[0]["r_virtual"] == pytest.approx(200 / 26)
    assert math.sqrt(200 / nets[0]["r_virtual"] - 1) == pytest.approx(5)
    assert rfmatch.q_min(200, 50) == pytest.approx(math.sqrt(3))
    with pytest.raises(ValueError, match="too low"):
        rfmatch.pi_networks(200, 50, F, 1.5)
    with pytest.raises(ValueError, match="too low"):
        rfmatch.t_networks(200, 50, F, 1.5)
    # higher Q: narrower band (RL at a 10 % offset is lower)
    def rl_off(q):
        n = next(n for n in rfmatch.pi_networks(200, 50, F, q)
                 if [e["kind"] for e in n["elements"]] == ["C", "L", "C"])
        load = rfmatch.Load(z0=200 + 0j, f0=F)
        return rfmatch.rl_band(n["elements"], load, [1.1 * F])["min_db"]
    assert rl_off(2) > rl_off(6)


def test_values_series_and_snap():
    assert rfmatch.parse_z("35+j20@2.45G") == (35 + 20j, 2.45e9)
    assert rfmatch.parse_z("200-100j") == (200 - 100j, None)
    assert rfmatch.parse_z("50") == (50 + 0j, None)
    assert rfmatch.parse_z("12.5-j7.25") == (12.5 - 7.25j, None)
    with pytest.raises(ValueError):
        rfmatch.parse_z("abc")
    assert rfmatch.parse_si("3n9", "H") == pytest.approx(3.9e-9)
    assert rfmatch.parse_si("0.92pF", "F") == pytest.approx(0.92e-12)
    assert rfmatch.parse_si("868MHz", "Hz") == pytest.approx(868e6)
    assert rfmatch.fmt_si(3.9e-9, "H") == "3.9nH"
    assert rfmatch.fmt_si(0.92e-12, "F") == "920fF"
    assert len(rfmatch.e_series("E12")) == 12
    assert rfmatch.e_series("E6") == [1.0, 1.5, 2.2, 3.3, 4.7, 6.8]
    assert rfmatch.e_series("E96")[1] == 1.02
    assert rfmatch.e_neighbours(0.92e-12, rfmatch.E24) == pytest.approx(
        [0.91e-12, 1.0e-12])
    assert rfmatch.e_nearest(38.98e-9, rfmatch.E24) == pytest.approx(39e-9)
    # snapping keeps the match: Pozar sol. 1 snapped to E24 still > 25 dB
    load = rfmatch.Load(z0=200 - 100j, f0=F)
    sol = rfmatch.l_sections(200 - 100j, 100, F)[0]
    snapped = rfmatch.snap(sol["elements"], load, [F], 100, "E24",
                           rfmatch.IDEAL)
    for e, s in zip(sol["elements"], snapped):
        assert s["value"] in rfmatch.e_neighbours(e["value"], rfmatch.E24)
    assert rfmatch.rl_band(snapped, load, [F], 100)["min_db"] > 25


def test_parasitics_generic_0402():
    pm = rfmatch.GENERIC_0402
    # inductor SRF ~ 11 GHz / sqrt(L/nH); impedance peaks there
    p = pm.params("L", 10e-9)
    assert p["srf_hz"] == pytest.approx(11e9 / math.sqrt(10))
    z_lo = rfmatch.element_z("L", 10e-9, 1e9, pm)
    assert z_lo.imag > 2 * math.pi * 1e9 * 10e-9   # Cp raises it
    assert z_lo.real > 0
    z_srf = rfmatch.element_z("L", 10e-9, p["srf_hz"], pm)
    assert abs(z_srf) > 20 * abs(rfmatch.element_z("L", 10e-9, 1e9, pm))
    # capacitor: series ESL resonance, inductive above
    c = pm.params("C", 10e-12)
    assert c["srf_hz"] == pytest.approx(1 / (2 * math.pi * math.sqrt(
        0.4e-9 * 10e-12)))
    assert rfmatch.element_z("C", 10e-12, 2 * c["srf_hz"], pm).imag > 0
    assert rfmatch.element_z("C", 10e-12, 1e9, rfmatch.IDEAL) == \
        pytest.approx(complex(0, -1 / (2 * math.pi * 1e9 * 10e-12)))
    # a part model's override wins
    own = rfmatch.PartModel(per_value={3.9e-9: {"kind": "L",
                                                "srf_hz": 5e9, "q": 40}})
    assert own.params("L", 3.9e-9)["srf_hz"] == 5e9
    assert own.params("L", 3.9e-9)["q"] == 40


def test_point_load_models_across_the_band():
    lum = rfmatch.Load(z0=30 + 20j, f0=1e9)
    assert lum.z(2e9) == pytest.approx(30 + 40j)
    cap = rfmatch.Load(z0=30 - 20j, f0=1e9)
    assert cap.z(2e9) == pytest.approx(30 - 10j)
    const = rfmatch.Load(z0=30 - 20j, f0=1e9, model="const")
    assert const.z(2e9) == 30 - 20j


def _antenna_s1p(tmp_path, fmt="RI"):
    """A series-RLC antenna-ish load around 2.45 GHz, 2.3-2.6 GHz."""
    freqs = [2.3e9 + 5e6 * i for i in range(61)]
    zs = [18 + 1j * (2 * math.pi * f * 3e-9 - 1 / (2 * math.pi * f *
                                                    1.6e-12))
          for f in freqs]
    p = tmp_path / "ant.s1p"
    if fmt == "RI":
        p.write_text(rfmatch.write_s1p(freqs, zs), encoding="utf-8")
    else:
        rows = ["! antenna", f"# GHz S {fmt} R 50"]
        for f, z in zip(freqs, zs):
            g = (z - 50) / (z + 50)
            a = abs(g) if fmt == "MA" else 20 * math.log10(abs(g))
            rows.append(f"{f / 1e9:.6f} {a:.12g} "
                        f"{math.degrees(cmath.phase(g)):.12g}")
        p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return p, freqs, zs


@pytest.mark.parametrize("fmt", ["RI", "MA", "DB"])
def test_s1p_reader(tmp_path, fmt):
    p, freqs, zs = _antenna_s1p(tmp_path, fmt)
    load = rfmatch.read_s1p(p)
    assert load.freqs[0] == pytest.approx(freqs[0])
    for i in (0, 17, 60):
        assert load.z(freqs[i]) == pytest.approx(zs[i], rel=1e-5)
    mid = (freqs[10] + freqs[11]) / 2
    assert load.z(mid) == pytest.approx((zs[10] + zs[11]) / 2, rel=1e-5)


def _skrf():
    try:
        import skrf  # noqa: F401
        return skrf
    except ImportError:
        pass
    import solvers
    root = solvers.toolchain_root()
    if root is None:
        return None
    for d in sorted((root / "usr/local/lib").glob(
            "python3*/dist-packages")):
        if (d / "skrf").is_dir():
            sys.path.append(str(d))
            try:
                import skrf
                return skrf
            except Exception:  # noqa: BLE001
                return None
    return None


def _chosen(tmp_path):
    p, freqs, zs = _antenna_s1p(tmp_path)
    load = rfmatch.read_s1p(p)
    f0 = 2.45e9
    net = rfmatch.pi_networks(load.z(f0), 50, f0, 3)[0]
    snapped = rfmatch.snap(net["elements"], load,
                           rfmatch.band(f0, 100e6, 11), 50, "E24",
                           rfmatch.GENERIC_0402)
    band = [f for f in freqs if 2.4e9 <= f <= 2.5e9]
    return p, load, snapped, band


def test_skrf_cascade_gives_the_same_rl(tmp_path):
    skrf = _skrf()
    if skrf is None:
        pytest.skip("scikit-rf not installed and no IIC-OSIC tree")
    p, load, els, band = _chosen(tmp_path)
    ant = skrf.Network(str(p)).interpolate(skrf.Frequency.from_f(
        band, unit="hz"))
    media = skrf.media.DefinedGammaZ0(ant.frequency, z0=50)
    pm = rfmatch.IDEAL
    chain = None
    # the ladder from the source to the load, as two-ports
    for e in reversed(els):
        two = (media.shunt_capacitor(e["value"]) if e["where"] == "shunt"
               and e["kind"] == "C" else
               media.shunt_inductor(e["value"]) if e["where"] == "shunt"
               else media.capacitor(e["value"]) if e["kind"] == "C"
               else media.inductor(e["value"]))
        chain = two if chain is None else chain ** two
    s11 = (chain ** ant).s[:, 0, 0]
    ours = rfmatch.rl_band(els, load, band, 50, pm)["per_f"]
    theirs = [-20 * math.log10(abs(g)) for g in s11]
    assert ours == pytest.approx(theirs, abs=0.01)


def _dll():
    try:
        import env
        return env.find_ngspice_dll()
    except Exception:  # noqa: BLE001
        return None


def test_live_ngspice_ac_gives_the_same_rl(tmp_path):
    dll = _dll()
    if dll is None:
        pytest.skip("no ngspice shared library")
    p, load, els, band = _chosen(tmp_path)
    for model in (rfmatch.IDEAL, rfmatch.GENERIC_0402):
        spice = rf_synth.spice_rl([els], load, band, 50, model, dll)[0]
        ours = rfmatch.rl_band(els, load, band, 50, model)["per_f"]
        assert spice == pytest.approx(ours, abs=0.01)


def _cli(*args):
    cp = subprocess.run([sys.executable, str(ROOT / "scripts" /
                                             "rf_synth.py"), *args],
                        capture_output=True, text=True, timeout=600)
    return cp.returncode, json.loads(cp.stdout)


def test_cli_match_pozar_with_python_mc():
    code, out = _cli("match", "--z", "200-j100@500M", "--z0", "100",
                     "--bw", "20M", "--mc", "60", "--engine", "python")
    assert code == 0 and out["status"] == "ok"
    ls = [s for s in out["solutions"] if s["topology"] == "L"]
    assert len(ls) == 2
    texts = sorted(tuple(e["text"] for e in s["elements"]) for s in ls)
    assert texts == [("46.1nH", "2.6pF"), ("923fF", "39nH")]
    assert {s["topology"] for s in out["solutions"]} == {"L", "pi", "T"}
    for s in out["solutions"]:
        assert s["rl"]["ideal"]["min_db"] > 20
        assert set(s["rl"]) == {"ideal", "ideal_parasitic", "snapped"}
        assert s["mc"]["engine"] == "python" and s["mc"]["n"] == 60
        assert 0 <= s["mc"]["yield"] <= 1
    ylds = [s["mc"]["yield"] for s in out["solutions"]]
    assert ylds == sorted(ylds, reverse=True)
    assert out["rl_unmatched"]["min_db"] < 8


def test_cli_topology_q_and_errors(tmp_path):
    code, out = _cli("match", "--z", "25", "--f0", "1G", "--topology",
                     "pi", "--q", "4", "--mc", "0")
    assert code == 0
    assert {s["topology"] for s in out["solutions"]} == {"pi"}
    assert out["q"] == 4
    code, out = _cli("match", "--z", "25", "--f0", "1G", "--topology",
                     "pi", "--q", "0.5", "--mc", "0")
    assert code == 2 and "too low" in out["error"]
    code, out = _cli("match", "--z", "25", "--mc", "0")
    assert code == 2 and "frequency" in out["error"]
    p, _, _ = _antenna_s1p(tmp_path)
    code, out = _cli("match", "--s1p", str(p), "--f0", "2.45G", "--bw",
                     "80M", "--topology", "L", "--mc", "0")
    assert code == 0 and out["load"]["model"] == "s1p"
    assert all(s["rl"]["snapped"]["min_db"] > 6 for s in out["solutions"])


def test_live_ngspice_mc_matches_the_closed_form():
    if _dll() is None:
        pytest.skip("no ngspice shared library")
    args = ("match", "--z", "200-j100@500M", "--z0", "100", "--bw", "20M",
            "--topology", "L", "--mc", "40", "--tol-c", "10%")
    _, py = _cli(*args, "--engine", "python")
    _, sp = _cli(*args, "--engine", "ngspice")
    for a, b in zip(py["solutions"], sp["solutions"]):
        assert b["mc"]["engine"] == "ngspice"
        assert a["mc"]["yield"] == b["mc"]["yield"]
        assert a["mc"]["rl_min_db"]["min"] == pytest.approx(
            b["mc"]["rl_min_db"]["min"], abs=0.01)


def test_eng_calc_lmatch_hand_calc():
    cp = subprocess.run([sys.executable, str(ROOT / "scripts" /
                                             "eng_calc.py"), "lmatch",
                         "--z", "200-j100", "--z0", "100", "--f", "500M"],
                        capture_output=True, text=True)
    assert cp.returncode == 0
    res = json.loads(cp.stdout)["result"]
    texts = sorted(tuple(e["text"] for e in s["elements"])
                   for s in res["solutions"])
    assert texts == [("46.1nH", "2.6pF"), ("923fF", "39nH")]
