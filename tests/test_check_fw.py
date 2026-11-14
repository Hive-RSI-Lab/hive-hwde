"""check_fw: the board against its firmware's needs (hive-fw's fw constraints),
with a stub fw and a hand-written netlist, so no fw or KiCad is needed."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import check_fw  # noqa: E402

# U1 is the ESP32: pin 2 on +3V3; GPIO13/14 an I2C bus; GPIO12 a strap;
# GPIO36 an input-only pin.
NETS = {
    "+3V3": [("U1", "2"), ("R1", "1"), ("R2", "1"), ("R4", "1")],
    "GND": [("U1", "1")],
    "/I2C_SDA": [("U1", "16"), ("R1", "2")],
    "/I2C_SCL": [("U1", "13"), ("R2", "2")],
    "/I2S_DOUT": [("U1", "14")],
    "/IR_IN": [("U1", "5"), ("R4", "2")],
    "/ADC_SCLK": [("U1", "20")],   # an SPI clock: SCLK is not SCL, no pull-up wanted
}
PINS = [  # fw pins' rows: (gpio, firmware name, net)
    (13, "I2C_SDA", "I2C_SDA"), (14, "I2C_SCL", "I2C_SCL"),
    (12, "PIN_I2S_DOUT", "I2S_DOUT"), (36, "PIN_IR", "IR_IN"),
    (18, "PDAQ_PIN_ADC_SCLK", "ADC_SCLK")]


VALUES = {"U1": "ESP32-WROVER-E-N8R8"}


def netlist(path: Path, nets: dict, values: dict = VALUES) -> Path:
    comps = sorted({r for ms in nets.values() for r, _ in ms})
    out = ["(export (version \"E\")", " (components"]
    out += [f"  (comp (ref \"{r}\") (value \"{values.get(r, 'x')}\"))" for r in comps]
    out += [" )", " (nets"]
    for i, (name, ms) in enumerate(nets.items(), 1):
        nodes = " ".join(f"(node (ref \"{r}\") (pin \"{p}\") (pintype \"passive\"))" for r, p in ms)
        out.append(f"  (net (code \"{i}\") (name \"{name}\") {nodes})")
    out += [" )", ")"]
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return path


def report(problems=()) -> dict:
    return {
        "format": "fw-constraints/1", "target": "esp32",
        "module": {"chip": "esp32", "radio": {"wifi": True, "bt": False}},
        "peripherals": {"i2c_hz": 400000},
        "needs": [{"about": "power", "need": "a 3.3 V supply good for 500 mA or more at the module",
                   "from": "radio on"},
                  {"about": "module", "need": "an antenna keep-out", "from": "sdkconfig"}],
        "boards": [{"file": "b.net", "ref": "U1", "part": "ESP32-WROVER-E-N8R8",
                    "pins": {"pins": [{"gpio": g, "name": n, "net": net, "verdict": "pass",
                                       "why": "", "where": "x.h:1"} for g, n, net in PINS],
                             "unused": {}}}],
        "problems": list(problems), "ok": not problems,
    }


@pytest.fixture
def world(tmp_path):
    """A project with boards/demo (its netlist and constraints) and firmware/,
    and a stub hive-fw whose targets.json names boards/demo's netlist."""
    proj = tmp_path / "proj"
    kicad = proj / "boards" / "demo" / "kicad"
    kicad.mkdir(parents=True)
    (proj / "firmware").mkdir()
    netlist(kicad / "demo.net", NETS)
    (kicad / "constraints.json").write_text(json.dumps(
        {"power": [{"net": "+3V3", "current_a": 0.6}]}), encoding="utf-8")
    fw = tmp_path / "hive-fw"
    (fw / "bin").mkdir(parents=True)
    (fw / "targets.json").write_text(json.dumps({
        "demo-fw": {"project": "proj", "path": "firmware",
                    "pins": {"constraints": {"board": "../boards/demo/kicad/demo.net"}}},
        "other": {"project": "proj", "path": "firmware",
                  "pins": {"constraints": {"board": "../boards/other/kicad/other.net"}}}}),
        encoding="utf-8")
    (fw / "report.json").write_text(json.dumps(report()), encoding="utf-8")
    stub = fw / "bin" / "fw"
    stub.write_text(
        "#!/bin/sh\n"
        "here=$(cd \"$(dirname \"$0\")/..\" && pwd)\n"
        "if [ \"$1\" = help ]; then echo '  fw constraints [PROJECT]'; exit 0; fi\n"
        "echo \"$@\" >>\"$here/calls\"\n"
        "cat \"$here/report.json\"\n", encoding="utf-8")
    stub.chmod(0o755)
    return {"proj": proj, "board": proj / "boards" / "demo", "kicad": kicad, "fw": fw}


