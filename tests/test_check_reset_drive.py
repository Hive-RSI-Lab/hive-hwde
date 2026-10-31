"""check_reset_drive (issue #339): what an MCU's pins drive before firmware
runs, on synthetic netlists (docs/ingest/test-fw-mech.md's board cases)."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "lib"))
sys.path.insert(0, str(REPO / "tests"))
import check_reset_drive  # noqa: E402
import verify_all  # noqa: E402
from test_antenna_straps import _by_kind, _netlist  # noqa: E402


def _s3(tmp_path, comps: dict, nets: dict, mcu="ESP32-S3-WROOM-1-N8"):
    c = {"U1": mcu, **comps}
    n = {"+3V3": [("U1", "2", "3V3_2")], "GND": [("U1", "1", "GND_1")]}
    for name, nodes in nets.items():
        n.setdefault(name, []).extend(nodes)
    pcb = _netlist(tmp_path, c, n)
    payload, _ = check_reset_drive.run(["--pcb", str(pcb), "--netlist",
                                        str(tmp_path / "s.net")])
    return _by_kind(payload), payload


def _heater(pin="5", io="IO5", pulldown=None):
    comps = {"R1": "100R", "Q1": "2N7002"}
    nets = {"/HEAT_IO": [("U1", pin, f"{io}_{pin}"), ("R1", "1", "")],
            "/HEAT_G": [("R1", "2", ""), ("Q1", "1", "G")],
            "GND": [("Q1", "2", "S")], "/HEAT_LS": [("Q1", "3", "D")]}
    if pulldown:
        comps["R2"] = pulldown
        nets["/HEAT_G"].append(("R2", "1", ""))
        nets["GND"].append(("R2", "2", ""))
    return comps, nets


def test_floating_low_side_gate_fails(tmp_path):
    """The board case: GPIO5 to a low-side N-FET heater gate through 100 R
    with no pull-down."""
    k, _ = _s3(tmp_path, *_heater())
    (v,) = k["gate_floating"]
    assert v["severity"] == "error" and v["refs"] == ["Q1", "U1"]
    assert v["net"] == "/HEAT_G" and "ground" in v["msg"]
    # GPIO5 glitches low, which leaves an N-FET off
    assert "drive_glitch" not in k


def test_gate_pull_down_passes(tmp_path):
    k, payload = _s3(tmp_path, *_heater(pulldown="10k"))
    assert payload["status"] == "pass", payload["violations"]
    (load,) = payload["checked"][0]["loads"]
    assert load["kind"] == "gate" and "low" in load["por"]


def test_gate_divider_past_vth_fails(tmp_path):
    """A 10 k pull-up against a 10 k pull-down: 1.65 V, past the 2N7002's
    1.0 V VGS(th) min."""
    comps, nets = _heater(pulldown="10k")
    comps["R3"] = "10k"
    nets["/HEAT_G"].append(("R3", "1", ""))
    nets["+3V3"] = [("R3", "2", "")]
    k, _ = _s3(tmp_path, comps, nets)
    (v,) = k["gate_on_at_reset"]
    assert v["severity"] == "error" and abs(v["volts"] - 1.65) < 0.01


def test_usb_dplus_on_a_load_switch_enable_fails(tmp_path):
    """The second board case: a pull-down on the enable, but on GPIO20
    (USB_D+, held high for up to ~170 ms): the glitch is named."""
    comps = {"U2": "TPS22918", "R2": "100k"}
    nets = {"/LOAD_EN": [("U1", "20", "IO20_20"), ("U2", "3", "ON_3"),
                         ("R2", "1", "")],
            "GND": [("R2", "2", "")]}
    k, _ = _s3(tmp_path, comps, nets)
    (v,) = k["drive_glitch"]
    assert v["severity"] == "error" and v["pin"] == "IO20"
    assert "USB_D+" in v["msg"] and v["part_pin"] == "ON"
    assert "enable_floating" not in k


def test_low_glitch_on_a_p_fet_gate_fails(tmp_path):
    """GPIO1-17 glitch low for 60 us: a P-FET high-side switch whose gate
    is pulled up to its source sees it as on."""
    comps = {"Q1": "AO3401", "R2": "10k"}
    nets = {"/HS_G": [("U1", "7", "IO7_7"), ("Q1", "1", "G"),
                      ("R2", "1", "")],
            "+3V3": [("Q1", "2", "S"), ("R2", "2", "")],
            "/HS_OUT": [("Q1", "3", "D")]}
    k, payload = _s3(tmp_path, comps, nets)
    (v,) = k["drive_glitch"]
    assert v["glitch"] == "low" and v["severity"] == "error"
    assert set(k) == {"drive_glitch"}  # its pull-up holds it off otherwise


def test_floating_enable_warns_and_undefined_fails(tmp_path):
    k, _ = _s3(tmp_path, {"U2": "TPS22918"},
               {"/LOAD_EN": [("U1", "5", "IO5_5"), ("U2", "3", "EN_3")]})
    (v,) = k["enable_floating"]
    assert v["severity"] == "warning" and "ground" in v["msg"]
    tmp2 = tmp_path / "b"
    tmp2.mkdir()
    k, _ = _s3(tmp2, {"U2": "TPS22918", "R2": "10k", "R3": "10k"},
               {"/LOAD_EN": [("U1", "5", "IO5_5"), ("U2", "3", "EN_3"),
                             ("R2", "1", ""), ("R3", "1", "")],
                "GND": [("R2", "2", "")], "+3V3": [("R3", "2", "")]})
    (v,) = k["enable_undefined"]
    assert v["severity"] == "error"


def test_active_low_shutdown_pulled_down_passes(tmp_path):
    """lite-main's shape: a TPA3118's SDZ pulled down through 100 k, from
    GPIO21 through 10 k."""
    comps = {"U2": "TPA3118D2", "R1": "10k", "R2": "100k"}
    nets = {"/SDZ_IO": [("U1", "21", "IO21_21"), ("R1", "1", ""),
                        ("R2", "1", "")],
            "/SDZ": [("R1", "2", ""), ("U2", "2", "SDZ")],
            "GND": [("R2", "2", "")]}
    k, payload = _s3(tmp_path, comps, nets, mcu="ESP32-WROVER-E-N8R8")
    assert payload["status"] == "pass", payload["violations"]
    (load,) = payload["checked"][0]["loads"]
    assert load["part_pin"] == "SDZ"


def test_boot_log_pin_drives_an_enable(tmp_path):
    """U0TXD toggles with the ROM boot log: a pulled-down enable on it
    still switches."""
    comps = {"U2": "TPS22918", "R2": "100k"}
    nets = {"/TX_EN": [("U1", "35", "U0TXD_35"), ("U2", "3", "EN_3"),
                       ("R2", "1", "")], "GND": [("R2", "2", "")]}
    k, _ = _s3(tmp_path, comps, nets, mcu="ESP32-WROVER-E-N8R8")
    (v,) = k["drive_glitch"]
    assert v["pin"] == "IO1" and v["glitch"] == "toggle"


def test_c3_nanosecond_glitch_only_warns(tmp_path):
    comps, nets = _heater(pin="7", io="GPIO7", pulldown="10k")
    k, _ = _s3(tmp_path, comps, nets, mcu="ESP32-C3-WROOM-02-N4")
    (v,) = k["drive_glitch"]
    assert v["severity"] == "warning" and v["glitch_us"] < 1


def test_chip_pulled_pin_is_left_to_check_straps(tmp_path):
    """C3 GPIO6's reset pull-up on a bare gate is check_straps'
    load_on_at_reset; this check doesn't repeat it as floating or on."""
    comps, nets = _heater(pin="6", io="GPIO6")
    k, _ = _s3(tmp_path, comps, nets, mcu="ESP32-C3-WROOM-02-N4")
    assert not ({"gate_floating", "gate_on_at_reset"} & set(k))


def test_bjt_base_floating_warns(tmp_path):
    comps = {"R1": "1k", "Q1": "MMBT3904"}
    nets = {"/LED_IO": [("U1", "5", "IO5_5"), ("R1", "1", "")],
            "/LED_B": [("R1", "2", ""), ("Q1", "1", "B")],
            "GND": [("Q1", "2", "E")], "/LED_K": [("Q1", "3", "C")]}
    k, _ = _s3(tmp_path, comps, nets)
    (v,) = k["gate_floating"]
    assert v["severity"] == "warning" and "base" in v["msg"]


def test_fet_on_an_unnamed_rail_is_skipped(tmp_path):
    comps = {"Q1": "AO3401"}
    nets = {"/G": [("U1", "5", "IO5_5"), ("Q1", "1", "G")],
            "/PVDD": [("Q1", "2", "S")], "/OUT": [("Q1", "3", "D")]}
    k, payload = _s3(tmp_path, comps, nets)
    assert payload["status"] == "pass"


def test_joins_verify_all():
    assert "check_reset_drive" in {c["name"] for c in verify_all.CHECKS}
