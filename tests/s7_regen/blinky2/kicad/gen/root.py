"""Generator for the blinky2 schematic (S7 acceptance: regenerate golden
board 1's schematic via schlib; netlist must be electrically identical).

This file demonstrates the SPEC P4 generator-script pattern: the schematic
SOURCE is this Python; `../blinky2.kicad_sch` is BUILD OUTPUT. Rebuild:

    .venv/Scripts/python tests/s7_regen/blinky2/kicad/gen/root.py

Wiring facts (pins, nets, values) mirror the golden design module
tests/golden/generators/design_blinky2.py; in a real pipeline run they come
from datasheet-extract JSON (SPEC section 5 grounding rule) and the P2
architecture. `expect` entries pin the load-bearing pin names.

Emits ../decoupling.json (cap<->pin associations, the S4 check_decoupling
contract) as a side product of place_ic_with_decoupling.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[5]  # tests/s7_regen/blinky2/kicad/gen/root.py -> repo root
sys.path.insert(0, str(REPO / ".claude" / "skills" / "hwde" / "scripts"))

import schlib  # noqa: E402

# STM32F103C8Tx LQFP-48 pin map (pad -> net); NC = explicit no-connect.
U1_PINS = {
    "1": "+3V3", "2": "NC", "3": "NC", "4": "NC",
    "5": "OSC_IN", "6": "OSC_OUT", "7": "NRST", "8": "GND",
    "9": "+3V3", "10": "NC", "11": "NC", "12": "NC",
    "13": "NC", "14": "NC", "15": "LED", "16": "NC",
    "17": "NC", "18": "NC", "19": "NC", "20": "NC",
    "21": "NC", "22": "NC", "23": "GND", "24": "+3V3",
    "25": "NC", "26": "NC", "27": "NC", "28": "NC",
    "29": "NC", "30": "NC", "31": "NC", "32": "NC",
    "33": "NC", "34": "SWDIO", "35": "GND", "36": "+3V3",
    "37": "SWCLK", "38": "NC", "39": "NC", "40": "NC",
    "41": "NC", "42": "NC", "43": "NC", "44": "BOOT0",
    "45": "NC", "46": "NC", "47": "GND", "48": "+3V3",
}

C0603 = "Capacitor_SMD:C_0603_1608Metric"
C0805 = "Capacitor_SMD:C_0805_2012Metric"


def build() -> schlib.Sheet:
    sh = schlib.Sheet("blinky2", title="Golden 1: 2-layer STM32 blinky",
                      paper="A3", date="2026-07-06",
                      company="ai-ee golden corpus")

    # MCU + its decouplers (VDD_3=48, VDD_1=24, VDD_2=36, VDDA=9); the cap
    # row sits in free sheet area, matching the golden's layout.
    sh.place_ic_with_decoupling(
        "U1", "MCU_ST_STM32F1:STM32F103C8Tx", "STM32F103C8T6",
        at=(203.2, 152.4), pins=U1_PINS,
        footprint="Package_QFP:LQFP-48_7x7mm_P0.5mm",
        expect={"7": "NRST", "44": "BOOT0", "34": "PA13", "37": "PA14",
                "5": "PD0", "6": "PD1", "15": "PA5", "24": "VDD",
                "9": "VDDA", "1": "VBAT"},
        decoupling=[
            {"cap": "C1", "pin": "48", "rail": "+3V3", "value": "100nF",
             "footprint": C0603},
            {"cap": "C2", "pin": "24", "rail": "+3V3", "value": "100nF",
             "footprint": C0603},
            {"cap": "C3", "pin": "36", "rail": "+3V3", "value": "100nF",
             "footprint": C0603},
            {"cap": "C4", "pin": "9", "rail": "+3V3", "value": "100nF",
             "footprint": C0603},
        ],
        caps_at=(152.4, 240.03), caps_dx=15.24)

    # LDO: C5 = output cap (+3V3), C6 = input cap (+5V)
    sh.place_ic_with_decoupling(
        "U2", "Regulator_Linear:AMS1117-3.3", "AMS1117-3.3",
        at=(81.28, 60.96), pins={"1": "GND", "2": "+3V3", "3": "+5V"},
        footprint="Package_TO_SOT_SMD:SOT-223-3_TabPin2",
        expect={"1": "GND", "2": "VO", "3": "VI"},
        decoupling=[
            {"cap": "C5", "pin": "2", "rail": "+3V3", "value": "10uF",
             "footprint": C0805},
            {"cap": "C6", "pin": "3", "rail": "+5V", "value": "10uF",
             "footprint": C0805},
        ],
        caps_at=(99.06, 71.12), caps_dx=-35.56)

    # connectors, crystal, discretes
    sh.add_component("Connector_Generic:Conn_01x02", "J1", "PWR_5V",
                     at=(40.64, 60.96),
                     footprint="Connector_PinHeader_2.54mm:"
                               "PinHeader_1x02_P2.54mm_Vertical")
    sh.wire_pins("J1", {"1": "+5V", "2": "GND"})
    sh.add_component("Connector_Generic:Conn_01x04", "J2", "SWD",
                     at=(60.96, 152.4),
                     footprint="Connector_PinHeader_2.54mm:"
                               "PinHeader_1x04_P2.54mm_Vertical")
    sh.wire_pins("J2", {"1": "+3V3", "2": "SWDIO", "3": "SWCLK", "4": "GND"})
    sh.add_component("Device:Crystal_GND24", "Y1", "8MHz",
                     at=(281.94, 137.16),
                     footprint="Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm")
    sh.wire_pins("Y1", {"1": "OSC_IN", "2": "GND", "3": "OSC_OUT", "4": "GND"})
    for ref, val, at, pins in [
        ("C7", "22pF", (281.94, 152.4), {"1": "OSC_IN", "2": "GND"}),
        ("C8", "22pF", (297.18, 152.4), {"1": "OSC_OUT", "2": "GND"}),
        ("C9", "100nF", (139.7, 220.98), {"1": "NRST", "2": "GND"}),
    ]:
        sh.add_component("Device:C", ref, val, at=at, footprint=C0603)
        sh.wire_pins(ref, pins)
    sh.add_component("Device:R", "R1", "10k", at=(160.02, 220.98),
                     footprint="Resistor_SMD:R_0603_1608Metric")
    sh.wire_pins("R1", {"1": "BOOT0", "2": "GND"})
    sh.add_component("Device:R", "R2", "470", at=(281.94, 106.68),
                     footprint="Resistor_SMD:R_0603_1608Metric")
    sh.wire_pins("R2", {"1": "LED", "2": "LED_A"})
    sh.add_component("Device:LED", "D1", "LED_red", at=(299.72, 106.68),
                     footprint="LED_SMD:LED_0805_2012Metric")
    sh.wire_pins("D1", {"1": "GND", "2": "LED_A"})

    # power rails: GND and +5V clusters carry PWR_FLAG (externally driven);
    # +3V3 is driven by U2's power_out VO, so only a power symbol, hung off
    # C5 pin 1's stub end.
    sh.power_flag("GND", at=(40.64, 254.0), sym="power:GND", flag=True)
    sh.power_flag("+5V", at=(40.64, 264.16), sym="power:+5V", flag=True)
    sh.power_symbol_at_pin("C5", "1", "power:+3V3")
    return sh


def main(argv=None) -> int:
    out_dir = Path(argv[0]) if argv else HERE.parents[1]  # .../kicad
    try:
        sh = build()
        sch = sh.save(out_dir, project=True)
        meta = sh.emit_decoupling(out_dir / "decoupling.json")
    except Exception as exc:  # noqa: BLE001  (SPEC 6: any error -> exit 2)
        print(json.dumps({"script": "gen.blinky2", "status": "error",
                          "error": f"{type(exc).__name__}: {exc}"}))
        return 2
    print(json.dumps({
        "script": "gen.blinky2", "status": "pass",
        "files": [str(sch), str(out_dir / "blinky2.kicad_pro"), str(meta)],
        "decoupling_associations": len(sh.decoupling),
    }, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
