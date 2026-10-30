"""hwde's power-path checks: check_power_path (current through each
pad-track-via chain), lib/soa + soa_check (a switch's turn-on SOA as a
junction temperature), lib/pdsource + sim_inrush (load switches against a
USB PD source) and lib/mlcc + mlcc_dcbias (MLCC DC bias).

Each was done by hand in hifi-receiver-2's first-spin power review
(docs/review-2/power.md, POWER-1..11); the worked numbers here are that
review's, so a change to a model that moves them away from what the
reviewer found shows up. The sim_inrush engine test runs wherever the
KiCad-bundled ngspice is (bin/hwde test exports it); it skips elsewhere.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / ".claude" / "skills" / "hwde" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import check_power_path  # noqa: E402
import geom  # noqa: E402
import mlcc  # noqa: E402
import pdsource  # noqa: E402
import sim_inrush  # noqa: E402
import soa  # noqa: E402
from lib import env  # noqa: E402


def _cli(script: str, *args) -> tuple[int, dict]:
    cp = subprocess.run([sys.executable, str(SCRIPTS / script), *args],
                        capture_output=True, text=True, timeout=600)
    return cp.returncode, json.loads(cp.stdout)


# ------------------------------------------------------------------ mlcc

@pytest.mark.parametrize("c,pkg,rated,vdc,lo,hi", [
    # review-2 readings: 10u/10V 0603 at 5.2 V ~ 50 %; 2 x 22u/25V 0805 at
    # 5.2 V ~ 22 uF; 10u/50V 1206 at 28 V ~ 3-4 uF; 1u/10V 0402 3.3 V ~ 70 %
    (10e-6, "0603", 10, 5.2, 0.30, 0.65),
    (22e-6, "0805", 25, 5.2, 0.40, 0.70),
    (10e-6, "1206", 50, 28.0, 0.20, 0.45),
    (1e-6, "0402", 10, 3.3, 0.60, 0.90)])
def test_mlcc_generic_curve_matches_review(c, pkg, rated, vdc, lo, hi):
    r = mlcc.c_eff(c, vdc, footprint=f"C_{pkg}", value="", rated_v=rated,
                   table=[])
    assert r["curve"] == "generic" and r["kind"] == "class2"
    assert lo < r["fraction"] < hi
    assert r["c_eff_low"] < r["c_eff"] < c


def test_mlcc_kinds_temperature_and_table():
    assert mlcc.classify("100pF", "C_0402")["kind"] == "class1"
    assert mlcc.classify("10uF X7R", "C_0805")["dielectric"] == "X7R"
    assert mlcc.classify("470uF", "CP_Elec_8x10")["kind"] == "electrolytic"
    assert mlcc.c_eff(470e-6, 20, footprint="CP_Elec_8x10")["c_eff"] == 470e-6
    assert mlcc.classify("1uF", "C_0603")["assumed"]
    hot = mlcc.c_eff(10e-6, 0, footprint="C_0805", text="X5R", temp_c=85,
                     table=[])
    assert hot["fraction"] == pytest.approx(0.85)
    row = {"match": "^GRM21BR61E226", "curve": [[0, 1.0], [5, 0.5],
                                                 [10, 0.3]], "source": "t"}
    r = mlcc.c_eff(22e-6, 7.5, footprint="C_0805", mpn="GRM21BR61E226ME44",
                   table=[row])
    assert r["fraction"] == pytest.approx(0.4) and r["curve"].startswith(
        "table")


# ------------------------------------------------------------------- soa

def test_soa_zth_and_hotswap_energy():
    row = soa.find("AO4485")
    assert row and not row["generic"]
    assert soa.zth(row, 1e-3) == pytest.approx(2.5)
    assert soa.zth(row, 1e-2) == pytest.approx(6.25)
    assert soa.zth(row, 0.25e-3) == pytest.approx(1.25)       # sqrt(t)
    assert soa.soa_power(row, 10e-3) == pytest.approx(20.0)
    ts, ps = soa.hotswap_profile(940e-6, 28, t_ramp=10e-3)
    res = soa.junction(row, ts, ps)
    assert res["energy_j"] == pytest.approx(0.5 * 940e-6 * 28 ** 2, rel=1e-3)
    assert soa.find("", "XYZ123", "Package_SO:SOIC-8")["generic"]
    assert soa.find("", "XYZ123", "") is None


@pytest.mark.parametrize("c,v,ramp,sev", [
    (940e-6, 28, 10e-3, "error"),     # POWER-2: core, 940 uF in ~10 ms
    (690e-6, 20, 5.75e-3, "warning"),  # POWER-4: Lite, marginal
    (940e-6, 28, 60e-3, None),        # the review's core fix: ~60 ms
    (690e-6, 20, 35e-3, None)])       # the review's Lite fix: ~28 ms 10-90
def test_soa_review_cases(c, v, ramp, sev):
    row = soa.find("AO4485")
    ts, ps = soa.hotswap_profile(c, v, t_ramp=ramp)
    got, _why = soa.judge(row, soa.junction(row, ts, ps))
    assert got == sev


def test_soa_check_cli(tmp_path):
    rc, rep = _cli("soa_check.py", "--fet", "AO4485", "--hotswap", "--c",
                   "940u", "--v", "28", "--t-ramp", "10m")
    assert rc == 1
    (v,) = rep["violations"]
    assert v["kind"] == "fet_soa" and v["severity"] == "error"
    csv = tmp_path / "p.csv"
    csv.write_text("t,vds,id\n0,20,0.1\n0.001,10,0.1\n0.002,0,0.1\n",
                   encoding="utf-8")
    rc, rep = _cli("soa_check.py", "--fet", "AO4485", "--csv", str(csv))
    assert rc == 0 and rep["result"]["energy_j"] == pytest.approx(0.002,
                                                                   rel=0.01)
    rc, rep = _cli("soa_check.py", "--fet", "AO4485", "--at", "10m")
    assert rep["soa_power_w"] == pytest.approx(20.0)


# -------------------------------------------------------------- pdsource

def test_pd_scenarios_are_well_formed():
    scs = pdsource.scenarios(20.0)
    names = [s["name"] for s in scs]
    assert names == ["attach", "step_0.5", "step_3", "step_30", "fw_on",
                     "hard_reset"]
    for sc in scs:
        for key in ("src", "fw", "pg", "discharge"):
            pts = pdsource._edges(sc[key])
            assert all(b[0] > a[0] for a, b in zip(pts, pts[1:])), (sc, key)
        assert sc["src"][-1][0] <= sc["tstop"]
    step = scs[3]
    t0, t1 = step["src"][-2][0], step["src"][-1][0]
    assert (20.0 - 5.0) / ((t1 - t0) * 1e3) == pytest.approx(30.0)
    lines = pdsource.source_lines("vbus", step)
    assert any(ln.startswith("DPD ") for ln in lines)   # cannot sink


# ------------------------------------------------------ check_power_path

def _board(tmp_path, body: str) -> Path:
    p = tmp_path / "b.kicad_pcb"
    p.write_text(f"""(kicad_pcb
  (version 20260206) (generator "test")
  (general (thickness 1.6))
  (layers (0 "F.Cu" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user))
  (setup)
  (gr_rect (start 0 0) (end 30 10) (stroke (width 0.1)) (fill no)
    (layer "Edge.Cuts"))
  (footprint "t:J" (at 2 5) (layer "F.Cu")
    (property "Reference" "J1" (at 0 0 0)) (property "Value" "USB-C" (at 0 0 0))
    (pad "1" smd rect (at 0 0) (size 2 2) (layers "F.Cu") (net "VBUS")))
  (footprint "t:Q" (at 28 5) (layer "F.Cu")
    (property "Reference" "Q1" (at 0 0 0)) (property "Value" "AO4485" (at 0 0 0))
    (pad "1" smd rect (at 0 0) (size 2 2) (layers "F.Cu") (net "VBUS")))
  (footprint "t:C" (at 15 8) (layer "F.Cu")
    (property "Reference" "C1" (at 0 0 0)) (property "Value" "10u" (at 0 0 0))
    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net "VBUS")))
{body})
""", encoding="utf-8")
    return p


# J1 -> 0.25 mm F.Cu -> two vias -> 2 mm B.Cu -> two vias -> 3 mm F.Cu -> Q1
CHAIN = """  (segment (start 2 5) (end 10 5) (width 0.25) (layer "F.Cu") (net "VBUS"))
  (segment (start 2 5) (end 10 5.6) (width 0.25) (layer "F.Cu") (net "VBUS"))
  (via (at 10 5) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net "VBUS"))
  (via (at 10 5.6) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net "VBUS"))
  (segment (start 10 5) (end 20 5) (width 2) (layer "B.Cu") (net "VBUS"))
  (segment (start 10 5.6) (end 10 5) (width 2) (layer "B.Cu") (net "VBUS"))
  (via (at 20 5) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net "VBUS"))
  (segment (start 20 5) (end 28 5) (width 3) (layer "F.Cu") (net "VBUS"))
