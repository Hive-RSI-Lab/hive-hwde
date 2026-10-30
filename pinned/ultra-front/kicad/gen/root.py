"""ultra-front root (and only) sheet: the Ultra receiver's front panel.

Blocks (architecture/blocks.md): B1 core link J1 (20-pin FFC), B2 LCD J2
(12-pin FPC), B3 backlight Q1, B4 I2C expander U1 (TCA9535 @0x27), B5 EC11
encoder SW4, B6 buttons SW1-SW3, B7 IR receiver U2, B8 RGB status LED D1.
The four M2.5 mounting holes are board-only (board_init --mounting-holes).

Pin tables come from `schlib.py --pins` on lib/aiee.kicad_sym and the
datasheets in research notes (README "Sources"): TCA9535 pin 1 INT ... 24
VCC; IRM-H638T 1/2 GND, 3 OUT, 4 VCC; LED 1 common anode, 2 B, 3 G, 4 R;
TS-1187A A-B one contact, C-D the other; EC11 A/C/B quadrature (C common),
D/E push switch, F/G mounting lugs; LCD tail (HS20HS072RX datasheet section
6) 1 GND, 2 CS, 3 RS, 4 SCL, 5 SDA, 6 RST, 7 NC, 8 IOVCC, 9 VCC, 10 LED-A,
11 LED-K, 12 GND.

Rebuild (from the board workspace):
  eval "$(sh $HIVE_MODULES/hive-hwde/bin/hwde env)"
  PYTHONPATH=$HWDE_SKILL/scripts:$HWDE_SKILL/scripts/lib $HWDE_PY kicad/gen/root.py
Outputs: kicad/ultra-front.kicad_sch + .kicad_pro + kicad/decoupling.json
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
BOARD = HERE.parents[2]

import schlib  # noqa: E402

with contextlib.redirect_stdout(io.StringIO()), \
        contextlib.redirect_stderr(io.StringIO()):
    cache = schlib.ksa.get_symbol_cache()
    cache.add_library_path(str(BOARD / "lib" / "aiee.kicad_sym"))

NAME = "ultra-front"
R0603 = "aiee:R0603"
C0603 = "aiee:C0603"
C0805 = "aiee:C0805"

# J1 to core: requirements.md section 2. Pin N here = pin N on core.
J1_PINS = {
    "1": "GND", "2": "+3V3", "3": "+5V", "4": "GND", "5": "LCD_SCK",
    "6": "GND", "7": "LCD_MOSI", "8": "LCD_CS", "9": "LCD_DC",
    "10": "LCD_BL_PWM", "11": "GND", "12": "I2C_SCL", "13": "I2C_SDA",
    "14": "FRONT_INT_N", "15": "IR_OUT", "16": "ENC_A", "17": "ENC_B",
    "18": "+5V", "19": "+3V3", "20": "GND", "21": "GND", "22": "GND",
}
# J2 = LCD tail, straight through (pin N of connector = LCD pin N).
J2_PINS = {
    "1": "GND", "2": "LCD_CS", "3": "LCD_DC", "4": "LCD_SCK",
    "5": "LCD_MOSI", "6": "LCD_RST", "7": "NC", "8": "+3V3", "9": "+3V3",
    "10": "BL_A", "11": "BL_K", "12": "GND", "13": "GND", "14": "GND",
}
# Unused P06, P07, P14-P17 are pulled to GND together through R19 10k: a
# floating input toggles INT (the TCA9535 has no interrupt mask), and a pin
# set as an output by mistake fights 10k, not a hard short.
U1_PINS = {
    "1": "FRONT_INT_N", "2": "+3V3", "3": "+3V3", "21": "+3V3",  # A1 A2 A0
    "4": "ENC_A", "5": "ENC_B", "6": "ENC_SW", "7": "BTN1", "8": "BTN2",
    "9": "BTN3", "10": "EXP_UNUSED", "11": "EXP_UNUSED",          # P00-P07
    "13": "LCD_RST", "14": "LED_R", "15": "LED_G", "16": "LED_B",
    "17": "EXP_UNUSED", "18": "EXP_UNUSED", "19": "EXP_UNUSED",
    "20": "EXP_UNUSED",                                           # P10-P17
    "12": "GND", "22": "I2C_SCL", "23": "I2C_SDA", "24": "+3V3",
}


def resistor(sh, ref, value, at, a, b, rot=0, fp=R0603):
    sh.add_component("Device:R", ref, value, at=at, rotation=rot,
                     footprint=fp)
    sh.wire_pins(ref, {"1": a, "2": b})


def cap(sh, ref, value, at, a, b="GND", fp=C0603):
    sh.add_component("Device:C", ref, value, at=at, footprint=fp)
    sh.wire_pins(ref, {"1": a, "2": b})


def build() -> schlib.Sheet:
    sh = schlib.Sheet(NAME, title="ultra-front: Ultra front panel",
                      paper="A3", pwr_base=100)

    # B1 core link + bulk caps
    sh.add_component("aiee:FPC-05F-20PH20", "J1", "FPC-05F-20PH20",
                     at=(50.8, 101.6),
                     footprint="aiee:FPC-SMD_20P-P0.50_XUNPU_FPC-05F-20PH20")
    sh.wire_pins("J1", J1_PINS)
    cap(sh, "C2", "10uF", (30.48, 165.1), "+3V3", fp=C0805)
    cap(sh, "C3", "10uF", (43.18, 165.1), "+5V", fp=C0805)

    # B4 expander + its decoupler
    sh.place_ic_with_decoupling(
        "U1", "aiee:TCA9535PWR", "TCA9535PWR", at=(152.4, 101.6),
        pins=U1_PINS, footprint="aiee:TSSOP-24_L7.8-W4.4-P0.65-LS6.4-BL",
        decoupling=[{"cap": "C1", "pin": "24", "rail": "+3V3",
                     "value": "100nF", "footprint": C0603}],
        caps_at=(132.08, 139.7),
        expect={"1": "INT", "12": "GND", "21": "A0", "22": "SCL",
                "23": "SDA", "24": "VCC", "4": "P00", "13": "P10"})
    resistor(sh, "R7", "10k", (147.32, 139.7), "+3V3", "FRONT_INT_N")
    resistor(sh, "R19", "10k", (160.02, 139.7), "EXP_UNUSED", "GND")

    # B2 LCD connector, VCC decoupler, reset pull-up
    sh.add_component("aiee:FPC-05FB-12PH20", "J2", "FPC-05FB-12PH20",
                     at=(254.0, 101.6),
                     footprint="aiee:FPC-SMD_12P-P0.50_XUNPU_FPC-05FB-12PH20")
    sh.wire_pins("J2", J2_PINS)
    cap(sh, "C4", "100nF", (233.68, 139.7), "+3V3")
    resistor(sh, "R8", "10k", (246.38, 139.7), "+3V3", "LCD_RST")

    # B3 backlight: +5V -> R13 || R17 || R18 (3 x 120R = 40R) -> LED-A;
    # LED-K -> Q1 -> GND. 3 resistors keep each 0603 at <= 50 mW.
    for ref, x in (("R13", 290.83), ("R17", 303.53), ("R18", 316.23)):
        resistor(sh, ref, "120R", (x, 76.2), "+5V", "BL_A")
    sh.add_component("aiee:AO3400A", "Q1", "AO3400A", at=(304.8, 106.68),
                     footprint="aiee:SOT-23-3_L2.9-W1.3-P1.90-LS2.4-BR",
                     expect={"1": "G", "2": "S", "3": "D"})
    sh.wire_pins("Q1", {"1": "BL_GATE", "2": "GND", "3": "BL_K"})
    resistor(sh, "R12", "100R", (281.94, 121.92), "LCD_BL_PWM", "BL_GATE")
    resistor(sh, "R11", "100k", (294.64, 121.92), "BL_GATE", "GND")

    # B5 encoder: A/B/SW pull-ups, A/B debounce
    sh.add_component("aiee:EC11E1834403", "SW4", "EC11E1834403",
                     at=(76.2, 210.82), footprint="aiee:SW-TH_EC11E1820402",
                     expect={"A": "A", "B": "B", "C": "C", "D": "D",
                             "E": "E", "F": "F", "G": "G"})
    sh.wire_pins("SW4", {"A": "ENC_A", "B": "ENC_B", "C": "GND",
                         "D": "ENC_SW", "E": "GND", "F": "GND", "G": "GND"})
    resistor(sh, "R1", "10k", (109.22, 196.85), "+3V3", "ENC_A")
    resistor(sh, "R2", "10k", (121.92, 196.85), "+3V3", "ENC_B")
    resistor(sh, "R3", "10k", (134.62, 196.85), "+3V3", "ENC_SW")
    cap(sh, "C6", "10nF", (109.22, 222.25), "ENC_A")
    cap(sh, "C7", "10nF", (121.92, 222.25), "ENC_B")

    # B6 buttons: A/B = button net, C/D = GND; 10 k pull-ups
    for i, (ref, x) in enumerate((("SW1", 165.1), ("SW2", 190.5),
                                  ("SW3", 215.9)), start=1):
        sh.add_component("aiee:TS-1187A-B-A-B", ref, "TS-1187A-B-A-B",
                         at=(x, 210.82),
                         footprint="aiee:SW-SMD_4P-L5.1-W5.1-P3.70-LS6.5-TL_H1.5",
                         expect={"1": "A", "2": "B", "3": "C", "4": "D"})
        sh.wire_pins(ref, {"1": f"BTN{i}", "2": f"BTN{i}", "3": "GND",
                           "4": "GND"})
        resistor(sh, f"R{3 + i}", "10k", (x, 237.49), "+3V3", f"BTN{i}")

    # B7 IR receiver with its RC supply filter and output pull-up
    sh.add_component("aiee:IRM-H638T_TR2", "U2", "IRM-H638T/TR2",
                     at=(254.0, 203.2),
                     footprint="aiee:SENSOR-SMD_IRM-H638T-TR2-TL",
                     expect={"1": "GND", "2": "GND", "3": "OUT", "4": "VCC"})
    sh.wire_pins("U2", {"1": "GND", "2": "GND", "3": "IR_OUT",
                        "4": "IR_VCC"})
    resistor(sh, "R10", "100R", (281.94, 190.5), "+3V3", "IR_VCC")
    cap(sh, "C5", "4.7uF", (294.64, 190.5), "IR_VCC")
    resistor(sh, "R9", "10k", (281.94, 220.98), "+3V3", "IR_OUT")

    # B8 RGB status LED: common anode on +5V, cathodes sink into U1
    sh.add_component("aiee:TJ-S1615SW6TGLCCYRGB-A5", "D1",
                     "TJ-S1615SW6TGLCCYRGB-A5", at=(330.2, 203.2),
                     footprint="aiee:LED-SMD_4P-L1.6-W1.5-TR",
                     expect={"2": "B", "3": "G", "4": "R"})
    sh.wire_pins("D1", {"1": "+5V", "2": "LEDB_K", "3": "LEDG_K",
                        "4": "LEDR_K"})
    resistor(sh, "R14", "1k", (317.5, 236.22), "LED_R", "LEDR_K")
    resistor(sh, "R15", "680R", (330.2, 236.22), "LED_G", "LEDG_K")
    resistor(sh, "R16", "680R", (342.9, 236.22), "LED_B", "LEDB_K")

    # Rails enter on passive connector pins: PWR_FLAG marks them driven.
    sh.power_flag("+3V3", at=(30.48, 45.72), sym="power:+3V3", flag=True)
    sh.power_flag("+5V", at=(30.48, 55.88), sym="power:+5V", flag=True)
    sh.power_flag("GND", at=(30.48, 66.04), sym="power:GND", flag=True)
    # IR_VCC is U2's power_in fed through R10 (the RC filter): flag it.
    sh.power_flag("IR_VCC", at=(307.34, 177.8), sym=None, flag=True)
    return sh


def main(argv=None) -> int:
    out_dir = Path(argv[0]) if argv else HERE.parents[1]  # .../kicad
    try:
        sh = build()
        sch = sh.save(out_dir)
        # Bulk caps at J1 hold the rails up at their source; C4 is the LCD's
        # own decoupler at J2 (its VCC/IOVCC pins are on the tail).
        sh.emit_decoupling(out_dir / "decoupling.json", extra=[
            {"cap": "C2", "ic": "J1", "pin": "2", "rail": "+3V3",
             "value": "10uF"},
            {"cap": "C3", "ic": "J1", "pin": "3", "rail": "+5V",
             "value": "10uF"},
            {"cap": "C4", "ic": "J2", "pin": "9", "rail": "+3V3",
             "value": "100nF"},
            {"cap": "C5", "ic": "U2", "pin": "4", "rail": "/IR_VCC",
             "value": "4.7uF"}])
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"script": "gen.root", "status": "error",
                          "error": f"{type(exc).__name__}: {exc}"}))
        return 2
    print(json.dumps({"script": "gen.root", "status": "pass",
                      "root": str(sch),
                      "decoupling_associations": len(sh.decoupling)},
                     indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
