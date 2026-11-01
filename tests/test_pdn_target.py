"""PDN target impedance + full chain (#490): scripts/lib/pdnlib.py and its
use in check_pdn_z.

Known answers from closed forms (series RLC minimum, two-branch
antiresonance, target/knee/VRM formulas), the whole ladder against ngspice
AC of the same netlist, and a planted missing-decap mutant on a synthetic
board that must fail the derived mask while the full board passes.
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
GOLDEN = REPO / "tests" / "golden"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
import check_pdn_z  # noqa: E402
import pdnlib  # noqa: E402


# ============================================================ closed forms

def test_single_cap_min_is_esr_at_srf():
    c, l, r = 100e-9, 0.8e-9, 0.025
    f0 = pdnlib.srf_hz(c, l)
    assert f0 == pytest.approx(1 / (2 * math.pi * math.sqrt(l * c)))
    freqs = np.logspace(math.log10(f0) - 1, math.log10(f0) + 1, 20001)
    z = np.abs(pdnlib.rlc_z(freqs, c, l, r))
    i = int(np.argmin(z))
    assert freqs[i] == pytest.approx(f0, rel=1e-3)
    assert z[i] == pytest.approx(r, rel=1e-6)
    # exactly at f0 the reactances cancel
    assert abs(pdnlib.rlc_z([f0], c, l, r)[0]) == pytest.approx(r, rel=1e-9)


def test_two_cap_antiresonance_closed_form():
    """10 uF / 2 nH bulk with a 100 nF / 0.6 nH ceramic: the parallel peak
    sits where X1 + X2 = 0, height X1^2 / (R1 + R2) (high Q)."""
    c1, l1, r1 = 10e-6, 2e-9, 0.005
    c2, l2, r2 = 100e-9, 0.6e-9, 0.010
    f_ar, z_pk = pdnlib.antiresonance(c1, l1, r1, c2, l2, r2)
    # hand numbers: w^2 = (1/C1 + 1/C2)/(L1 + L2)
    w = math.sqrt((1 / c1 + 1 / c2) / (l1 + l2))
    assert f_ar == pytest.approx(w / (2 * math.pi), rel=1e-12)
    assert f_ar == pytest.approx(9.92e6, rel=0.01)
    freqs = np.logspace(6, 8, 40001)
    z = np.abs(pdnlib.parallel(pdnlib.rlc_z(freqs, c1, l1, r1),
                               pdnlib.rlc_z(freqs, c2, l2, r2)))
    sel = (freqs > 3e6) & (freqs < 3e7)
    i = np.nonzero(sel)[0][int(np.argmax(z[sel]))]
    assert freqs[i] == pytest.approx(f_ar, rel=0.01)
    assert z[i] == pytest.approx(z_pk, rel=0.03)
    # and the peak tops both branches on their own
    assert z[i] > np.abs(pdnlib.rlc_z([freqs[i]], c1, l1, r1))[0]
    assert z[i] > np.abs(pdnlib.rlc_z([freqs[i]], c2, l2, r2))[0]


def test_target_knee_and_package_band():
    # 1.0 V core, 5 % ripple, 10 A step -> 5 mOhm; 3.3 V, 3 %, 0.5 A -> 198
    assert pdnlib.target_ohm(1.0, 5, 10) == pytest.approx(5e-3)
    assert pdnlib.target_ohm(3.3, 3, 0.5) == pytest.approx(0.198)
    with pytest.raises(ValueError):
        pdnlib.target_ohm(1.0, 5, 0)
    assert pdnlib.knee_hz(1.0) == pytest.approx(350e6)
    pkg = pdnlib.Package(0.2e-9, 20e-9)
    assert pkg.f_corner_hz() == pytest.approx(
        1 / (2 * math.pi * math.sqrt(0.2e-9 * 20e-9)))
    band = pdnlib.target_band(5e-3, edge_ns=1.0, pkg=pkg)
    assert band["f_hi_from"] == "package L_pkg*C_die"
    assert band["f_hi_hz"] == pytest.approx(79.58e6, rel=1e-3)
    band = pdnlib.target_band(5e-3, edge_ns=10.0, pkg=pkg)
    assert band["f_hi_from"] == "edge 0.35/t_r"
    assert band["f_hi_hz"] == pytest.approx(35e6)
    assert pdnlib.target_band(5e-3)["f_hi_hz"] is None


def test_vrm_bandwidth_model():
    v = pdnlib.VRM.from_spec({"r_out_mohm": 8, "f_bw_khz": 100})
    assert v.l_out_h == pytest.approx(8e-3 / (2 * math.pi * 1e5))
    assert not v.assumed
    # flat R below f_bw, |Z| = R*sqrt(2) at f_bw, ~wL far above
    z = np.abs(v.z([1e3, 1e5, 1e8]))
    assert z[0] == pytest.approx(8e-3, rel=1e-3)
    assert z[1] == pytest.approx(8e-3 * math.sqrt(2), rel=1e-9)
    assert z[2] == pytest.approx(2 * math.pi * 1e8 * v.l_out_h, rel=1e-3)
    d = pdnlib.VRM.from_spec(None)
    assert d.assumed and "default" in d.source
    e = pdnlib.VRM.from_spec({"r_out_mohm": 5, "l_out_nh": 12})
    assert e.l_out_h == pytest.approx(12e-9)


def test_chain_package_limits_and_mask():
    caps = [pdnlib.Cap("C1", 22e-6, 0.004, 1e-9),
            pdnlib.Cap("C2", 100e-9, 0.02, 0.6e-9)]
    vrm = pdnlib.VRM(0.01, 16e-9)
    pkg = pdnlib.Package(0.5e-9, 5e-9, r_die_ohm=0.02)
    freqs = np.logspace(3, 9, 601)
    ch = pdnlib.chain_z(freqs, caps, vrm=vrm, pkg=pkg)
    # DC end: the VRM's R_out (caps open); die side equals board side there
    assert abs(ch["board"][0]) == pytest.approx(0.01, rel=0.02)
    assert abs(ch["die"][0]) == pytest.approx(abs(ch["board"][0]), rel=0.02)
    # far above the package corner the die sees only C_die
    zc = abs(pdnlib.rlc_z([1e9], 5e-9, 0, 0.02)[0])
    assert abs(ch["die"][-1]) == pytest.approx(zc, rel=0.05)
    mm = pdnlib.mask_margin(freqs, ch["board"], 0.05, 1e4, 3e7)
    assert mm["worst_ratio"] is not None
    assert (mm["worst_ratio"] > 1) == bool(mm["over"])
    for f0, f1, zmax in mm["over"]:
        assert 1e4 <= f0 <= f1 <= 3e7 and zmax > 0.05
    # a target far above the curve is never broken
    assert pdnlib.mask_margin(freqs, ch["board"], 10.0)["over"] == []


def test_cap_from_part_derates_class2():
    c = pdnlib.cap_from_part("C1", "10uF", 10e-6, 3.3,
                             footprint="Capacitor_SMD:C_0603_1608Metric")
    assert c.c_f < 10e-6 * 0.9          # X5R 0603 at 3.3 V loses a lot
    assert c.meta["package"] == "0603"
    assert c.esl_h == pytest.approx(pdnlib.ESL_BODY_NH["0603"] * 1e-9)
    assert c.esr_ohm == pdnlib.esr_default(10e-6)
    c0 = pdnlib.cap_from_part("C2", "100pF C0G", 100e-12, 3.3,
                              footprint="Capacitor_SMD:C_0402_1005Metric")
    assert c0.c_f == pytest.approx(100e-12)


def test_package_table_lookup():
    rows = [{"match": "ECP5", "l_pkg_nh": 0.1, "c_die_nf": 40,
             "verified": True, "source": "TN"},
            {"footprint": "BGA", "l_pkg_nh": 0.2, "c_die_nf": 20},
            {"default": True, "l_pkg_nh": 0.5, "c_die_nf": 5}]
    p = pdnlib.package_for(["LFE5U ECP5"], "x", table=rows)
    assert p.l_pkg_h == pytest.approx(0.1e-9) and not p.assumed
    p = pdnlib.package_for(["?"], "Package_BGA:BGA-256", table=rows)
    assert p.c_die_f == pytest.approx(20e-9) and p.assumed
    p = pdnlib.package_for(["?"], "QFN", table=rows)
    assert p.source == "generic default" and p.assumed
    p = pdnlib.package_for([], "", {"l_pkg_nh": 1, "c_die_nf": 2}, rows)
    assert p.source == "constraints" and not p.assumed
    # the shipped table loads and has a default row
    assert any(r.get("default") for r in pdnlib.load_pkg_table())


# ============================================================ ngspice

_NG_RUN = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
import env, simlib
dll = env.find_ngspice_dll()
simlib.setup_engine_env(dll)
deck = open(sys.argv[2]).read()
print(json.dumps(simlib.run_circuit(simlib.prepare_circuit(deck))))
"""


