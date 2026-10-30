"""lite-main schematic generator (hwde P4).

The schematic SOURCE: run it to (re)write kicad/lite-main.kicad_sch,
kicad/decoupling.json and parts/bom-lines.json (the ref -> LCSC map that
parts.json is built from). One flat root sheet; blocks are grouped by position.
Every pin number below comes from the part's datasheet (paths in
parts/datasheets.md); `expect` checks it against the pulled symbol's pin name.

    eval "$(hwde env)"
    PYTHONPATH=$HWDE_SKILL/scripts:$HWDE_SKILL/scripts/lib $HWDE_PY kicad/gen/root.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import schlib

WS = Path(__file__).resolve().parents[2]          # boards/lite-main
LIB = WS / "lib" / "aiee.kicad_sym"
BOARD = "lite-main"

# ------------------------------------------------------------------ library
# kicad-sch-api does not read the project's sym-lib-table: register the
# pulled library with its cache, or every aiee: lib_id is "not found".
with schlib._quiet():
    schlib.ksa.get_symbol_cache().add_library_path(str(LIB))
_sym_text = LIB.read_text(encoding="utf-8")


def _lib_by_lcsc() -> dict:
    """LCSC id -> (symbol name, footprint) from the pulled library."""
    out = {}
    for m in re.finditer(r'\n  \(symbol "([^"]+)"(.*?)\n  \)', _sym_text, re.S):
        name, body = m.group(1), m.group(2)
        lc = re.search(r'\(property\s+"LCSC Part"\s+"([^"]+)"', body)
        fp = re.search(r'\(property\s+"Footprint"\s+"([^"]+)"', body)
        if lc:
            out[lc.group(1)] = (name, fp.group(1) if fp else "")
    return out


LIBMAP = _lib_by_lcsc()

# passives: KiCad stock symbols and footprints (their courtyards are right;
# EasyEDA's 0603 courtyards are the body outline - hwde-led-test LEARNINGS)
FP = {"0603R": "Resistor_SMD:R_0603_1608Metric",
      "0603": "Capacitor_SMD:C_0603_1608Metric",
      "0805": "Capacitor_SMD:C_0805_2012Metric",
      "1206": "Capacitor_SMD:C_1206_3216Metric"}

# value -> (LCSC, footprint key); all JLC Basic
R = {"0R": "C21189", "10R": "C22859", "100R": "C22775", "1k": "C21190",
     "2.2k": "C4190", "10k": "C25804", "13k": "C22797", "20k": "C4184", "22k": "C31850",
     "33k": "C4216", "47k": "C25819", "100k": "C25803", "120k": "C25808",
     "150k": "C22807", "15k": "C22809", "220k": "C22961"}
C = {"4.7uF/50V": ("C29823", "1206"), "10uF/50V": ("C13585", "1206"), "22pF": ("C1653", "0603"), "100pF": ("C14858", "0603"),
     "1nF": ("C1588", "0603"), "100nF": ("C14663", "0603"),
     "220nF": ("C21120", "0603"), "1uF": ("C15849", "0603"),
     "1uF/0805": ("C28323", "0805"), "1uF/1206": ("C1848", "1206"),
     "4.7uF": ("C19666", "0603"), "10uF": ("C19702", "0603"),
     "10uF/25V": ("C15850", "0805"), "22uF/25V": ("C45783", "0805"),
     "47pF": ("C1671", "0603"), "470pF": ("C1620", "0603"), "47nF": ("C1622", "0603"),
     "2.2uF/50V": ("C377773", "0805")}

BOM: list[dict] = []          # ref -> LCSC lines for parts.json
sheet = schlib.Sheet(BOARD, title="lite-main: hifi-receiver-2 Lite", paper="A0",
                     rev="A", date="2026-10-28", company="hifi-receiver-2",
                     pwr_base=1)

# ------------------------------------------------------------------ layout
# Components are packed left to right in rows inside each block's region.
GRID = 1.27


def g(v: float) -> float:
    return round(round(v / GRID) * GRID, 4)


class Region:
    def __init__(self, x, y, w):
        self.x0, self.y0, self.w = x, y, w
        self.x, self.y, self.row_h = x, y, 0.0

    def slot(self, w, h):
        if self.x + w > self.x0 + self.w and self.x > self.x0:
            self.x, self.y, self.row_h = self.x0, self.y + self.row_h, 0.0
        at = (g(self.x + w / 2), g(self.y + h / 2))
        self.x += w
        self.row_h = max(self.row_h, h)
        return at


def _bbox(lib_id: str):
    """Pin extent of a symbol (relative to its anchor), from the library."""
    name = lib_id.split(":", 1)[1]
    m = re.search(r'\n  \(symbol "%s"(.*?)\n  \)' % re.escape(name), _sym_text, re.S)
    xs, ys = [0.0], [0.0]
    if m:
        for pm in re.finditer(r'\(pin\s+\w+\s+\w+\s*\(at\s+([-\d.]+)\s+([-\d.]+)', m.group(1)):
            xs.append(float(pm.group(1)))
            ys.append(float(pm.group(2)))
    return min(xs), max(xs), min(ys), max(ys)


def ic(region, ref, lcsc, value, pins, expect=None, role="", decoupling=None, fp=None):
    name, lib_fp = LIBMAP[lcsc]
    fp = fp or lib_fp
    lib_id = f"aiee:{name}"
    x0, x1, y0, y1 = _bbox(lib_id)
    w, h = (x1 - x0) + 30, (y1 - y0) + 20
    cx, cy = region.slot(w, h)
    at = (g(cx - (x0 + x1) / 2), g(cy + (y0 + y1) / 2))
    sheet.add_component(lib_id, ref, value, at, footprint=fp, expect=expect,
                        fields={"LCSC": lcsc})
    sheet.wire_pins(ref, pins)
    BOM.append({"ref": ref, "lcsc": lcsc, "value": value, "role": role})


def r(region, ref, value, a, b, role=""):
    at = region.slot(15.24, 20.32)
    sheet.add_component("Device:R", ref, value, at, footprint=FP["0603R"],
                        fields={"LCSC": R[value]})
    sheet.wire_pins(ref, {"1": a, "2": b})
    BOM.append({"ref": ref, "lcsc": R[value], "value": value, "role": role})


DECOUP: list[dict] = []


def c(region, ref, value, a, b="GND", role="", ic_ref=None, pin=None, **extra):
    lcsc, pkg = C[value]
    at = region.slot(15.24, 20.32)
    sheet.add_component("Device:C", ref, value.split("/")[0], at,
                        footprint=FP[pkg], fields={"LCSC": lcsc})
    sheet.wire_pins(ref, {"1": a, "2": b})
    BOM.append({"ref": ref, "lcsc": lcsc, "value": value, "role": role})
    if ic_ref:
        ent = {"cap": ref, "ic": ic_ref, "pin": str(pin), "rail": a,
               "value": value.split("/")[0]}
        if b != "GND":
            ent["gnd"] = b
        ent.update(extra)
        DECOUP.append(ent)


def rails(region, names):
    for n in names:
        at = region.slot(12.7, 7.62)
        sym = {"+5V": "power:+5V", "+3V3": "power:+3V3", "GND": "power:GND",
               "VBUS": "power:VBUS"}.get(n, "power:VCC")
        sheet.power_flag(n, at=at, sym=sym, flag=True)


# ------------------------------------------------------------------ blocks
# Regions on an A0 sheet (1189 x 841 mm).
B_PWR = Region(20, 20, 560)
B_MCU = Region(600, 20, 560)
B_AUD = Region(20, 330, 560)
B_AMP = Region(600, 330, 560)
B_IO = Region(20, 640, 1140)

# ---- B1 USB-C, TVS, CH224Q ------------------------------------------------
# rails: driven by the connector / regulators; power symbols make them global
rails(B_PWR, ["VBUS", "PVDD", "+5V", "+3V3", "+3V3A", "GND"])

# HRO TYPE-C-31-M-12 (C165948), 20 V / 5 A: the USB4105-GF-A-120 had 0 free at
# JLC on 2026-10-29 (docs/bom-stock.md). Same pad positions as the USB4105
# (pads within 0.09 mm, pegs within 0.09 mm); Lite asks for 20 V only.
ic(B_PWR, "J1", "C165948", "TYPE-C-31-M-12",
   {"A4B9": "VBUS", "B4A9": "VBUS", "A1B12": "GND", "B1A12": "GND",
    "A5": "CC1", "B5": "CC2", "A6": "USB_DP", "B6": "USB_DP", "A7": "USB_DM",
    "B7": "USB_DM", "A8": "NC", "B8": "NC", "1": "GND", "2": "GND", "3": "GND",
    "4": "GND"},
   expect={"A5": "CC1", "B7": "DN2", "A4B9": "VBUS"}, role="USB-C 20 V/5 A, power + CH340B data")
# CH224A ESSOP-10 (datasheet V2.0 table 4-1: same pins as the CH224Q). The
# CH224Q's 2 x 2 mm DFN at 0.4 mm pitch could not be routed on 2 layers under
# the USB-C (its VBUS pin 8 sits between CC1 and CFG1); the CH224A is the same
# die in a 1.0 mm-pitch package, same price and stock (9.5k).
# CC1/CC2 are swapped against the receptacle (the CH224 treats them alike), so
# the two CC lines run straight down from the USB-C pads without crossing.
ic(B_PWR, "U1", "C42459160", "CH224A",
   {"1": "VBUS", "8": "VBUS", "11": "GND", "7": "CC2", "6": "CC1",
    "4": "NC", "5": "NC", "9": "PD_CFG1", "2": "PD_SCL", "3": "PD_SDA",
    "10": "PD_PG"},
   expect={"1": "VHV", "7": "CC1", "9": "CFG1", "10": "PG"}, role="USB PD sink, asks for 20 V")
c(B_PWR, "C1", "1uF", "VBUS", ic_ref="U1", pin=1, role="CH224Q VHV")
r(B_PWR, "R1", "120k", "PD_CFG1", "GND", role="CH224Q Rset: 20 V (table 5-1)")
r(B_PWR, "R2", "0R", "PD_SCL", "I2C_SCL", role="link: CH224Q I2C onto the codec bus (depopulate if its pull-up exceeds 3.6 V)")
r(B_PWR, "R3", "0R", "PD_SDA", "I2C_SDA", role="link: CH224Q I2C onto the codec bus")
r(B_PWR, "R4", "10k", "PD_PG", "+3V3", role="PG pull-up")
# SMBJ20A: breakdown 22.2-24.5 V sits above PD 20 V +5 %, clamps nearer the
# 30 V abs-max of TPS54302/TPA3118 than a 24 V part (review 2026-10-28)
ic(B_PWR, "D1", "C364296", "SMBJ20A", {"1": "VBUS", "2": "GND"},  # pin 1 = cathode bar
   role="VBUS TVS 20 V standoff")

# ---- B2 PVDD switch ---------------------------------------------------------
ic(B_PWR, "Q1", "C5125949", "AO4485",
   {"1": "VBUS", "2": "VBUS", "3": "VBUS", "4": "PSW_G",
    "5": "PVDD", "6": "PVDD", "7": "PVDD", "8": "PVDD"},
   role="PVDD high-side switch")
r(B_PWR, "R5", "100k", "VBUS", "PSW_G", role="Q1 gate-source")
# soft start: Miller cap gate-drain. Gate pulled at ~0.36 mA (17 V / 47k) into
# 100 nF gives ~3.6 V/ms on PVDD, ~1.6 A into the 690 uF bulk, 6 ms ramp.
r(B_PWR, "R15", "1k", "PSW_G", "PSW_MILLER", role="Q1 Miller cap series")
c(B_PWR, "C2", "100nF", "PSW_MILLER", "PVDD", role="Q1 soft-start (gate-drain)")
# review-2 POWER-3/4: 47k let C2 (gate-drain) turn Q1 on at every VBUS edge
# with firmware off, and the firmware turn-on sat at the SOA edge. 220k + C19
# (gate-source) hold the gate at VBUS through a 30 V/ms step (Vsg 0.77 V vs Vth
# 1.7 V) and slow the turn-on to 0.41 A inrush, 5.8 W peak, 28 ms ramp
# (docs/review-2/sims/power/tb_lite_*_FIX.cir, 2.2 uF). C19 only ever sees Vsg,
# at most 21 V x 100k/320k = 6.6 V, so a 4.7 uF/16 V 0603 X5R (Basic) does:
# ~2.5-3 uF left at 6 V bias, 2.4x voltage margin, and it fits beside R5.
r(B_PWR, "R6", "220k", "PSW_G", "PSW_PULL", role="Q1 gate divider: Vgs -6.3 V at 20 V")
c(B_PWR, "C19", "4.7uF", "VBUS", "PSW_G", role="Q1 gate-source hold-off (Vsg <= 6.6 V)")
# Q4's emitter goes to CH224Q PG (open drain, low = contract good), not GND:
# PVDD switches on only when firmware asks AND the PD contract is good.
ic(B_PWR, "Q4", "C20526", "MMBT3904", {"3": "PSW_PULL", "1": "PSW_B", "2": "PD_PG"},
   role="PVDD switch driver (GPIO18 AND PG)")
r(B_PWR, "R7", "10k", "PVDD_EN", "PSW_B", role="Q4 base")
r(B_PWR, "R8", "100k", "PVDD_EN", "GND", role="PVDD off until firmware enables it")
r(B_PWR, "R16", "22k", "PVDD", "GND", role="PVDD bleeder (Q1 body diode back-feeds VBUS)")

# ---- B3 5 V buck TPS54302 (TI design example 7.2.1, table 7-2) --------------
ic(B_PWR, "U2", "C311983", "TPS54302",
   {"1": "GND", "2": "BUCK5_SW", "3": "VBUS", "4": "BUCK5_FB", "5": "NC",
    "6": "BUCK5_BST"},
   role="VBUS -> 5 V buck")   # EN floats = enabled (datasheet pin table)
c(B_PWR, "C3", "10uF/50V", "VBUS", ic_ref="U2", pin=3, role="reg_input")
c(B_PWR, "C4", "4.7uF/50V", "VBUS", ic_ref="U2", pin=3, role="reg_input")
c(B_PWR, "C5", "100nF", "VBUS", ic_ref="U2", pin=3, role="reg_input")
c(B_PWR, "C6", "100nF", "BUCK5_BST", "BUCK5_SW", role="bootstrap")
ic(B_PWR, "L5", "C79272", "10uH", {"1": "BUCK5_SW", "2": "+5V"}, role="buck inductor")
c(B_PWR, "C7", "22uF/25V", "+5V", role="5 V output")
c(B_PWR, "C8", "22uF/25V", "+5V", role="5 V output")
r(B_PWR, "R9", "100k", "+5V", "BUCK5_FB", role="FB top: 5.18 V")
r(B_PWR, "R10", "13k", "BUCK5_FB", "GND", role="FB bottom")
c(B_PWR, "C9", "47pF", "+5V", "BUCK5_FB", role="feed-forward (review-2 POWER-10: 100 pF pushed fo past 40 kHz)")
c(B_PWR, "C25", "22uF/25V", "+5V", role="5 V output (review-2 POWER-10: 2 x 22 uF derate to ~22 uF)")

# ---- B4 3.3 V buck SY8089 ---------------------------------------------------
ic(B_PWR, "U3", "C479074", "SY8089A1AAC",
   {"1": "+5V", "2": "GND", "3": "BUCK3_LX", "4": "+5V", "5": "BUCK3_FB"},
   role="5 V -> 3.3 V buck")
c(B_PWR, "C10", "10uF", "+5V", ic_ref="U3", pin=4, role="reg_input")
c(B_PWR, "C11", "100nF", "+5V", ic_ref="U3", pin=4, role="reg_input")
ic(B_PWR, "L6", "C305171", "2.2uH", {"1": "BUCK3_LX", "2": "+3V3"}, role="buck inductor")
c(B_PWR, "C12", "22uF/25V", "+3V3", role="3.3 V output")
c(B_PWR, "C13", "10uF", "+3V3", role="3.3 V output")
r(B_PWR, "R11", "150k", "+3V3", "BUCK3_FB", role="FB top: 3.33 V")
r(B_PWR, "R12", "33k", "BUCK3_FB", "GND", role="FB bottom")
c(B_PWR, "C14", "22pF", "+3V3", "BUCK3_FB", role="feed-forward")

# ---- B5 audio LDO LP5907 ----------------------------------------------------
ic(B_PWR, "U4", "C80670", "LP5907-3.3",
   {"1": "+5V", "2": "GND", "3": "+5V", "4": "NC", "5": "+3V3A"},
   role="analog 3.3 V for the codec")
c(B_PWR, "C15", "1uF", "+5V", ic_ref="U4", pin=1)
c(B_PWR, "C16", "1uF", "+3V3A", role="LDO output")

# VBUS sense for the firmware
r(B_PWR, "R13", "100k", "VBUS", "VBUS_SENSE", role="VBUS divider top")
r(B_PWR, "R14", "10k", "VBUS_SENSE", "GND", role="VBUS divider bottom (21 V -> 1.9 V)")
c(B_PWR, "C17", "100nF", "VBUS_SENSE", role="ADC filter")
# PVDD sense (firmware ask): same divider on the switched side of Q1
r(B_PWR, "R17", "100k", "PVDD", "PVDD_SENSE", role="PVDD divider top")
r(B_PWR, "R18", "10k", "PVDD_SENSE", "GND", role="PVDD divider bottom (21 V -> 1.9 V)")
c(B_PWR, "C18", "100nF", "PVDD_SENSE", role="ADC filter")

# ---- B6 ESP32-WROVER-E ------------------------------------------------------
ESP = {
    "1": "GND", "15": "GND", "38": "GND", "39": "GND", "2": "+3V3", "3": "ESP_EN",
    "4": "BTN_VOLDN",     # GPIO36 (SENSOR_VP), input only
    "5": "PVDD_SENSE",    # GPIO39 (input only, ADC1): PVDD/11, firmware checks the switch closed
    "6": "VBUS_SENSE",    # GPIO34 ADC1
    "7": "I2S_DIN",       # GPIO35 <- codec ASDOUT
    "8": "I2C_SCL",       # GPIO32
    "9": "I2C_SDA",       # GPIO33
    "10": "I2S_LRCK",     # GPIO25
    "11": "I2S_DOUT",     # GPIO26 -> codec DSDIN
    "12": "I2S_BCLK",     # GPIO27
    "13": "IR_IN",        # GPIO14
    "14": "NC",           # GPIO12 (MTDI strap: internal pull-down = 3.3 V flash)
    "16": "BTN_VOLUP",    # GPIO13
    "17": "NC", "18": "NC", "19": "NC", "20": "NC", "21": "NC", "22": "NC",  # flash
    "23": "NC",           # GPIO15
    "24": "NC",           # GPIO2
    "25": "I2S_MCLK",     # GPIO0 (boot strap, MCLK out)
    "26": "PD_PG",        # GPIO4
    "27": "NC", "28": "NC",  # GPIO16/17: PSRAM
    "29": "NC",           # GPIO5
    "30": "PVDD_EN",      # GPIO18
    "31": "BTN_PLAY",     # GPIO19
    "32": "NC",
    "33": "AMP_SDZ_IO",   # GPIO21 -> 100k -> SDZ
    "34": "U0RXD",        # GPIO3
    "35": "U0TXD",        # GPIO1
    "36": "LED_DRV",      # GPIO22
    "37": "AMP_FAULTZ",   # GPIO23
}
ic(B_MCU, "U5", "C2973667", "ESP32-WROVER-E-N8R8", ESP,
   expect={"3": "EN", "25": "IO0", "8": "IO32", "12": "IO27", "36": "IO22"},
   role="Wi-Fi/BT module")
c(B_MCU, "C20", "10uF", "+3V3", ic_ref="U5", pin=2)
c(B_MCU, "C21", "100nF", "+3V3", ic_ref="U5", pin=2)
r(B_MCU, "R20", "10k", "+3V3", "ESP_EN", role="EN pull-up")
c(B_MCU, "C22", "1uF", "ESP_EN", role="EN delay")
r(B_MCU, "R21", "10k", "+3V3", "I2S_MCLK", role="GPIO0 pull-up (boot)")
r(B_MCU, "R22", "2.2k", "+3V3", "I2C_SCL", role="I2C pull-up")
r(B_MCU, "R23", "2.2k", "+3V3", "I2C_SDA", role="I2C pull-up")

# ---- B7 CH340C + auto-program -----------------------------------------------
# CH340B (C81010): the CH340C is not buyable at JLC (EOL, 2026-10-29); the B
# differs only on pins 7 (RST#, internal pull-up), 8 (NC), 15 (TNOW), all open.
# Its EasyEDA land (SOIC-16, 1.97 mm pads) crowds the routed tracks; the CH340C's
# SOP-16 land is the same JEDEC MS-012 package, so U6 keeps it.
ic(B_MCU, "U6", "C81010", "CH340B",
   {"1": "GND", "2": "U0RXD", "3": "U0TXD", "4": "+3V3", "5": "USB_DP",
    "6": "USB_DM", "7": "NC", "8": "NC", "9": "NC", "10": "NC", "11": "NC",
    "12": "NC", "13": "DTR", "14": "RTS", "15": "NC", "16": "+3V3"},
   expect={"13": "~{DTR}", "14": "~{RTS}", "16": "VCC"}, role="USB-UART console + flashing",
   fp="aiee:SOP-16_L10.0-W3.9-P1.27-LS6.0-BL")
c(B_MCU, "C23", "100nF", "+3V3", ic_ref="U6", pin=16)
c(B_MCU, "C24", "100nF", "+3V3", ic_ref="U6", pin=4)
ic(B_MCU, "Q2", "C20526", "MMBT3904", {"1": "Q2_B", "2": "RTS", "3": "ESP_EN"},
   role="auto-program: EN")
ic(B_MCU, "Q3", "C20526", "MMBT3904", {"1": "Q3_B", "2": "DTR", "3": "I2S_MCLK"},
   role="auto-program: GPIO0")
r(B_MCU, "R24", "10k", "DTR", "Q2_B", role="Q2 base")
r(B_MCU, "R25", "10k", "RTS", "Q3_B", role="Q3 base")

# ---- B8 ES8388 codec (datasheet rev 5.0 pin table; LyraT v4 decoupling) -----
ic(B_AUD, "U7", "C365736", "ES8388",
   {"1": "I2S_MCLK", "2": "+3V3", "3": "+3V3", "4": "GND", "5": "I2S_BCLK",
    "6": "I2S_DOUT", "7": "I2S_LRCK", "8": "I2S_DIN", "9": "NC",
    "10": "CODEC_VREF", "11": "AMP_IN_R", "12": "AMP_IN_L", "13": "GND",
    "14": "SUB_R_RAW", "15": "SUB_L_RAW", "16": "+3V3A", "17": "+3V3A",
    "18": "GND", "19": "CODEC_DACVREF", "20": "CODEC_VMID", "21": "TAP_R_C",
    "22": "TAP_L_C", "23": "LINEIN_R_C", "24": "LINEIN_L_C", "25": "NC",
    "26": "GND", "27": "I2C_SDA", "28": "I2C_SCL", "29": "GND"},
   expect={"1": "MCLK", "12": "LOUT1", "20": "VMID", "28": "CCLK", "21": "RIN2", "22": "LIN2"},
   role="codec: DAC, ADC, analog bypass")
c(B_AUD, "C30", "100nF", "+3V3", ic_ref="U7", pin=2)
c(B_AUD, "C31", "100nF", "+3V3", ic_ref="U7", pin=3)
c(B_AUD, "C32", "4.7uF", "+3V3A", ic_ref="U7", pin=17)  # LP5907 Cout <= 10 uF in total
c(B_AUD, "C33", "100nF", "+3V3A", ic_ref="U7", pin=17)
c(B_AUD, "C34", "100nF", "+3V3A", ic_ref="U7", pin=16)
c(B_AUD, "C35", "10uF", "CODEC_VREF", role="VREF decoupling")
c(B_AUD, "C36", "100nF", "CODEC_VREF", role="VREF decoupling")
c(B_AUD, "C37", "10uF", "CODEC_DACVREF", role="DACVREF decoupling")
c(B_AUD, "C38", "100nF", "CODEC_DACVREF", role="DACVREF decoupling")
c(B_AUD, "C39", "10uF", "CODEC_VMID", role="VMID decoupling")
c(B_AUD, "C40", "100nF", "CODEC_VMID", role="VMID decoupling")

# line in (FLX4): 15k/10k divider (-8 dB, 25k load), AC-coupled into LIN1/RIN1.
# review-2 AUDIO-7: 10k/10k left 1.5-2 dB headroom at the FLX4's 2.1 Vrms.
for side, n in (("L", 0), ("R", 1)):
    r(B_AUD, f"R{30 + 3 * n}", "15k", f"LINEIN_{side}", f"LINEIN_{side}_D", role="line-in attenuator")
    r(B_AUD, f"R{31 + 3 * n}", "10k", f"LINEIN_{side}_D", "GND", role="line-in attenuator")
    c(B_AUD, f"C{41 + 3 * n}", "1uF", f"LINEIN_{side}_D", f"LINEIN_{side}_C", role="line-in coupling")
    c(B_AUD, f"C{42 + 3 * n}", "100pF", f"LINEIN_{side}", role="line-in RF filter")
# speaker loopback taps (docs/remote.md 'The board measures its own output'):
# SPK_x_P / 11 (100k/10k), 470 pF carrier filter, AC-coupled into LIN2/RIN2.
# At full power the ADC sees +-0.55 Vpk (-7.6 dBFS), carrier ~1.7 mVpk
# (docs/review-2/audio.md). Ratio with the ADC input: 0.0625.
for side, n in (("L", 0), ("R", 1)):
    r(B_AUD, f"R{66 + 2 * n}", "100k", f"SPK_{side}_P", f"TAP_{side}", role="speaker tap divider top")
    r(B_AUD, f"R{67 + 2 * n}", "10k", f"TAP_{side}", "GND", role="speaker tap divider bottom")
    c(B_AUD, f"C{81 + 2 * n}", "470pF", f"TAP_{side}", role="speaker tap carrier filter")
    c(B_AUD, f"C{82 + 2 * n}", "1uF", f"TAP_{side}", f"TAP_{side}_C", role="speaker tap coupling")
# sub outs: 10 uF coupling, 100R build-out, 47k bleeder
for side, n in (("L", 0), ("R", 1)):
    c(B_AUD, f"C{47 + n}", "10uF", f"SUB_{side}_RAW", f"SUB_{side}_AC", role="sub out coupling")
    r(B_AUD, f"R{36 + 2 * n}", "100R", f"SUB_{side}_AC", f"SUB_{side}", role="sub out build-out")
    r(B_AUD, f"R{37 + 2 * n}", "47k", f"SUB_{side}", "GND", role="sub out bleeder")

# ---- B9 TPA3118D2 (SLOS708G pin table, table 1, figure 34 values) ----------
# The chip's "right" channel (pins 4/5 in, 26-30 out) drives the LEFT speaker
# and its "left" channel the RIGHT one: the output row then runs L+ L- R+ R-
# left to right, straight into the speaker terminal, with no crossing.
# Nets are named by speaker channel.
ic(B_AMP, "U8", "C46497", "TPA3118D2",
   {"1": "GND", "2": "AMP_SDZ", "3": "AMP_FAULTZ", "4": "AMP_L_INP",
    "5": "AMP_L_INN", "6": "AMP_GVDD", "7": "AMP_GVDD", "8": "AMP_GAIN",
    "9": "GND", "10": "AMP_R_INP", "11": "AMP_R_INN", "12": "GND",
    "13": "GND", "14": "GND", "15": "GND", "16": "NC", "17": "PVDD",
    "18": "PVDD", "19": "PVDD", "20": "BS_R_N", "21": "OUT_R_N", "22": "GND",
    "23": "OUT_R_P", "24": "BS_R_P", "25": "GND", "26": "BS_L_N", "27": "OUT_L_N",
    "28": "GND", "29": "OUT_L_P", "30": "BS_L_P", "31": "PVDD", "32": "PVDD",
    "33": "GND"},
   expect={"2": "SDZ", "4": "RINP", "7": "GVDD", "17": "AVCC", "29": "OUTPR"},
   role="2 x BTL class-D, 26 dB")
c(B_AMP, "C50", "1uF", "AMP_GVDD", ic_ref="U8", pin=7)
c(B_AMP, "C51", "1uF/0805", "PVDD", ic_ref="U8", pin=17)
for i, pin in enumerate((18, 31)):
    c(B_AMP, f"C{52 + 3 * i}", "1uF/0805", "PVDD", ic_ref="U8", pin=pin)
    c(B_AMP, f"C{53 + 3 * i}", "100nF", "PVDD", ic_ref="U8", pin=pin)
    c(B_AMP, f"C{54 + 3 * i}", "1nF", "PVDD", ic_ref="U8", pin=pin)
ic(B_AMP, "C58", "C46550472", "470uF 35V polymer", {"1": "PVDD", "2": "GND"},
   role="PVDD bulk, left side: polymer, 3.8 A ripple")
ic(B_AMP, "C59", "C2836441", "220uF 35V", {"1": "PVDD", "2": "GND"}, role="PVDD bulk, right side")
r(B_AMP, "R50", "20k", "AMP_GAIN", "GND", role="gain 26 dB master (table 1)")
r(B_AMP, "R51", "100k", "AMP_GVDD", "AMP_GAIN", role="gain 26 dB master (table 1)")
r(B_AMP, "R52", "100k", "AMP_SDZ_IO", "GND", role="amp off until firmware enables it")
# review-2 AUDIO-4/DIGITAL-14: through 100k the SDZ input current (up to 50 uA)
# kept SDZ under VIH 2 V. 10k gives >= 2.7 V; C26 keeps the edge under 10 V/ms.
r(B_AMP, "R54", "10k", "AMP_SDZ_IO", "AMP_SDZ", role="SDZ series: TI 10 V/ms and PVCC+0.3 V limits")
c(B_AMP, "C26", "47nF", "AMP_SDZ", role="SDZ slew limit (tau 0.47 ms)")
r(B_AMP, "R53", "10k", "+3V3", "AMP_FAULTZ", role="FAULTZ pull-up")
# inputs: single-ended drive, INN AC-grounded
for side, n in (("L", 0), ("R", 1)):
    c(B_AMP, f"C{60 + 2 * n}", "1uF", f"AMP_IN_{side}", f"AMP_{side}_INP", role="amp input coupling")
    c(B_AMP, f"C{61 + 2 * n}", "1uF", f"AMP_{side}_INN", "GND", role="amp input AC ground")
# bootstraps
for bs, out, ref in (("BS_L_P", "OUT_L_P", "C64"), ("BS_L_N", "OUT_L_N", "C65"),
                     ("BS_R_P", "OUT_R_P", "C66"), ("BS_R_N", "OUT_R_N", "C67")):
    c(B_AMP, ref, "220nF", bs, out, role="bootstrap")
# output LC filters: 10 uH + 1 uF per output
for i, (out, spk) in enumerate((("OUT_L_P", "SPK_L_P"), ("OUT_L_N", "SPK_L_N"),
                                ("OUT_R_P", "SPK_R_P"), ("OUT_R_N", "SPK_R_N"))):
    ic(B_AMP, f"L{1 + i}", "C48391614", "10uH 8A", {"1": out, "2": spk}, role="output filter")
    c(B_AMP, f"C{68 + i}", "1uF/1206", spk, role="output filter")

# ---- B10/B11 connectors and UI ----------------------------------------------
# stacked dual RCA: pad 2 = sleeve, pad 1 = top jack (white, L), pad 3 = bottom
# jack (red, R); see parts/datasheets.md
ic(B_IO, "J2", "C7527522", "RCA line in", {"1": "LINEIN_L", "2": "GND", "3": "LINEIN_R"}, role="line in L/R (FLX4)")
ic(B_IO, "J3", "C7527522", "RCA sub out", {"1": "SUB_L", "2": "GND", "3": "SUB_R"}, role="sub out L/R")
# speakers: one 4-way 5.08 mm screw terminal (16 A); the enclosure's 4 mm
# binding posts wire to it (GC4010-F has no EasyEDA footprint and is a bullet
# connector, not a banana socket - see README "Decisions")
ic(B_IO, "J4", "C2915641", "DB128V-5.08-4P",
   {"1": "SPK_L_P", "2": "SPK_L_N", "3": "SPK_R_P", "4": "SPK_R_N"},
   role="speaker terminal L+ L- R+ R-")
for i, net in enumerate(("BTN_VOLDN", "BTN_VOLUP", "BTN_PLAY")):
    # pads 1/2 and 3/4 are the switch's two internally joined pairs
    ic(B_IO, f"SW{1 + i}", "C318884", "TS-1187A",
       {"1": net, "2": net, "3": "GND", "4": "GND"}, role="button")
    r(B_IO, f"R{60 + i}", "10k", "+3V3", net, role="button pull-up")
# IRM-3638T: through-hole, standing at the front edge with its lens facing
# forward through the enclosure's front wall (the top-looking IRM-H638T sat
# 31 mm under the lid, outside a remote's cone). Everlight's RC supply filter.
ic(B_IO, "U9", "C42421366", "IRM-3638T", {"1": "IR_IN", "2": "GND", "3": "IR_VCC"},
   expect={"1": "OUT", "3": "VCC"}, role="IR receiver 38 kHz, front-facing")
r(B_IO, "R65", "100R", "+3V3", "IR_VCC", role="IR supply filter")
c(B_IO, "C80", "4.7uF", "IR_VCC", ic_ref="U9", pin=3)
r(B_IO, "R63", "10k", "+3V3", "IR_IN", role="IR pull-up")
r(B_IO, "R64", "1k", "LED_DRV", "LED_A", role="LED current 1.3 mA")
ic(B_IO, "D2", "C2286", "red", {"1": "LED_A", "2": "GND"}, expect={"1": "A", "2": "K"},
   role="status LED")

if __name__ == "__main__":
    out = sheet.save(WS / "kicad")
    # decoupling.json carries FINAL netlist names: rails are global power
    # nets (bare), everything else on the root sheet is "/NAME".
    POWER = {"VBUS", "PVDD", "+5V", "+3V3", "+3V3A", "GND"}
    for d in DECOUP:
        for k in ("rail", "gnd"):
            if k in d and d[k] not in POWER:
                d[k] = "/" + d[k]
    (WS / "kicad" / "decoupling.json").write_text(
        json.dumps({"associations": DECOUP}, indent=1) + "\n")
    (WS / "parts" / "bom-lines.json").write_text(json.dumps(BOM, indent=1) + "\n")
    print(json.dumps({"sch": str(out), "parts": len(BOM),
                      "decoupling": len(DECOUP),
                      "place_report": sheet.place_report}))
