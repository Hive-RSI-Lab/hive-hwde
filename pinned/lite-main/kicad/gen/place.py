"""lite-main placement (hwde P6), as an absolute place_edit op list.

Prints {"version": 1, "ops": [...]} for place_edit.py. Positions are given in
board coordinates (u right, v down, mm) from the outline's top-left corner,
which board_init reports in reports/board-init.json. The rear edge is v = 0
(RCA in, RCA sub out, USB-C, speaker terminal), the front edge v = 90
(buttons, LED, IR). See README "Layout".

    python3 kicad/gen/place.py > kicad/place-ops.json
"""
import json
from pathlib import Path

WS = Path(__file__).resolve().parents[2]
X0, Y0 = json.loads((WS / "reports" / "board-init.json").read_text())["outline_origin"]
ops = []
placed = set()


def P(ref, u, v, deg=0):
    assert ref not in placed, ref
    placed.add(ref)
    ops.append({"op": "place", "ref": ref, "x": round(X0 + u, 3),
                "y": round(Y0 + v, 3), "deg": deg, "side": "front"})


def row(refs, u0, v, du, deg=0):
    for i, r in enumerate(refs):
        P(r, u0 + i * du, v, deg)


def col(refs, u, v0, dv, deg=0):
    for i, r in enumerate(refs):
        P(r, u, v0 + i * dv, deg)


# ---------------------------------------------------------------- rear edge
# RCA jacks: footprint front face at local y = +15.18, so rot 180 and the
# origin 15.18 mm in from the edge puts the face on the edge.
P("J2", 19.6, 15.2, 180)          # line in (FLX4)
P("J3", 41.8, 15.2, 180)          # sub out
P("J1", 58.5, 5.07, 180)           # USB-C, receptacle front on the edge
P("J4", 82.8, 5.6, 0)              # speaker terminal L+ L- R+ R-

# ---------------------------------------------------------------- USB-C / PD
P("U1", 55.75, 13.6, 0)            # CH224A: pin 7 (CC2) under J1's B5, pin 8 (VBUS) under its VBUS via
P("R1", 51.8, 10.6, 90)            # Rset 120k, by CFG1 (pin 9)
P("R4", 51.8, 13.8, 90)            # PG pull-up, by PG (pin 10)
P("C1", 51.8, 17.0, 90)            # VHV (pin 1)
P("R2", 54.2, 19.4, 0)             # I2C 0R links, below SCL/SDA (pins 2/3)
P("R3", 57.5, 19.4, 0)
P("R15", 60.5, 14.8, 0)            # Miller series
P("D1", 62.0, 11.9, 180)          # TVS: cathode (pad 1) in the VBUS pour at Q1, anode to GND

# PVDD switch between USB-C and the amp; its drain feeds a PVDD strip on F.Cu
# (u 69.4-72.9) down between the filter caps to the B.Cu PVDD pour
P("Q1", 67.7, 6.0, 270)            # source (VBUS) toward J1, drain (PVDD) right
P("Q4", 64.0, 15.6, 0)
P("C2", 68.9, 12.5, 90)           # Miller cap, PVDD pad in the drain strip
P("R5", 67.4, 11.0, 90)
P("R6", 67.4, 14.6, 90)
P("R7", 60.5, 16.7, 0)
P("R8", 21.0, 55.5, 90)            # PVDD_EN pull-down, at the ESP32's GPIO18

# ---------------------------------------------------------------- 5 V buck
# rot 90: VIN/SW/GND on the right column, FB/EN/BOOT on the left; SW runs
# straight right into L5's pad 1 on F.Cu (no via on the switch node)
P("U2", 53.0, 26.5, 90)            # TPS54302
P("C3", 55.5, 22.0, 0)             # VIN 10 uF 50 V (1206)
P("C4", 50.3, 22.6, 0)             # VIN 4.7 uF 50 V (1206)
P("C5", 56.4, 24.3, 0)             # VIN 100 nF, at the VIN pin
P("C6", 53.0, 29.4, 0)             # bootstrap
P("L5", 59.2, 29.2, 270)           # 10 uH, pad 1 (SW) up toward U2
col(["C7", "C8"], 58.5, 34.5, 2.1, 0)    # 22 uF out
P("R9", 48.8, 25.5, 90)            # FB divider left of U2's FB pin
P("R10", 48.8, 28.7, 90)
P("C9", 47.0, 25.5, 90)

# ---------------------------------------------------------------- 3.3 V buck
P("U3", 52.0, 42.0, 0)             # SY8089
P("C10", 52.0, 37.6, 0)            # input caps across U3 (IN pin 4 left, GND pin 2 right)
P("C11", 52.0, 39.4, 0)
P("L6", 56.5, 42.0, 90)            # 2.2 uH
P("C12", 56.5, 47.0, 0)
P("C13", 52.5, 47.0, 0)
P("R11", 52.0, 45.0, 0)
col(["R12", "C14"], 48.5, 48.5, 1.8, 0)