def test_ladder_matches_ngspice_ac(tmp_path):
    import env
    if env.find_ngspice_dll() is None:
        pytest.skip("no ngspice shared library (KiCad bundle or "
                    "HWDE_NGSPICE_DLL)")
    caps = [pdnlib.Cap("C1", 47e-6, 0.005, 1.2e-9, 0.8e-9),
            pdnlib.Cap("C2", 1e-6, 0.012, 0.5e-9, 0.6e-9),
            pdnlib.Cap("C3", 100e-9, 0.025, 0.35e-9, 0.7e-9),
            pdnlib.Cap("C4", 10e-9, 0.08, 0.35e-9, 0.5e-9)]
    vrm = pdnlib.VRM.from_spec({"r_out_mohm": 8, "f_bw_khz": 80})
    pkg = pdnlib.Package(0.3e-9, 10e-9, 0.5e-3, 10e-3)
    fpts = list(np.logspace(4, 8.5, 19))
    deck = tmp_path / "pdn.cir"
    deck.write_text(pdnlib.ladder_spice(caps, vrm, plane_c_f=8e-9, pkg=pkg,
                                        f_points=fpts))
    cp = subprocess.run([sys.executable, "-c", _NG_RUN, str(SCRIPTS / "lib"),
                         str(deck)], capture_output=True, text=True,
                        timeout=120)
    out = json.loads(cp.stdout.strip().splitlines()[-1])
    assert out["status"] == "ok", out
    ours = np.abs(pdnlib.chain_z(fpts, caps, vrm=vrm, plane_c_f=8e-9,
                                 pkg=pkg)["die"])
    for k, f in enumerate(fpts):
        assert out["measures"][f"z{k}"] == pytest.approx(ours[k], rel=0.01), \
            f"{f:.3g} Hz"


