"""core: every block's parts and nets (architecture/blocks.md)."""
from __future__ import annotations

from design import C, FB, P, R, sheet

A = "aiee:"

# ------------------------------------------------------------ B1-B3 power in
sheet("power_in")
# GCT USB4085-GF-A (C7095263): through-hole, 48 V / 5 A / 240 W. The SMD
# USB4105 had 0 free at JLC (docs/bom-stock.md, 2026-10-29); through-hole
# pins also tie VBUS_IN into the pours on every layer (review-2 POWER-1).
P("J1", A + "USB4085-GF-A_C7095263", "USB4085-GF-A", A + "USC-C-SMD_USB4085-GF-A",
  {"A4": "VBUS_IN", "A9": "VBUS_IN", "B4": "VBUS_IN", "B9": "VBUS_IN",
   "A1": "GND", "A12": "GND", "B1": "GND", "B12": "GND", "A5": "CC1",
   "B5": "CC2", "A6": "USB_DP", "B6": "USB_DP", "A7": "USB_DM", "B7": "USB_DM",
   "A8": "NC", "B8": "NC", "S1": "GND"},
  lcsc="C7095263", mpn="USB4085-GF-A", role="USB-C receptacle, 48 V/5 A, 240 W (EPR 28 V)")
P("D1", A + "SMBJ30A_C113998", "SMBJ30A", A + "SMB_L4.6-W3.6-LS5.3-RD",
  {"1": "VBUS_IN", "2": "GND"}, lcsc="C113998", role="VBUS TVS")
P("U2", A + "USBLC6-2SC6_C2687116", "USBLC6-2SC6", A + "SOT-23-6_L2.9-W1.6-P0.95-LS2.8-BL",
  {"1": "USB_DP", "6": "USB_DP", "3": "USB_DM", "4": "USB_DM", "2": "GND",
   "5": "+5V"}, lcsc="C2687116", role="USB D+/D- ESD")
# PG (pin 10, open drain, low = contract good) gates the PVDD switch like the
# Lite's: review-2 POWER-6, PVDD drops in hardware when the contract is lost.
P("U1", A + "CH224Q_C46061833", "CH224Q", A + "DFN-10_L2.0-W2.0-P0.40-BL-EP",
  {"1": "VBUS_PD", "8": "VBUS_PD", "2": "I2C_SCL", "3": "I2C_SDA", "4": "NC",
   "5": "NC", "6": "CC1", "7": "CC2", "9": "PD_CFG1", "10": "PD_PG", "11": "GND"},  # CC1/CC2 swapped at the chip (the CH224 treats them alike) to untangle the fan-out
  lcsc="C46061833", role="USB PD 3.2 EPR sink, asks for 28 V")
R("R2", "210k", "PD_CFG1", "GND", role="CH224Q CFG1: 28 V request")
R("R20", "10k", "+3V3", "PD_PG", role="CH224Q PG pull-up")
R("R17", "10R", "VBUS", "VBUS_PD", role="CH224Q supply/sense feed (mA)")
C("C1", "1uF/50V", "VBUS_PD", pkg="0805", ic="U1", pin="1")
R("R1", "10m", "VBUS_IN", "VBUS", pkg="2512", role="input current shunt")
P("U3", A + "INA186A1IDCKR", "INA186A1", A + "SC-70-6_L2.2-W1.3-P0.65-LS2.1-BR",
  {"n:IN+": "ISNS_P", "n:IN-": "ISNS_N", "n:VS": "+3V3", "n:GND": "GND",
   "n:REF": "GND", "n:OUT": "ISENSE_RAW"}, lcsc="C2058311",
  role="VBUS current sense, 25 V/V, 40 V common mode")
C("C2", "100nF", "+3V3", ic="U3", pin="3")
R("R18", "10R", "VBUS_IN", "ISNS_P", role="Kelvin tap + input filter at R1")
R("R19", "10R", "VBUS", "ISNS_N", role="Kelvin tap + input filter at R1")
C("C9", "100nF/50V", "ISNS_P", "ISNS_N", role="differential filter, 80 kHz")
R("R3", "1k", "ISENSE_RAW", "ISENSE")
C("C3", "10nF", "ISENSE")
# PVDD high-side switch with soft start and overvoltage cut-off
P("Q1", A + "AO4485", "AO4485", A + "SOIC-8_L5.0-W4.0-P1.27-LS6.0-BL",
  {"n:S": "VBUS", "n:D": "PVDD", "n:G": "PVDD_G"}, lcsc="C51499",
  role="PVDD P-FET switch, 40 V")
# review-2 POWER-2: 100k/100k + 1 uF charged 940 uF in ~12 ms, 0.36 J in Q1
# against ~0.2 J of SOA. 470k/470k + 2.2 uF: 0.47 A, 9.8 W peak, ~60 ms ramp
# (docs/review-2/sims/power/tb_core_fw_on_28v_FIX.cir), 2.5x SOA margin.
R("R4", "470k", "VBUS", "PVDD_G")
R("R5", "470k", "PVDD_G", "PVDD_PD")
C("C4", "100nF/50V", "PVDD_G", "PVDD", pkg="0603", role="soft-start gate-drain")
C("C8", "2.2uF/50V", "VBUS", "PVDD_G", pkg="0805",
  role="gate-source: holds Q1 off while VBUS steps (attach, 5 V to 28 V)")