# ---------------------------------------------------------------- amplifier
row(["C68", "C69", "C70", "C71"], 67.0, 18.5, 8.5, 0)   # filter caps at the terminal
row(["L1", "L2", "L3", "L4"], 67.0, 27.0, 8.5, 90)      # 10 uH, pad 1 (amp) down
P("U8", 79.0, 45.0, 0)             # output row (17-32) faces the inductors
P("C64", 73.0, 37.0, 90)           # bootstraps: BS_L_P - OUT_L_P (pins 30/29)
P("C65", 77.6, 37.0, 90)           # BS_L_N - OUT_L_N (26/27)
P("C66", 80.4, 37.0, 90)           # BS_R_P - OUT_R_P (24/23)
P("C67", 85.0, 37.0, 90)           # BS_R_N - OUT_R_N (20/21)
for ref, v in (("C54", 40.2), ("C53", 42.0), ("C52", 44.0), ("C51", 46.2)):
    P(ref, 87.5, v, 0)             # pins 18/19 (1 nF nearest), AVCC 17
for ref, v in (("C57", 40.2), ("C56", 42.0), ("C55", 44.0)):
    P(ref, 71.3, v, 0)             # pins 31/32
P("C58", 64.2, 48.0, 270)          # 470 uF polymer, left, + pad up
P("R16", 60.0, 60.4, 0)            # PVDD bleeder, beside the PVDD divider
P("C59", 94.5, 48.0, 270)          # 220 uF, right, + pad up
P("C50", 78.2, 53.0, 90)           # GVDD
P("R50", 80.2, 53.0, 90)           # gain to GND
P("R51", 82.2, 53.0, 90)           # gain to GVDD
P("R52", 72.5, 52.5, 90)           # SDZ pull-down (GPIO side)
P("R54", 72.5, 56.5, 90)           # SDZ series 100k
P("R53", 70.5, 56.5, 90)           # FAULTZ pull-up
col(["C60", "C61"], 75.5, 52.5, 1.9, 0)   # left-speaker inputs (pins 4/5)
col(["C62", "C63"], 85.5, 53.0, 1.9, 0)   # right-speaker inputs (pins 10/11)

# ---------------------------------------------------------------- codec
P("U7", 30.0, 35.0, 0)             # ES8388
col(["C30", "C31"], 27.0, 40.0, 1.8, 0)         # DVDD/PVDD (pins 2/3)
col(["C32", "C33", "C34"], 34.0, 29.5, 1.8, 0)  # AVDD/HPVDD (16/17)
col(["C35", "C36"], 36.5, 35.0, 1.8, 0)         # VREF (10)
col(["C37", "C38"], 30.0, 29.5, 1.8, 0)         # DACVREF (19)
col(["C39", "C40"], 26.5, 29.5, 1.8, 0)         # VMID (20)
P("U4", 37.5, 41.0, 0)             # LP5907
P("C15", 41.5, 40.0, 0)
P("C16", 37.5, 44.0, 0)
# line-in network, between J2's pins and LIN1/RIN1
col(["R30", "R31", "C41", "C42"], 15.5, 22.5, 1.8, 0)   # left
col(["R33", "R34", "C44", "C45"], 21.5, 22.5, 1.8, 0)   # right
# sub-out network, between LOUT2/ROUT2 and J3's pins
col(["C47", "R36", "R37"], 38.5, 22.0, 1.8, 0)   # left
col(["C48", "R38", "R39"], 44.0, 22.0, 1.8, 0)   # right

# ---------------------------------------------------------------- MCU
# WROVER-E rot 90: antenna to the left edge (antenna end at u = 0.6)
P("U5", 19.5, 68.0, 90)
P("C20", 10.5, 80.5, 0)            # near pin 2 (3V3)
P("C21", 13.5, 80.5, 0)
P("R20", 16.5, 80.5, 0)            # EN pull-up
P("C22", 19.5, 80.5, 0)            # EN cap
P("R21", 24.5, 55.5, 90)           # GPIO0 pull-up
col(["R22", "R23"], 36.0, 51.0, 1.8, 0)   # I2C pull-ups
col(["R13", "R14", "C17"], 41.0, 47.5, 1.8, 0)   # VBUS divider
row(["R17", "R18", "C18"], 60.0, 58.6, 3.2, 0)   # PVDD divider, below C58
# CH340C + auto-program
P("U6", 46.0, 62.0, 90)
P("C23", 46.5, 68.8, 0)            # VCC pin 16
P("C24", 52.5, 62.5, 0)            # V3 pin 4
P("Q2", 40.0, 70.0, 0)
P("Q3", 40.0, 74.0, 0)
P("R24", 43.5, 70.0, 90)
P("R25", 43.5, 74.0, 90)

# ---------------------------------------------------------------- front edge
row(["SW1", "SW2", "SW3"], 58.0, 86.5, 11.0, 0)
row(["R60", "R61", "R62"], 58.0, 81.5, 11.0, 0)
P("U9", 89.0, 87.0, 0)             # IR receiver, lens (local +y) at the front edge
P("C80", 89.0, 82.0, 0)
P("R65", 92.0, 80.0, 0)
P("R63", 85.5, 81.5, 90)
P("D2", 48.0, 88.0, 0)             # status LED
P("R64", 48.0, 85.5, 0)

print(json.dumps({"version": 1, "ops": ops}, indent=1))