# ============================================================ boards

_LAYERS2 = '(layers (0 "F.Cu" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user))'


def _fp(ref, lib, x, y, pads, value=""):
    body = "".join(
        f'    (pad "{n}" smd rect (at {px} {py}) (size {w} {h}) '
        f'(layers "F.Cu") (net "{net}"))\n' for n, px, py, w, h, net in pads)
    return (f'  (footprint "{lib}" (at {x} {y})\n'
            f'    (property "Reference" "{ref}" (at 0 0) (layer "F.SilkS"))\n'
            f'    (property "Value" "{value}" (at 0 0) (layer "F.Fab"))\n'
            f'{body}  )\n')


def _via(x, y, net):
    return (f'  (via (at {x} {y}) (size 0.6) (drill 0.3) '
            f'(layers "F.Cu" "B.Cu") (net "{net}"))\n')


# U1 (a QFN) on +1V1: 22 uF / 4.7 uF bulk, two 1 uF and two 100 nF at the
# pins, each with its own via pair (mm = distance to U1's rail pin).
# Target 1.1 V * 5 % / 0.32 A = 171.9 mOhm flat to 0.35/10 ns = 35 MHz; the
# full set peaks near 159 mOhm (6.5 MHz antiresonance).
_S, _M, _L = ("Capacitor_SMD:C_0402_1005Metric",
              "Capacitor_SMD:C_0603_1608Metric",
              "Capacitor_SMD:C_0805_2012Metric")