def run(world, *extra) -> tuple[int, dict]:
    out = world["proj"] / "out.json"
    rc = check_fw.main(["--board", str(world["board"]), "--fw", str(world["fw"] / "bin" / "fw"),
                        "--out", str(out), *extra])
    return rc, json.loads(out.read_text(encoding="utf-8"))


def checks(rep: dict) -> list[str]:
    return sorted(v["check"] for v in rep["violations"])


def test_board_that_meets_the_firmware_passes(world):
    rc, rep = run(world)
    assert rc == 0, rep
    assert rep["violations"] == []
    t, = rep["targets"]
    assert t["target"] == "demo-fw"  # "other" names another board's netlist
    assert any("antenna" in n["need"] for n in t["needs"])  # handed on to weigh


def test_the_firmware_read_is_the_one_beside_the_board(world):
    run(world)
    call = (world["fw"] / "calls").read_text(encoding="utf-8").split()
    assert call[:2] == ["constraints", str(world["proj"] / "firmware")]
    assert call[call.index("--board") + 1] == str(world["kicad"] / "demo.net")


def test_supply_budgeted_under_the_radio_peak_is_an_error(world):
    (world["kicad"] / "constraints.json").write_text(json.dumps(
        {"power": [{"net": "+3V3", "current_a": 0.3}]}), encoding="utf-8")
    rc, rep = run(world)
    assert rc == 1
    v, = rep["violations"]
    assert v["check"] == "fw_supply_budget" and v["severity"] == "error"
    assert "0.3 A" in v["msg"] and "0.5 A" in v["msg"]


def test_unbudgeted_supply_is_a_warning(world):
    (world["kicad"] / "constraints.json").write_text(json.dumps({"power": []}), encoding="utf-8")
    rc, rep = run(world)
    assert rc == 0
    assert [(v["check"], v["severity"]) for v in rep["violations"]] == [("fw_supply_budget", "warning")]


def test_pulls_and_floating_inputs(world):
    nets = {k: list(v) for k, v in NETS.items()}
    nets["+3V3"].remove(("R2", "1"))           # SCL's pull-up gone
    nets["/I2C_SCL"].remove(("R2", "2"))
    nets["+3V3"].remove(("R4", "1"))           # GPIO36 reaches nothing
    nets["/IR_IN"].remove(("R4", "2"))
    netlist(world["kicad"] / "demo.net", nets)
    rc, rep = run(world)
    assert rc == 1
    assert checks(rep) == ["fw_i2c_pullup", "fw_input_floating"]


def test_fws_own_problems_come_through(world):
    (world["fw"] / "report.json").write_text(json.dumps(report(problems=[
        {"about": "board", "code": "STRAP-12", "from": "b.net",
         "problem": "R3 pulls GPIO12 high at reset: a 3.3 V module selects 1.8 V flash"}])),
        encoding="utf-8")
    rc, rep = run(world)
    assert rc == 1
    assert checks(rep) == ["fw_problem"]
    v, = rep["violations"]
    assert v["code"] == "STRAP-12" and "STRAP-12: R3 pulls GPIO12" in v["msg"]


def test_named_target_runs_even_unmatched(world):
    rc, rep = run(world, "--target", "other")
    assert [t["target"] for t in rep["targets"]] == ["other"]