P("D4", A + "BZT52C15", "BZT52C15", A + "x", {"n:K": "VBUS", "n:A": "PVDD_G"},
  lcsc="C173427", role="Q1 gate-source clamp, 15 V")
P("Q2", A + "2N7002", "2N7002", A + "SOT-23-3_L2.9-W1.3-P1.90-LS2.4-BR",
  {"n:D": "PVDD_PD", "n:S": "PD_PG", "n:G": "PVDD_ENG"}, lcsc="C8545", basic=True,
  role="PVDD enable: firmware (PVDD_EN) AND the PD contract (PG low)")
R("R6", "1k", "PVDD_EN", "PVDD_ENG")
R("R7", "100k", "PVDD_ENG", "GND")
P("D2", A + "BZT52C33_C266574", "BZT52C33", A + "x",
  {"1": "VBUS", "2": "OV_Z"}, lcsc="C266574",
  role="33 V zener: gross-overvoltage trip, ~32-36 V (TPA3221 abs max 37 V)")
R("R8", "10k", "OV_Z", "OV_B")
R("R9", "10k", "OV_B", "GND")
P("Q3", A + "S8050_C2146", "S8050", A + "SOT-23-3_L2.9-W1.3-P1.90-LS2.4-BR",
  {"n:B": "OV_B", "n:E": "GND", "n:C": "PVDD_ENG"}, lcsc="C2146", basic=True,
  role="pulls PVDD enable low above ~31 V")
P("C5", "Device:C_Polarized", "470uF/50V", A + "CAP-TH_BD10.0-P5.00-D1.0-FD",
  {"1": "PVDD", "2": "GND"}, lcsc="C88733", role="PVDD bulk, zone A")
P("C6", "Device:C_Polarized", "470uF/50V", A + "CAP-TH_BD10.0-P5.00-D1.0-FD",
  {"1": "PVDD", "2": "GND"}, lcsc="C88733", role="PVDD bulk, zone A")
C("C7", "10uF/50V", "PVDD", pkg="1206")

# ------------------------------------------------------------ B4 rails
sheet("rails")
P("U4", A + "SY8303AIC", "SY8303AIC", A + "TSOT-23-8_L2.9-W1.6-P0.65-LS2.8-BR",
  {"n:IN": "VBUS", "n:GND": "GND", "n:EN": "BUCK5_EN", "n:FB": "BUCK5_FB",
   "n:FS": "BUCK5_FS", "n:LX": "BUCK5_LX", "n:BS": "BUCK5_BS"},
  lcsc="C97054", role="VBUS to 5.1 V, 3 A, 1 MHz")
C("C10", "10uF/50V", "VBUS", pkg="1206", ic="U4", pin="5", role="reg_input")
C("C11", "100nF/50V", "VBUS", pkg="0603", ic="U4", pin="5", role="reg_input")
R("R10", "100k", "VBUS", "BUCK5_EN")
R("R11", "100k", "BUCK5_EN", "GND")
R("R12", "100k", "BUCK5_FS", "GND", role="FS: 1.05 MHz")
C("C12", "10nF", "BUCK5_BS", "BUCK5_LX")
P("L9", A + "SWPA4030S4R7MT", "4.7uH", A + "IND-SMD_L4.0-W4.0",
  {"1": "BUCK5_LX", "2": "+5V"}, lcsc="C57269")
R("R13", "15k", "+5V", "BUCK5_FB", role="5.1 V = 0.6 x (1 + 15k/2k)")
R("R14", "2k", "BUCK5_FB", "GND")
C("C13", "22uF", "+5V", pkg="0805")
C("C14", "22uF", "+5V", pkg="0805")
P("U5", A + "SY8089A1AAC", "SY8089A1AAC", A + "SOT-23-5_L3.0-W1.7-P0.95-LS2.8-BR",
  {"n:IN": "+5V", "n:GND": "GND", "n:EN": "+5V", "n:LX": "BUCK3_LX",
   "n:FB": "BUCK3_FB"}, lcsc="C479074", role="5 V to 3.3 V, 2 A")
C("C15", "10uF", "+5V", pkg="0603", ic="U5", pin="4", role="reg_input")
P("L10", A + "SWPA4030S2R2MT", "2.2uH", A + "IND-SMD_L4.0-W4.0",
  {"1": "BUCK3_LX", "2": "+3V3"}, lcsc="C36409")
R("R15", "100k", "+3V3", "BUCK3_FB", role="3.33 V = 0.6 x (1 + 100k/22k)")
R("R16", "22k", "BUCK3_FB", "GND")
C("C16", "22uF", "+3V3", pkg="0805")
C("C17", "22uF", "+3V3", pkg="0805")
P("U6", A + "LP5907MFX-3.3_NOPB", "LP5907-3.3", A + "SOT-23-5_L3.0-W1.7-P0.95-LS2.8-BR",
  {"n:IN": "+5V", "n:GND": "GND", "n:EN": "+5V", "n:N/C": "NC",
   "n:OUT": "+3V3A"}, lcsc="C80670", role="analog 3.3 V for the converters")
C("C18", "1uF", "+5V", ic="U6", pin="1")
C("C19", "1uF", "+3V3A", ic="U6", pin="5")
FB("FB1", "+5V", "+5VA", role="+5VA for op-amp and switch")
C("C21", "10uF", "+5VA", pkg="0603")
FB("FB2", "+3V3", "+3V3E", role="+3V3E for the PHY analog")
C("C22", "10uF", "+3V3E", pkg="0603")

import blocks_mcu  # noqa: E402,F401