_CAPS = [("C1", "22uF", _L, 6.0), ("C2", "4.7uF", _M, 4.0),
         ("C3", "1uF", _S, 2.0), ("C4", "1uF", _S, 2.0),
         ("C5", "100nF", _S, 1.5), ("C6", "100nF", _S, 1.5)]
_ZT = 1.1 * 0.05 / 0.32 * 1e3
_CONS = {"power": [{"net": "+1V1", "current_a": 1.0, "transient_a": 0.32,
                    "ripple_pct": 5, "edge_ns": 10,
                    "vrm": {"r_out_mohm": 10, "f_bw_khz": 100}},
                   {"net": "+3V3", "current_a": 0.1},
                   {"net": "GND", "current_a": 1.0}]}


def _board(tmp_path: Path, caps, rail="+1V1") -> tuple[Path, Path, Path]:
    body = _fp("U1", "Package_DFN_QFN:QFN-32", 20, 10,
               [("1", 0, 0, 0.3, 0.8, rail), ("2", 1, 0, 0.3, 0.8, "GND")])
    assocs = []
    for i, (ref, val, lib, dist) in enumerate(caps):
        y = 10 + (dist if i % 2 == 0 else -dist)
        x = 20 + (i // 2) * 0.8
        body += _fp(ref, lib, x, y, [("1", 0, 0, 0.5, 0.5, rail),
                                     ("2", 1.0, 0, 0.5, 0.5, "GND")], val)
        body += _via(x, y + 0.6, rail) + _via(x + 1.0, y + 0.6, "GND")
        assocs.append({"cap": ref, "ic": "U1", "pin": "1", "rail": rail,
                       "value": val})
    pcb = tmp_path / "pdn.kicad_pcb"
    pcb.write_text(f"""(kicad_pcb
  (version 20260206) (generator "test")
  (general (thickness 1.6))
  {_LAYERS2}
  (setup)
  (gr_rect (start 0 0) (end 40 25) (stroke (width 0.1)) (fill no)
    (layer "Edge.Cuts"))
{body})
""", encoding="utf-8")
    meta = tmp_path / "decoupling.json"
    meta.write_text(json.dumps({"associations": assocs}), encoding="utf-8")
    cons = tmp_path / "constraints.json"
    cons.write_text(json.dumps(_CONS), encoding="utf-8")
    return pcb, meta, cons


def _run(pcb, meta, cons, tmp_path):
    payload, _ = check_pdn_z.run(
        ["--pcb", str(pcb), "--metadata", str(meta), "--constraints",
         str(cons), "--curve-out", str(tmp_path / "curve.json")])
    return payload


def test_board_full_set_passes_derived_mask(tmp_path):
    payload = _run(*_board(tmp_path, _CAPS), tmp_path)
    assert payload["status"] == "pass", payload["violations"]
    assert payload["pairs"] == []           # 2-layer: no plane pair
    (r,) = payload["rails"]
    assert r["rail"] == "+1V1" and r["model"] == "lumped"
    t = r["target"]
    assert t["z_t_mohm"] == pytest.approx(_ZT, abs=1e-3)
    assert t["from"] == "V*ripple/dI" and t["voltage"] == 1.1
    assert t["f_hi_hz"] == pytest.approx(35e6, rel=1e-3)
    assert t["f_hi_from"] == "edge 0.35/t_r"
    assert t["vrm"]["assumed"] is False
    assert t["package"]["assumed"] is True       # class row, flagged
    assert "QFN" in t["package"]["source"]
    assert r["mask"]["worst_ratio"] < 1.0
    assert [d["cap"] for d in r["decaps"]] == [c[0] for c in _CAPS]
    for d in r["decaps"]:
        assert d["l_mount_nh"] > 0 and d["l_path_nh"] > 0
        assert d["derate"] < 1.0                 # X5R at 1.1 V
    assert payload["targets_missing"] == ["+3V3"]   # GND is no rail
    curve = json.loads((tmp_path / "curve.json").read_text())
    assert len(curve["pairs"][0]["z_chain_mohm"]) == check_pdn_z.N_FREQ


def test_board_missing_decap_mutant_fails(tmp_path):
    """Planted fault: one of the two 1 uF caps at U1 left off. The 6 MHz
    antiresonance between the bulk and the 100 nF pair rises over the
    171.9 mOhm target, inside the 35 MHz band."""
    caps = [c for c in _CAPS if c[0] != "C3"]
    payload = _run(*_board(tmp_path, caps), tmp_path)
    assert payload["status"] == "violations"
    vs = [v for v in payload["violations"] if v["kind"] == "pdn_z_excess"]
    assert vs and all(v["derived"] for v in vs)
    assert all(v["f_hz"] <= 35e6 * 1.001 for v in vs)
    assert all(v["z_mohm"] > _ZT for v in vs)
    assert any(2e6 < v["f_hz"] < 1e7 for v in vs)
    assert payload["rails"][0]["mask"]["worst_ratio"] > 1.0


def test_ripple_default_and_unreadable_voltage(tmp_path):
    pcb, meta, cons = _board(tmp_path, _CAPS)
    c = json.loads(cons.read_text())
    c["power"][0].pop("ripple_pct")
    cons.write_text(json.dumps(c))
    payload = _run(pcb, meta, cons, tmp_path)
    assert payload["rails"][0]["target"]["ripple_pct"] == \
        pdnlib.RIPPLE_PCT_DEFAULT
    # a rail whose name holds no voltage: warned, not judged
    d2 = tmp_path / "v"
    d2.mkdir()
    pcb, meta, cons = _board(d2, _CAPS, rail="VCORE")
    cons.write_text(json.dumps({"power": [{"net": "VCORE",
                                           "transient_a": 1.0}]}))
    payload = _run(pcb, meta, cons, d2)
    assert "no voltage" in payload["rails"][0]["skipped"]
    assert [v for v in payload["violations"]
            if v["kind"] == "pdn_target_underived"]


def test_plane_pair_rail_judged_on_chain(tmp_path):
    """usbbuck4's +3V3 plane pairs with a transient spec: the first pair
    carries the derived mask (cavity + VRM), the old peaks gate is off."""
    cons = tmp_path / "constraints.json"
    cons.write_text(json.dumps({"power": [
        {"net": "+3V3", "current_a": 0.4, "transient_a": 0.2,
         "ripple_pct": 5, "vrm": {"r_out_mohm": 20, "f_bw_khz": 100}}]}))
    payload, _ = check_pdn_z.run(
        ["--pcb", str(GOLDEN / "usbbuck4" / "usbbuck4.kicad_pcb"),
         "--metadata", str(GOLDEN / "usbbuck4" / "decoupling.json"),
         "--constraints", str(cons)])
    judged = [p for p in payload["pairs"] if "mask" in p]
    assert len(judged) == 1
    t = judged[0]["target"]
    assert t["z_t_mohm"] == pytest.approx(3.3 * 0.05 / 0.2 * 1e3)
    assert judged[0]["mask"]["model"].startswith("cavity")
    assert payload["rails"] == []           # no lumped duplicate
    for v in payload["violations"]:
        if v["kind"] == "pdn_z_excess":
            assert v["derived"] is True
