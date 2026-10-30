"""core: DACs (B7, B8), line in (B9), optical (B13)."""
from __future__ import annotations

from design import C, FB, P, R, sheet  # noqa: F401

A = "aiee:"

# ------------------------------------------------------------ B7 Sabre DAC
sheet("dac")
P("U10", A + "ES9018K2M", "ES9018K2M", A + "QFN-28_L4.0-W4.0-P0.40-BL-EP2.6",
  {"1": "GND", "2": "NC", "3": "I2C_SCL", "4": "I2C_SDA", "5": "GND",
   "6": "NC", "7": "DAC_MCLK", "8": "GND", "9": "DACR_P", "10": "DACR_N",
   "11": "GND", "12": "GND", "13": "DACL_P", "14": "DACL_N", "15": "+3V3A",
   "16": "+3V3A", "17": "+3V3A", "18": "+3V3", "19": "NC", "20": "GND",
   "21": "DAC_DVDD", "22": "NC", "23": "NC", "24": "OPT_SPDIF",
   "25": "I2S_DOUT_MAIN", "26": "I2S_LRCK", "27": "I2S_BCK", "28": "DAC_EN",
   "29": "GND"},
  lcsc="C2688377", role="Sabre 32-bit stereo DAC, mains, I2C 0x48")
C("C50", "1uF", "+3V3A", ic="U10", pin="15")
C("C51", "100nF", "+3V3A", ic="U10", pin="15")
C("C52", "1uF", "+3V3A", ic="U10", pin="16")
C("C53", "100nF", "+3V3A", ic="U10", pin="16")
C("C54", "100nF", "+3V3A", ic="U10", pin="17")
C("C55", "100nF", "+3V3", ic="U10", pin="18")
C("C56", "4.7uF", "DAC_DVDD", pkg="0603", ic="U10", pin="21")
R("R70", "10k", "OPT_SPDIF", "GND", role="defines GPIO1 when TOSLINK is not fitted")
P("Y1", A + "OT322550MJBA4SL", "50MHz", A + "OSC-SMD_4P-L3.2-W2.5-BL",
  {"1": "+3V3", "2": "GND", "3": "OSC_OUT", "4": "+3V3"},
  lcsc="C717684", role="DAC master clock (internal DVDD allows 50 MHz max)")
C("C57", "100nF", "+3V3", ic="Y1", pin="4")
R("R71", "33R", "OSC_OUT", "DAC_MCLK")
# OPA1678: differential to single-ended, gain 1.5k/(2k+806R) = 0.53
P("U11", A + "OPA1678IDR", "OPA1678", A + "SOIC-8_L5.0-W4.0-P1.27-LS6.0-BL",
  {"n:INA+": "OPL_P", "n:INA-": "OPL_N", "n:OUTA": "MAIN_L",
   "n:INB+": "OPR_P", "n:INB-": "OPR_N", "n:OUTB": "MAIN_R",
   "n:V+": "+5VA", "n:V-": "GND"}, lcsc="C192421", role="DAC output stage")
C("C58", "100nF", "+5VA", ic="U11", pin="8")
C("C59", "10uF", "+5VA", pkg="0603")
for ch, n in (("L", 72), ("R", 80)):
    # inverting: DAC P leg to the - input, so the TPA3221's own inversion
    # nets out to absolute polarity at the speaker terminals
    R(f"R{n}", "2k", f"DAC{ch}_N", f"OP{ch}_P")
    R(f"R{n + 1}", "1.5k", f"OP{ch}_P", "VREF")
    C(f"C{n + 70}", "1nF", f"OP{ch}_P", "VREF")
    R(f"R{n + 2}", "2k", f"DAC{ch}_P", f"OP{ch}_N")
    R(f"R{n + 3}", "1.5k", f"OP{ch}_N", f"MAIN_{ch}")
    C(f"C{n + 71}", "1nF", f"OP{ch}_N", f"MAIN_{ch}")
R("R90", "470R", "+5VA", "VREF", role="2.4 V reference, stiff against the 2x4.3k DAC legs")
R("R91", "470R", "VREF", "GND")
C("C64", "10uF", "VREF", pkg="0603")

# ------------------------------------------------------------ B8 sub DAC
P("U12", A + "PCM5102APWR", "PCM5102A", A + "TSSOP-20_L6.5-W4.4-P0.65-LS6.4-BL",
  {"n:CPVDD": "+3V3A", "n:CAPP": "SUB_CAPP", "n:CPGND": "GND",
   "n:CAPM": "SUB_CAPM", "n:VNEG": "SUB_VNEG", "n:OUTL": "SUB1_DAC",
   "n:OUTR": "SUB2_DAC", "n:AVDD": "+3V3A", "n:AGND": "GND", "n:DEMP": "GND",
   "n:FLT": "GND", "n:SCK": "GND", "n:BCK": "I2S_BCK",
   "n:DIN": "I2S_DOUT_SUB", "n:LRCK": "I2S_LRCK", "n:FMT": "GND",
   "n:XSMT": "DAC_EN", "n:LDOO": "SUB_LDOO", "n:DGND": "GND",
   "n:DVDD": "+3V3"}, lcsc="C107671", role="sub DAC, 2.1 Vrms ground-centred")
