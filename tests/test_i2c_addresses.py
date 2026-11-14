"""reference/i2c_addresses.yaml and netlist_audit's i2c_address_collision
rule (#1034). Seeded from glasgow-2: rev A plants the fault, rev B (the
fix) must stay quiet."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "lib"))

import netlist_audit  # noqa: E402


def _m(ref, pin, func="", ptype="passive"):
    return {"ref": ref, "pin": pin, "pintype": ptype, "pinfunction": func}


def _audit(comps, nets):
    parsed = {"components": {r: {"value": v, "libsource": ("", "")}
                             for r, v in comps.items()},
              "nets": nets}
    viol, facts = netlist_audit.audit(parsed, {}, None, pin_requirements=[],
                                      pin_states=[])
    return [x for x in viol if x["kind"] == "i2c_address_collision"], facts


def _glasgow(tmp_add0, dac_a0="GND", exp_addr=("GND", "+3V3")):
    """glasgow's bus: TMP112 U3 (ADD0 on `tmp_add0`), DAC43204 U21 (A0 on
    `dac_a0`) and two PCA6416As U24/U25 on /SCL, /SDA."""
    comps = {"U3": "TMP112xxDRL", "U21": "DAC43204",
             "U24": "PCA6416APW", "U25": "PCA6416APW"}
    nets = {"/SCL": [_m("U3", "1", "SCL", "input"),
                     _m("U21", "6", "SCL/~{SYNC}", "input"),
                     _m("U24", "22", "SCL", "input"),
                     _m("U25", "22", "SCL", "input")],
            "/SDA": [_m("U3", "6", "SDA", "bidirectional"),
                     _m("U21", "8", "SDA/SCLK", "bidirectional"),
                     _m("U24", "23", "SDA", "bidirectional"),
                     _m("U25", "23", "SDA", "bidirectional")],
            "+3V3": [_m("U3", "5", "V+", "power_in"),
                     _m("U24", "2", "VDD_I2C", "power_in"),
                     _m("U25", "2", "VDD_I2C", "power_in")],
            "VDDA": [_m("U21", "15", "VDD", "power_in")],
            "GND": [_m("U3", "2", "GND", "power_in"),
                    _m("U21", "14", "AGND", "power_in")]}
    nets.setdefault(tmp_add0, []).append(_m("U3", "4", "ADD0", "input"))
    nets.setdefault(dac_a0, []).append(_m("U21", "7", "A0/SDI", "input"))
    for u, n in zip(("U24", "U25"), exp_addr):
        nets.setdefault(n, []).append(_m(u, "21", "ADDR", "input"))
    return comps, nets


def test_glasgow_revc2_tmp112_and_dac_both_at_0x48_is_the_planted_fault():
    hits, facts = _audit(*_glasgow("GND"))
    assert [(h["refs"], h["net"], h["address"], h["severity"])
            for h in hits] == [(["U21", "U3"], "/SCL", "0x48", "error")]
    assert "SBOS473L" in hits[0]["msg"] and "SLASEX3A" in hits[0]["msg"]
    assert facts["i2c_buses_checked"] == 1
    assert facts["i2c_devices_placed"] == 4


def test_glasgow_tmp112_on_its_own_supply_is_quiet():
    # the fix: ADD0 to V+ (+3V3) puts the TMP112 at 0x49
    assert _audit(*_glasgow("+3V3"))[0] == []


@pytest.mark.parametrize("strap,clash", [("/SDA", "0x4A"), ("/SCL", "0x4B"),
                                         ("VDDA", "0x49")])
def test_sda_scl_and_supply_straps(strap, clash):
    # the TMP112 strapped to SDA/SCL/its V+ net, the DAC to the same kind
    tmp = {"VDDA": "+3V3"}.get(strap, strap)
    hits, _ = _audit(*_glasgow(tmp, dac_a0=strap))
    assert [h["address"] for h in hits] == [clash]


def test_two_expanders_both_low_collide():
    hits, _ = _audit(*_glasgow("+3V3", exp_addr=("GND", "GND")))
    assert [(h["refs"], h["address"]) for h in hits] == [
        (["U24", "U25"], "0x20")]


def test_a_strap_it_cannot_read_is_left_out():
    comps, nets = _glasgow("/ALERT_MCU")
    nets["/ALERT_MCU"].append(_m("U9", "3", "PA1", "bidirectional"))
    comps["U9"] = "STM32G0"
    hits, facts = _audit(comps, nets)
    assert hits == [] and facts["i2c_devices_placed"] == 3


def test_separate_buses_do_not_collide():
    comps, nets = _glasgow("GND")
    nets["/SCL2"] = [m for m in nets["/SCL"] if m["ref"] == "U21"]
    nets["/SCL"] = [m for m in nets["/SCL"] if m["ref"] != "U21"]
    hits, facts = _audit(comps, nets)
    assert hits == [] and facts["i2c_buses_checked"] == 2


def test_every_row_cites_its_datasheet():
    doc = yaml.safe_load(
        (REPO / "reference" / "i2c_addresses.yaml").read_text())
    for row in doc["devices"]:
        assert row["verified"] is True and row["match"], row["mpn"]
        assert row["source"].strip() and row["pin"]
        assert set(row["addresses"]) <= {"ground", "supply", "high",
                                         "sda", "scl"}
        assert all(0x08 <= a <= 0x77 for a in row["addresses"].values())