def test_fw_without_constraints_is_exit_2(world, capsys):
    (world["fw"] / "bin" / "fw").write_text("#!/bin/sh\necho '  fw pins'\n", encoding="utf-8")
    rc = check_fw.main(["--board", str(world["board"]), "--fw", str(world["fw"] / "bin" / "fw")])
    assert rc == 2
    assert "fw_unavailable" in capsys.readouterr().out


def test_esp32_board_no_target_names_has_nothing_to_judge(world):
    """The ESP32 is there but no firmware target names the board: not a
    checker error (exit 2), and not a pass either."""
    (world["fw"] / "targets.json").write_text("{}", encoding="utf-8")
    rc, rep = run(world)
    assert rc == 0
    assert rep["violations"] == [] and rep["esp32"] == ["U1"]
    assert "U1" in rep["nothing_to_judge"] and "give --target" in rep["nothing_to_judge"]
    assert not (world["fw"] / "calls").exists()


def test_board_without_an_esp32_needs_no_fw(world, monkeypatch, tmp_path):
    """The reported case: a board with no ESP32 on it exited 2 because no fw
    was installed. It has no firmware for fw to check."""
    netlist(world["kicad"] / "demo.net", NETS, values={"U1": "AM6254"})
    monkeypatch.setattr(check_fw, "find_fw", lambda given: None)
    out = tmp_path / "out.json"
    rc = check_fw.main(["--board", str(world["board"]), "--out", str(out)])
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert rc == 0 and rep["status"] == "pass"
    assert rep["esp32"] == [] and "no ESP32" in rep["nothing_to_judge"]


@pytest.mark.parametrize("field,text", [
    ("value", "ESP32-S3-WROOM-1-N8"),
    ("footprint", "RF_Module:ESP32-WROOM-32"),
    ("libsource", ("RF_Module", "ESP32-C3-MINI-1")),
    ("value", "esp32-wrover-e"),
])
def test_an_esp32_is_found_by_value_footprint_or_symbol(field, text):
    comp = {"value": "MCU", "footprint": "Package_QFP:LQFP-48", **{field: text}}
    parsed = {"components": {"U3": comp, "R1": {"value": "10k", "footprint": "R_0402"}}}
    assert check_fw.esp32_refs(parsed) == ["U3"]


def test_esp32_board_without_fw_is_still_exit_2(world, monkeypatch, capsys):
    monkeypatch.setattr(check_fw, "find_fw", lambda given: None)
    rc = check_fw.main(["--board", str(world["board"])])
    assert rc == 2
    out = capsys.readouterr().out
    assert "fw_unavailable" in out and "U1" in out


@pytest.mark.parametrize("name", ["hive-fw", "fw"])
def test_fw_is_found_in_a_modules_folder_under_either_name(tmp_path, monkeypatch, name):
    """H0 installs the module as hive-fw, other Hives as fw."""
    stub = tmp_path / "mods" / name / "bin" / "fw"
    stub.parent.mkdir(parents=True)
    stub.write_text("#!/bin/sh\n", encoding="utf-8")
    stub.chmod(0o755)
    monkeypatch.delenv("HWDE_FW", raising=False)
    monkeypatch.setenv("HIVE_MODULES", str(tmp_path / "mods"))
    assert check_fw.find_fw(None) == stub.resolve()


def test_fw_is_found_beside_this_module(tmp_path, monkeypatch):
    """No $HIVE_MODULES: fw installed in the same folder as hwde."""
    mods = tmp_path / "mods"
    scripts = mods / "pcb-flow" / "scripts"
    scripts.mkdir(parents=True)
    stub = mods / "fw" / "bin" / "fw"
    stub.parent.mkdir(parents=True)
    stub.write_text("#!/bin/sh\n", encoding="utf-8")
    stub.chmod(0o755)
    monkeypatch.delenv("HWDE_FW", raising=False)
    monkeypatch.delenv("HIVE_MODULES", raising=False)
    monkeypatch.setattr(check_fw, "HERE", scripts)
    assert check_fw.find_fw(None) == stub.resolve()