C("C65", "2.2uF", "SUB_CAPP", "SUB_CAPM", pkg="0603")
C("C66", "2.2uF", "SUB_VNEG", pkg="0603")
C("C67", "2.2uF", "SUB_LDOO", pkg="0603")
C("C68", "1uF", "+3V3A", ic="U12", pin="1")
C("C69", "1uF", "+3V3A", ic="U12", pin="8")
C("C70", "1uF", "+3V3", ic="U12", pin="20")
R("R92", "470R", "SUB1_DAC", "SUB1_OUT")
R("R93", "470R", "SUB2_DAC", "SUB2_OUT")
C("C71", "1nF", "SUB1_OUT")
C("C72", "1nF", "SUB2_OUT")
P("J4", A + "XDJK-0271-1650", "SUB OUT", A + "CONN-TH_XDJK-0271-1650",
  {"1": "SUB1_OUT", "2": "GND", "3": "SUB2_OUT"}, lcsc="C7527522", role="stacked dual RCA: SUB1 (top) / SUB2")

# ------------------------------------------------------------ B9 line in
sheet("linein")
P("J5", A + "XDJK-0271-1650", "LINE IN", A + "CONN-TH_XDJK-0271-1650",
  {"1": "LIN_L", "2": "GND", "3": "LIN_R"}, lcsc="C7527522", role="stacked dual RCA: line in L (top) / R")
for ch, n in (("L", 100), ("R", 110)):
    R(f"R{n}", "4.7k", f"LIN_{ch}", f"LDIV_{ch}", role="halve 2 Vrms to 1 Vrms")
    R(f"R{n + 1}", "4.7k", f"LDIV_{ch}", "GND")
    C(f"C{n}", "4.7uF/25V", f"LDIV_{ch}", f"LSW_{ch}", pkg="0805")
    R(f"R{n + 2}", "100k", f"LSW_{ch}", "VREF", role="bias at 2.5 V")
    C(f"C{n + 1}", "1uF", f"LDIV_{ch}", f"ADC_{ch}P")
    C(f"C{n + 2}", "1uF", f"ADC_{ch}N")
P("U13", A + "ES7243E", "ES7243E", A + "QFN-20_L3.0-W3.0-P0.40-BL-EP1.7",
  {"n:VDDP": "+3V3", "n:TDMIN": "GND", "n:SDOUT/AD2": "I2S_DIN",
   "n:GNDD": "GND", "n:VDDD": "+3V3", "n:SCLK": "I2S_BCK", "n:LRCK": "I2S_LRCK",
   "n:AD1": "GND", "n:AINLP": "ADC_LP", "n:AINLN": "ADC_LN",
   "n:REFQ": "ADC_REFQ", "n:VDDA": "+3V3A", "n:GNDA": "GND",
   "n:REFP": "ADC_REFP", "n:AINRN": "ADC_RN", "n:AINRP": "ADC_RP",
   "n:AD0": "GND", "n:CDATA": "I2C_SDA", "n:CCLK": "I2C_SCL",
   "n:MCLK": "GND", "n:EP": "GND"},
  lcsc="C2929446", role="line-in ADC, MCLK taken from BCLK")
R("R120", "100k", "I2S_DIN", "GND", role="AD2 strap")
C("C120", "100nF", "+3V3", ic="U13", pin="1")
C("C121", "100nF", "+3V3", ic="U13", pin="5")
C("C122", "1uF", "+3V3A", ic="U13", pin="12")
C("C123", "1uF", "ADC_REFQ")
C("C124", "1uF", "ADC_REFP")
P("U14", A + "TS5A23157DGSR", "TS5A23157", A + "MSOP-10_L3.0-W3.0-P0.50-LS5.0-BL",
  {"n:IN1": "DJ_SEL", "n:IN2": "DJ_SEL", "n:NO1": "MAIN_L",
   "n:NC1": "LSW_L", "n:COM1": "AMP_IN_L", "n:NO2": "MAIN_R",
   "n:NC2": "LSW_R", "n:COM2": "AMP_IN_R", "n:V+": "+5VA", "n:GND": "GND"},
  lcsc="C11133", role="DAC path / DJ direct selector")
C("C125", "100nF", "+5VA", ic="U14", pin="8")
# 3.3 V expander to the switch's 5 V logic (VIH 3.5 V): an inverter.
# DJ_DIRECT low (default) -> DJ_SEL high -> NO = DAC path.
P("Q6", A + "S8050_C2146", "S8050", A + "x",
  {"n:B": "DJ_B", "n:E": "GND", "n:C": "DJ_SEL"}, lcsc="C2146", basic=True,
  role="DJ-direct level shift / inverter")
R("R121", "10k", "DJ_DIRECT", "DJ_B")
R("R122", "10k", "+5VA", "DJ_SEL")
# B13 TOSLINK [Ultra]
P("U19", A + "PLR237_T10BK", "PLR237/T8BK", A + "x",
  {"n:VCC": "OPT_VCC", "n:GND": "GND", "n:VOUT": "OPT_SPDIF", "n:NC": "NC"},
  lcsc="C17223620", dnp=True, role="TOSLINK receiver [Ultra]", nc_rest=True)
FB("FB3", "+3V3", "OPT_VCC", dnp=True)
C("C126", "100nF", "OPT_VCC", dnp=True)

import blocks_amp  # noqa: E402,F401