"""


def test_power_path_bottleneck_is_the_min_cut(tmp_path):
    bg = geom.BoardGeom.from_file(_board(tmp_path, CHAIN))
    r = check_power_path.path_flow(bg, "VBUS", "J1", "Q1", 10, None, 0.02)
    one_via = check_power_path.via_amps(0.3, 0.02, 10)
    two_tracks = 2 * check_power_path.amps_for_width(0.25, 0.035, 10)
    # the two 0.25 mm tracks (~1 A) are narrower than one 0.3 mm via
    assert r["amps"] == pytest.approx(min(two_tracks, one_via), rel=1e-6)
    assert one_via == pytest.approx(1.06, abs=0.05)
    # amps_for_width inverts check_current's required width
    import check_current
    for i in (0.4, 1.0, 3.0, 8.0):
        w = check_current.required_width_mm(i, 10, 0.035)
        assert check_power_path.amps_for_width(w, 0.035, 10) == \
            pytest.approx(i, rel=1e-6)


def test_power_path_cli_judges_budget_and_paths(tmp_path):
    pcb = _board(tmp_path, CHAIN)
    cons = tmp_path / "constraints.json"
    cons.write_text(json.dumps({"power": [{"net": "VBUS", "current_a": 0.5}],
                                "power_paths": []}), encoding="utf-8")
    rc, rep = _cli("check_power_path.py", "--pcb", str(pcb),
                   "--constraints", str(cons))
    # the budget path Q1 -> J1 (a transistor is the likelier source; C1 is
    # not a power part) passes 0.5 A
    assert rc == 0, rep
    assert [(p["from"], p["to"]) for p in rep["paths"]] == [("Q1", "J1")]
    rc, rep = _cli("check_power_path.py", "--pcb", str(pcb),
                   "--path", "VBUS:J1:Q1:5")
    assert rc == 1
    (v,) = rep["violations"]
    assert v["kind"] == "path_capacity" and v["severity"] == "error"
    assert {t["width_mm"] for t in v["neck_tracks"]} == {0.25}


def test_power_path_vias_and_overlapping_pours(tmp_path):
    # J1 -> F.Cu pour -> 1 via -> two overlapping B.Cu pours -> via -> Q1
    body = """  (zone (net "VBUS") (layer "F.Cu")
    (polygon (pts (xy 1 4) (xy 8 4) (xy 8 6) (xy 1 6)))
    (filled_polygon (layer "F.Cu") (pts (xy 1 4) (xy 8 4) (xy 8 6) (xy 1 6))))
  (zone (net "VBUS") (layer "B.Cu")
    (polygon (pts (xy 6 4) (xy 16 4) (xy 16 6) (xy 6 6)))
    (filled_polygon (layer "B.Cu") (pts (xy 6 4) (xy 16 4) (xy 16 6) (xy 6 6))))
  (zone (net "VBUS") (layer "B.Cu")
    (polygon (pts (xy 15 4) (xy 25 4) (xy 25 6) (xy 15 6)))
    (filled_polygon (layer "B.Cu") (pts (xy 15 4) (xy 25 4) (xy 25 6) (xy 15 6))))
  (via (at 7 5) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net "VBUS"))
  (via (at 24 5) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net "VBUS"))
  (segment (start 24 5) (end 28 5) (width 3) (layer "F.Cu") (net "VBUS"))
