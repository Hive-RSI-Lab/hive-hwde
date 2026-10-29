"""SPICE sim gate acceptance tests (sim_run.py + lib/simlib.py).

Hermetic tests exercise fragment synthesis on the COMMITTED board netlists,
the bounds/measure logic on captured engine output, and the report assembly
with a monkeypatched worker - no toolchain needed. `smoke` tests drive the
real KiCad-bundled ngspice shared library (session fixture skips when
env.find_ngspice_dll() finds none): the flagship testbenches must pass and
the seeded wrong-value mutant (R7 4.7k -> 47k) must fail on exactly the
vtrip_grn measure - the defect class ERC/DRC/verify/DFM all miss.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / ".claude" / "skills" / "hwde" / "scripts"
SIMS = REPO / "tests" / "sims"
# the shipped boards' netlists, frozen under tests/fixtures
PD_NET = REPO / "tests" / "fixtures" / "stages" / "pd_trigger" / "pd-trigger.net"
BLINKY_NET = REPO / "tests" / "fixtures" / "sim" / "stm32-blinky.net"
GATES_YAML = SCRIPTS.parent / "reference" / "gates.yaml"
PYTHON = sys.executable
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import check_env  # noqa: E402
import checklib  # noqa: E402
import gate  # noqa: E402
import sim_run  # noqa: E402
import simlib  # noqa: E402
from lib import env  # noqa: E402

FLAGSHIP = SIMS / "pd_trigger_zener_window.cir"
MUTANT = SIMS / "pd_trigger_zener_window_mutant_r7.cir"


def run_cli(*args, extra_env=None):
    e = dict(os.environ)
    e.update(extra_env or {})
    return subprocess.run(
        [PYTHON, str(SCRIPTS / "sim_run.py"), *args],
        capture_output=True, text=True, env=e, timeout=300, cwd=REPO)


# ------------------------------------------------------- fragment synthesis

def test_fragment_pd_trigger_zener_block():
    parsed = simlib.parse_netlist(PD_NET)
    frag = simlib.synth_fragment(
        parsed, refs=["D2", "D5", "D6", "R6", "R7", "R8", "R9", "R12", "R13"])
    els = frag["elements"]
    # exact topology from the committed netlist, renamed deterministically
    assert "R6 N_ZBIAS N_HV_B 6.8k" in els
    assert "R7 N_HV_B 0 4.7k" in els            # GND -> SPICE ground node 0
    assert "R9 N_HV_OK N_FB_B 47k" in els
    assert "R12 N_VIND N_D5_A 1.5k" in els
    # 3-pin SOT-23 zener resolved by A_/K_ pinfunctions (anode first)
    assert "D2 N_ZBIAS N_VIND D_D2" in els
    assert "D5 N_D5_A N_FB_K D_D5" in els
    rm = frag["rename_map"]
    assert rm["/VIND"] == "N_VIND" and rm["GND"] == "0"
    models = {m["ref"]: m for m in frag["models"]}
    assert models["D2"]["kind"] == "diode"
    assert models["D2"]["card"].startswith(".model D_D2 D(")
    assert frag["unresolved"] == []


def test_fragment_dual_bjt_is_unresolved_with_pin_table():
    # A BC847BS dual does NOT pin-sort into units (unit A is pins 1/2/6, not
    # 1/2/3): the helper must refuse to guess and hand over the pin table.
    parsed = simlib.parse_netlist(PD_NET)
    frag = simlib.synth_fragment(parsed, refs=["Q1"])
    assert frag["elements"] == []
    (u,) = frag["unresolved"]
    assert u["ref"] == "Q1"
    pins = {p["pin"]: p for p in u["pins"]}
    assert pins["2"]["pinfunction"] == "B_2"
    assert pins["2"]["spice_node"] == "N_HV_B"
    assert pins["6"]["net"] == "/HV_OK"


def test_fragment_rename_map_blinky():
    parsed = simlib.parse_netlist(BLINKY_NET)
    frag = simlib.synth_fragment(parsed, refs=["R1", "D2", "C9"])
    rm = frag["rename_map"]
    assert rm["+3V3"] == "P3V3"      # "+" is the SPICE continuation char
    assert rm["/LED"] == "N_LED"
    assert "C9 N_NRST 0 100nF" in frag["elements"]
    # KiCad Device pin1=K / pin2=A fallback for the 2-pin LED
    assert "D2 N_LED_A N_LED D_D2" in frag["elements"]


def test_fragment_net_selection_pulls_refs():
    parsed = simlib.parse_netlist(BLINKY_NET)
    frag = simlib.synth_fragment(parsed, nets=["/LED_A"])
    assert set(frag["refs"]) == {"R1", "D2"}


def test_fragment_singleton_net_tolerance():
    # D2's pin 2 sits on a singleton unconnected-* net. Hierarchical exports
    # DROP those nets entirely - synthesis must give identical elements
    # either way and only fall back to an nc_* placeholder node.
    parsed = simlib.parse_netlist(PD_NET)
    with_nc = simlib.synth_fragment(parsed, refs=["D2"])
    assert with_nc["rename_map"]["unconnected-(D2-Pad2)"] == "NC_D2_PAD2"
    stripped = {"components": parsed["components"],
                "nets": {k: v for k, v in parsed["nets"].items()
                         if not k.startswith("unconnected-")}}
    without = simlib.synth_fragment(stripped, refs=["D2"])
    assert without["elements"] == with_nc["elements"]
    assert without["floating_nodes"] == []  # A/K pins both still netted


def test_fragment_errors():
    parsed = simlib.parse_netlist(PD_NET)
    with pytest.raises(checklib.CheckError, match="refs not in netlist"):
        simlib.synth_fragment(parsed, refs=["R999"])
    with pytest.raises(checklib.CheckError, match="empty selection"):
        simlib.synth_fragment(parsed)
    with pytest.raises(checklib.CheckError, match="not in netlist"):
        simlib.synth_fragment(parsed, nets=["/NOPE"])


def test_spice_net_name_rules():
    f = simlib.spice_net_name
    assert f("GND") == "0"
    assert f("+3V3") == "P3V3"
    assert f("/LED") == "N_LED"
    assert f("/a/b c") == "N_A_B_C"
    assert f("unconnected-(J1-Dn1-PadA7)") == "NC_J1_DN1_PADA7"
    assert f("12V")[0] == "N"  # never a leading digit
    # collisions dedup deterministically
    rm = simlib.rename_map(["/A B", "/A_B", "/A-B"])
    assert len(set(rm.values())) == 3


def test_value_token_kicad_mega_trap():
    # KiCad "1M" is a megohm; SPICE "m" is milli. 6 orders of magnitude.
    assert simlib._value_token("1M") == "1meg"
    assert simlib._value_token("1meg") == "1meg"
    assert simlib._value_token("10uF 50V X5R") == "10uF"
    assert simlib._value_token("PPTC 1A 30V") is None
    assert simlib._value_token("0R") == "0R"


# ------------------------------------------------------------ bounds logic

def test_load_bounds_committed_sidecars_valid():
    for bp in sorted(SIMS.glob("*.bounds.json")):
        bounds = simlib.load_bounds(bp)
        assert bounds, bp
        assert all(b.get("severity", "error") in ("error", "warning")
                   for b in bounds)


def test_load_bounds_validation(tmp_path):
    bad = tmp_path / "b.bounds.json"
    bad.write_text('{"measure": "x", "min": 0}', encoding="utf-8")
    with pytest.raises(checklib.CheckError, match="must be a JSON list"):
        simlib.load_bounds(bad)
    bad.write_text('[{"measure": "x"}]', encoding="utf-8")
    with pytest.raises(checklib.CheckError, match="min.*max|max.*min"):
        simlib.load_bounds(bad)
    bad.write_text('[{"measure": "x", "min": 0, "severity": "fatal"}]',
                   encoding="utf-8")
    with pytest.raises(checklib.CheckError, match="severity"):
        simlib.load_bounds(bad)
    bad.write_text('[{"min": 0}]', encoding="utf-8")
    with pytest.raises(checklib.CheckError, match="measure"):
        simlib.load_bounds(bad)


def test_compare_bounds_kinds_and_severities():
    bounds = [
        {"measure": "OK_ONE", "min": 1.0, "max": 3.0, "severity": "error"},
        {"measure": "too_low", "min": 5.0, "severity": "error", "msg": "why"},
        {"measure": "too_high", "max": 1.0, "severity": "warning"},
        {"measure": "gone", "min": 0.0, "severity": "warning"},
        {"measure": "never_met", "min": 0.0, "severity": "error"},
        {"measure": "edge", "min": 2.0, "max": 2.0, "severity": "error"},
    ]
    measures = {"ok_one": 2.0, "too_low": 4.0, "too_high": 2.5, "edge": 2.0}
    vios = simlib.compare_bounds(bounds, measures, "tb.cir",
                                 failed_measures=["never_met"])
    by = {v["measure"]: v for v in vios}
    assert set(by) == {"too_low", "too_high", "gone", "never_met"}
    assert by["too_low"]["kind"] == "sim_bound_fail"
    assert by["too_low"]["severity"] == "error"
    assert by["too_low"]["value"] == 4.0
    assert by["too_low"]["bound"] == {"min": 5.0, "max": None}
    assert by["too_low"]["testbench"] == "tb.cir"
    assert "why" in by["too_low"]["msg"]
    assert by["too_high"]["severity"] == "warning"
    assert by["gone"]["kind"] == "sim_measure_missing"
    assert by["never_met"]["kind"] == "sim_measure_missing"
    assert "failed!" in by["never_met"]["msg"]
    # normalized S2 schema fields present
    for v in vios:
        assert v["check"] == "sim" and v["source"] == "sim.ngspice"
        assert v["refs"] == [] and v["pos"] is None


def test_compare_bounds_nonfinite_fails():
    vios = simlib.compare_bounds([{"measure": "m", "min": 0.0}],
                                 {"m": math.nan}, "tb.cir")
    assert len(vios) == 1 and vios[0]["kind"] == "sim_bound_fail"


# ------------------------------------------------------- engine text plumbing

CAPTURED_TRAN_STDOUT = """\
Doing analysis at TEMP = 27.000000 and TNOM = 27.000000
Using SPARSE 1.3 as Direct Linear Solver
Initial Transient Solution
--------------------------
Node                                   Voltage
----                                   -------
p3v3                                       3.3
vpin#branch                           -6.6e-09
No. of Data Rows : 1511
Measurements for Transient Analysis
t_rise              =  8.78996e-03 targ=  9.21141e-03 trig=  4.21447e-04
v_final             =  3.29753e+00
i_neg               =  -3.13506e-18 at=  8.00000e-03
"""


def test_parse_measures_captured_output():
    m = simlib.parse_measures(CAPTURED_TRAN_STDOUT)
    assert m == {"t_rise": pytest.approx(8.78996e-03),
                 "v_final": pytest.approx(3.29753),
                 "i_neg": pytest.approx(-3.13506e-18)}
    # nothing outside a Measurements region may leak in
    assert "p3v3" not in m and "temp" not in m


def test_parse_failed_measures():
    err = (".measure tran never trig v(nrst) val=5.0 rise=1 targ v(nrst) "
           "val=6.0 rise=1 failed!\nout of interval\n")
    assert simlib.parse_failed_measures(err) == ["never"]


def test_prepare_circuit():
    out = simlib.prepare_circuit("* t\nR1 a 0 1k\n")
    lines = out.splitlines()
    assert lines[-1] == ".end"                      # silent-no-circuit trap
    assert lines[1] == ".options rshunt=1e9"        # floating-node trap
    already = simlib.prepare_circuit("* t\n.options rshunt=1e6\n.end\n")
    assert already.count("rshunt") == 1
    with pytest.raises(checklib.CheckError, match="empty"):
        simlib.prepare_circuit("  \n")


# ------------------------------------------- report assembly (worker mocked)

def _fake_dll(monkeypatch):
    monkeypatch.setattr(sim_run.env, "find_ngspice_dll",
                        lambda: Path("C:/fake/ngspice.dll"))


def test_run_report_assembly(tmp_path, monkeypatch):
    _fake_dll(monkeypatch)
    good = tmp_path / "good.cir"
    good.write_text("* g\n.end\n", encoding="utf-8")
    (tmp_path / "good.bounds.json").write_text(
        '[{"measure": "m1", "min": 1.0, "max": 3.0}]', encoding="utf-8")
    failing = tmp_path / "failing.cir"
    failing.write_text("* f\n.end\n", encoding="utf-8")
    (tmp_path / "failing.bounds.json").write_text(
        '[{"measure": "m1", "min": 5.0, "severity": "error"}]',
        encoding="utf-8")
    nosidecar = tmp_path / "nosidecar.cir"
    nosidecar.write_text("* n\n.end\n", encoding="utf-8")
    hung = tmp_path / "z_hung.cir"
    hung.write_text("* z\n.end\n", encoding="utf-8")
    (tmp_path / "z_hung.bounds.json").write_text(
        '[{"measure": "m1", "min": 0}]', encoding="utf-8")

    def fake_worker(cir, dll, timeout):
        if cir.stem == "z_hung":
            return {"status": "timeout", "error": "engine timeout after 60 s"}
        return {"status": "ok", "ngspice_version": 46,
                "measures": {"m1": 2.0}, "failed_measures": []}

    monkeypatch.setattr(sim_run, "_run_worker", fake_worker)
    rep = sim_run.run(tmp_path)
    assert rep["script"] == "sim_run" and rep["status"] == "violations"
    by_bench = {t["name"]: t for t in rep["testbenches"]}
    assert by_bench["good"]["status"] == "pass"
    assert by_bench["good"]["measures"] == {"m1": 2.0}
    assert by_bench["failing"]["status"] == "violations"
    assert by_bench["nosidecar"]["status"] == "error"
    assert by_bench["z_hung"]["status"] == "error"
    kinds = sorted(v["kind"] for v in rep["violations"])
    assert kinds == ["sim_bound_fail", "sim_engine_error", "sim_engine_error"]
    assert rep["counts"]["by_severity"]["error"] == 3
    # an engine error names its bench and never masks the others
    msgs = [v["msg"] for v in rep["violations"]
            if v["kind"] == "sim_engine_error"]
    assert any("nosidecar" in m and "sidecar" in m for m in msgs)
    assert any("z_hung" in m and "timeout" in m for m in msgs)
    # exit-code mapping per SPEC section 6
    assert checklib.emit(rep, str(tmp_path / "r.json")) == 1


def test_run_without_dll_is_clean_error(monkeypatch):
    monkeypatch.setattr(sim_run.env, "find_ngspice_dll", lambda: None)
    with pytest.raises(checklib.CheckError, match="HWDE_NGSPICE_DLL"):
        sim_run.run(SIMS)


# ----------------------------------------------------------------- CLI/gate

def test_cli_list_mode():
    r = run_cli("--list", "--dir", str(SIMS))
    assert r.returncode == 0, r.stderr
    d = json.loads(r.stdout)
    assert d["mode"] == "list" and d["status"] == "pass"
    names = {t["name"] for t in d["testbenches"]}
    assert {"pd_trigger_zener_window", "pd_trigger_zener_window_mutant_r7",
            "blinky_pc13_led_sink", "blinky_nrst_rc"} <= names
    assert all(t["has_bounds"] for t in d["testbenches"])
    tb = next(t for t in d["testbenches"]
              if t["name"] == "pd_trigger_zener_window")
    assert len(tb["bounds"]) == 5


def test_cli_error_paths(tmp_path):
    r = run_cli("--dir", str(tmp_path))          # no testbenches
    assert r.returncode == 2
    assert json.loads(r.stdout)["status"] == "error"
    r = run_cli("--testbench", str(FLAGSHIP),    # loud wrong pin, exact grammar
                extra_env={"HWDE_NGSPICE_DLL": r"C:\nonexistent\ng.dll"})
    assert r.returncode == 2
    err = json.loads(r.stdout)
    assert "HWDE_NGSPICE_DLL does not exist" in err["error"]
    r = run_cli("--fragment")                    # fragment needs --net
    assert r.returncode == 2


def test_gates_yaml_sim_gate_wired():
    gates = yaml.safe_load(GATES_YAML.read_text(encoding="utf-8"))["gates"]
    g = gates["sim"]
    assert g["tool"] == "sim" and g["phase"] == "P8"
    assert g["fail_severities"] == ["error"] and g["max_count"] == 0
    # gate.py knows the tool (report-shape evaluation is engine-free)
    res = gate.evaluate("sim", g, {"violations": [
        {"severity": "error", "kind": "sim_bound_fail"}], "counts": {}})
    assert res["status"] == "fail"


def test_check_env_registers_inspice():
    assert check_env.REQUIRED_PACKAGES.get("InSpice") == "InSpice"
    assert "HWDE_NGSPICE_DLL" in check_env.NGSPICE_HELP or \
           "HWDE_NGSPICE_DLL" in env.find_ngspice_dll.__doc__


# ------------------------------------------------------------------- smoke

@pytest.fixture(scope="session")
def ngspice_dll():
    dll = env.find_ngspice_dll()
    if dll is None:
        pytest.skip("no ngspice shared library (KiCad bundle or HWDE_NGSPICE_DLL)")
    return dll


@pytest.mark.smoke
def test_smoke_flagship_zener_window_passes(ngspice_dll):
    rep = sim_run.run(FLAGSHIP)
    assert rep["status"] == "pass", rep["violations"]
    m = rep["testbenches"][0]["measures"]
    # exactly-one-lit invariant, live values (calibrated 2026-07: 2.18 mA /
    # 1.32 mA / trip 7.74 V with the generic datasheet-derived models)
    assert 1e-3 < m["i_red_5v0"] < 4e-3
    assert m["i_grn_5v0"] < 1e-9
    assert m["i_red_9v0"] < 1e-6
    assert 5e-4 < m["i_grn_9v0"] < 4e-3
    assert 7.5 < m["vtrip_grn"] < 8.0


@pytest.mark.smoke
def test_smoke_mutant_r7_fails_on_vtrip(ngspice_dll):
    rep = sim_run.run(MUTANT)
    assert rep["status"] == "violations"
    (v,) = rep["violations"]  # exactly one bound trips, and it names R7's job
    assert v["kind"] == "sim_bound_fail"
    assert v["measure"] == "vtrip_grn"
    assert v["severity"] == "error"
    assert v["value"] < 7.2  # trip collapsed below the design window
    assert v["testbench"] == MUTANT.name


@pytest.mark.smoke
def test_smoke_blinky_pc13_sink_under_abs_max(ngspice_dll):
    rep = sim_run.run(SIMS / "blinky_pc13_led_sink.cir")
    assert rep["status"] == "pass", rep["violations"]
    m = rep["testbenches"][0]["measures"]
    assert 5e-4 < m["i_led_max"] <= 3e-3  # the DS5319 Table 5 note 5 bound


@pytest.mark.smoke
def test_smoke_blinky_nrst_rc_rise(ngspice_dll):
    rep = sim_run.run(SIMS / "blinky_nrst_rc.cir")
    assert rep["status"] == "pass", rep["violations"]
    m = rep["testbenches"][0]["measures"]
    assert 4e-3 < m["t_rise"] < 16e-3  # tau*ln9 ~ 8.8 ms
    assert m["v_final"] > 3.2


@pytest.mark.smoke
def test_smoke_dir_run_cli_and_gate(ngspice_dll):
    # CLI over the whole committed dir: only the mutant may fail, exit 1
    r = run_cli("--dir", str(SIMS))
    assert r.returncode == 1, r.stderr
    d = json.loads(r.stdout)
    bad = {t["name"] for t in d["testbenches"] if t["status"] != "pass"}
    assert bad == {"pd_trigger_zener_window_mutant_r7"}
    assert [v["measure"] for v in d["violations"]] == ["vtrip_grn"]
    # CLI single flagship: exit 0
    r0 = run_cli("--testbench", str(FLAGSHIP))
    assert r0.returncode == 0, r0.stderr
    # gate.py wiring: the same dir fails the sim gate with the same measure
    gates = gate.load_gates(GATES_YAML)
    report = gate.run_report_for_gate(gates["sim"], SIMS)
    res = gate.evaluate("sim", gates["sim"], report)
    assert res["status"] == "fail"
    assert [f["measure"] for f in res["failing"]] == ["vtrip_grn"]


@pytest.mark.smoke
def test_smoke_engine_error_is_violation_not_traceback(ngspice_dll, tmp_path):
    bad = tmp_path / "broken.cir"
    bad.write_text("* broken bench\nQQ this is not spice\n.dc V1 0 1 1\n.end\n",
                   encoding="utf-8")
    (tmp_path / "broken.bounds.json").write_text(
        '[{"measure": "m1", "min": 0}]', encoding="utf-8")
    rep = sim_run.run(tmp_path)
    assert rep["status"] == "violations"
    (v,) = rep["violations"]
    assert v["kind"] == "sim_engine_error" and v["severity"] == "error"
    assert "broken.cir" in v["msg"]


# -------------------------------------------------- adversarial regressions


def test_empty_bounds_sidecar_rejected(tmp_path):
    """A-1: an empty [] sidecar must fail loudly, not gate-pass silently."""
    p = tmp_path / "b.bounds.json"
    p.write_text("[]", encoding="utf-8")
    with pytest.raises(Exception, match="empty list"):
        simlib.load_bounds(p)


def test_nonfinite_bound_limits_rejected(tmp_path):
    """A-2: NaN/Infinity bound limits are silently-dead bounds - reject."""
    p = tmp_path / "b.bounds.json"
    p.write_text('[{"measure": "m", "min": NaN}]', encoding="utf-8")
    with pytest.raises(Exception, match="finite"):
        simlib.load_bounds(p)
    p.write_text('[{"measure": "m", "max": Infinity}]', encoding="utf-8")
    with pytest.raises(Exception, match="finite"):
        simlib.load_bounds(p)
    p.write_text('[{"measure": "m", "min": true}]', encoding="utf-8")
    with pytest.raises(Exception, match="finite"):
        simlib.load_bounds(p)