"""
    bg = geom.BoardGeom.from_file(_board(tmp_path, body))
    r = check_power_path.path_flow(bg, "VBUS", "J1", "Q1", 10, 2.0, 0.02)
    assert r["amps"] == pytest.approx(2.0)          # one via per transition
    assert len(r["neck_vias"]) == 1
    r = check_power_path.path_flow(bg, "VBUS", "J1", "C1", 10, 2.0, 0.02)
    assert r["amps"] == 0.0                         # C1's pad is off the pours


# ------------------------------------------------------------ sim_inrush

def write_net(path: Path, board) -> Path:
    comps, nets = [], {}
    for ref, value, fp, pins in board:
        comps.append(f'(comp (ref "{ref}") (value "{value}") '
                     f'(footprint "{fp}"))')
        for pin, (net, fn) in pins.items():
            nets.setdefault(net, []).append(
                f'(node (ref "{ref}") (pin "{pin}") (pinfunction "{fn}") '
                f'(pintype "passive"))')
    body = "\n".join(f'(net (code "{i}") (name "{n}") {" ".join(nd)})'
                     for i, (n, nd) in enumerate(sorted(nets.items()), 1))
    path.write_text(f'(export (version "E") (components {" ".join(comps)}) '
                    f'(nets {body}))\n', encoding="utf-8")
    return path


# The Lite PVDD switch as built (POWER-3/4): AO4485 with its gate pulled to
# VBUS by 100k, a 100n Miller cap gate -> PVDD (through 1k), firmware
# pulling the gate down through 47k and an NPN, 470u + 220u of bulk.
LITE_SWITCH = [
    ("J1", "USB-C", "USB_C_Receptacle", {"1": ("VBUS", "VBUS"),
                                         "2": ("GND", "GND")}),
    ("C1", "10uF 50V X5R", "C_1206_3216Metric", {"1": ("VBUS", ""),
                                                 "2": ("GND", "")}),
    ("Q1", "AO4485", "SOIC-8", {"1": ("VBUS", "S"), "2": ("/PSW_G", "G"),
                                "3": ("PVDD", "D")}),
    ("R5", "100k", "R_0402", {"1": ("VBUS", ""), "2": ("/PSW_G", "")}),
    ("R15", "1k", "R_0402", {"1": ("/PSW_G", ""), "2": ("/MIL", "")}),
    ("C2", "100nF 50V X7R", "C_0603", {"1": ("/MIL", ""), "2": ("PVDD", "")}),
    ("R6", "47k", "R_0402", {"1": ("/PSW_G", ""), "2": ("/PULL", "")}),
    ("Q4", "MMBT3904", "SOT-23", {"1": ("/B", "B"), "2": ("GND", "E"),
                                  "3": ("/PULL", "C")}),
    ("R7", "10k", "R_0402", {"1": ("/EN", ""), "2": ("/B", "")}),
    ("R8", "100k", "R_0402", {"1": ("/EN", ""), "2": ("GND", "")}),
    ("U2", "MCU", "QFN-32", {"1": ("+3V3", "VDD"), "2": ("GND", "GND"),
                             "3": ("/EN", "IO5")}),
    ("C58", "470uF", "CP_Elec_10x10", {"1": ("PVDD", ""), "2": ("GND", "")}),
    ("C59", "220uF", "CP_Elec_8x10", {"1": ("PVDD", ""), "2": ("GND", "")}),
    ("R16", "22k", "R_0402", {"1": ("PVDD", ""), "2": ("GND", "")}),
]


def test_inrush_finds_the_switch_and_entry(tmp_path):
    import sim_board
    net = write_net(tmp_path / "b.net", LITE_SWITCH)
    board = sim_board.load_board(net)
    rails = sim_board.resolve_rails(board, {}, None, None,
                                    {"VBUS": 20.0})
    model = sim_board.build_model(board, {}, rails)
    sws = sim_inrush.find_switches(board, {}, rails, model, soa.load())
    assert [(s["ref"], s["in"], s["out"]) for s in sws] == \
        [("Q1", "VBUS", "PVDD")]
    assert sws[0]["bulk_f"] == pytest.approx(690e-6)
    assert sim_inrush.entry_net(board, model, sws, None) == "VBUS"


@pytest.fixture(scope="module")
def inrush(tmp_path_factory):
    if env.find_ngspice_dll() is None:
        pytest.skip("no ngspice shared library (KiCad bundle or "
                    "HWDE_NGSPICE_DLL)")
    d = tmp_path_factory.mktemp("inrush")
    net = write_net(d / "b.net", LITE_SWITCH)
    out = d / "r.json"
    cp = subprocess.run([sys.executable, str(SCRIPTS / "sim_inrush.py"),
                         "--net", str(net), "--rail", "VBUS=20",
                         "--slews", "3,30", "--window-ms", "150",
                         "--out", str(out)],
                        capture_output=True, text=True, timeout=900)
    assert cp.returncode == 1, cp.stdout + cp.stderr
    return json.loads(out.read_text(encoding="utf-8"))


def _scenario(rep, name):
    return next(s for s in rep["scenarios"] if s["scenario"] == name)


def test_inrush_reproduces_the_lite_review(inrush):
    # POWER-3: the Miller cap turns Q1 on at the 30 V/ms step, firmware off:
    # ~20 A from the source (the review's bench: 20.6 A, PVDD 15.6 V)
    st = _scenario(inrush, "step_30")
    assert 12 < st["src_peak_a"] < 30
    assert st["switches"]["Q1"]["out_peak_v"] > 10
    kinds = {(v["kind"], v["scenario"], v["severity"])
             for v in inrush["violations"]}
    assert ("pd_overcurrent", "step_30", "error") in kinds
    assert ("switch_on_fw_off", "step_30", "error") in kinds
    # POWER-4: firmware on, 690 uF in ~5 ms: ~0.14 J, Tj near or over max
    on = _scenario(inrush, "fw_on")["switches"]["Q1"]
    assert 0.10 < on["energy_j"] < 0.20
    assert on["tj_peak_c"] > 125
    assert 2e-3 < on["rise_10_90_s"] < 10e-3
    assert any(v["kind"] == "fet_soa" and v["scenario"] == "fw_on"
               for v in inrush["violations"])
    # Vth corners of the fitted model run on the fastest step
    assert {s["scenario"] for s in inrush["scenarios"]} >= {
        "step_30_Q1_vth1.7", "step_30_Q1_vth2.5"}


def test_inrush_review_fix_passes(tmp_path):
    """The review's Lite fix (R6 220k, 2.2u gate-source hold-off) clears
    the step and keeps turn-on inside the SOA."""
    if env.find_ngspice_dll() is None:
        pytest.skip("no ngspice shared library")
    board = [r for r in LITE_SWITCH if r[0] != "R6"] + [
        ("R6", "220k", "R_0402", {"1": ("/PSW_G", ""), "2": ("/PULL", "")}),
        ("C60", "2.2uF 25V X5R", "C_0603", {"1": ("VBUS", ""),
                                            "2": ("/PSW_G", "")})]
    net = write_net(tmp_path / "b.net", board)
    out = tmp_path / "r.json"
    subprocess.run([sys.executable, str(SCRIPTS / "sim_inrush.py"), "--net",
                    str(net), "--rail", "VBUS=20", "--slews", "30",
                    "--window-ms", "250", "--out", str(out)],
                   capture_output=True, text=True, timeout=900)
    rep = json.loads(out.read_text(encoding="utf-8"))
    bad = [v for v in rep["violations"] if v["severity"] == "error"
           and v["scenario"] in ("step_30", "fw_on")]
    assert bad == [], bad
    assert _scenario(rep, "step_30")["src_peak_a"] < 3.0
