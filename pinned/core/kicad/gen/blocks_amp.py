"""core: TPA3221 zones A and B (B10, B11) and the UI (B12, B14)."""
from __future__ import annotations

from design import C, P, R, sheet

A = "aiee:"


def zone(z: str, base: int, ultra: bool):
    """One TPA3221 stereo BTL stage. Zone A is the controller, zone B the
    peripheral clocked from it."""
    sheet(f"amp_{z.lower()}", dnp=ultra)
    u = f"U{15 if z == 'A' else 16}"
    n = lambda s: f"{z}_{s}"  # noqa: E731
    P(u, A + "TPA3221DDVR", "TPA3221", A + "HTSSOP-44_L14.0-W6.1-P0.64-LS8.1-BL",
      {"n:VDD": "+5V", "n:GAIN/SLV": n("GAIN"), "n:~{OTW_CLIP}": "AMP_OTW",
       "n:~{FAULT}": "AMP_FAULT", "n:GND": "GND", "n:IN1_P": n("IN1P"),
       "n:IN1_M": n("IN1M"), "n:~{RESET}": f"AMP_{z}_RST", "n:HEAD": "GND",
       "n:OSCM": "AMP_OSCM", "n:OSCP": "AMP_OSCP", "n:FREQ_ADJ": n("FREQ"),
       "n:IN2_P": n("IN2P"), "n:IN2_M": n("IN2M"), "n:~{CMUTE}": n("CMUTE"),
       "n:AVDD": n("AVDD"), "n:GVDD": n("GVDD"), "n:BST1_M": n("BST1M"),
       "n:BST1_P": n("BST1P"), "n:BST2_M": n("BST2M"), "n:BST2_P": n("BST2P"),
       "n:OUT1_M": n("OUT1M"), "n:OUT1_P": n("OUT1P"), "n:OUT2_M": n("OUT2M"),
       "n:OUT2_P": n("OUT2P"), "n:PVDD": "PVDD"},
      lcsc="C475500", role=f"zone {z} class-D amp, 2 x 77 W into 4 ohm at 28 V")
    i = base
    def nr():
        nonlocal i
        i += 1
        return i
    # supplies: VDD/AVDD from +5V (LDO bypassed), GVDD through 4.7R
    C(f"C{nr()}", "10uF", "+5V", pkg="0603", ic=u, pin="1")
    C(f"C{nr()}", "100nF", "+5V", ic=u, pin="1")
    R(f"R{nr()}", "4.7R", "+5V", n("AVDD"), pkg="0603")
    C(f"C{nr()}", "1uF", n("AVDD"), ic=u, pin="21")
    R(f"R{nr()}", "4.7R", "+5V", n("GVDD"), pkg="0603")
    C(f"C{nr()}", "1uF", n("GVDD"), ic=u, pin="22")
    for pin in ("29", "36"):
        C(f"C{nr()}", "1uF/50V", "PVDD", pkg="0805", ic=u, pin=pin)
        C(f"C{nr()}", "100nF/50V", "PVDD", pkg="0603", ic=u, pin=pin)
    if z == "B":
        P(f"C{nr()}", "Device:C_Polarized", "470uF/50V",
          A + "CAP-TH_BD10.0-P5.00-D1.0-FD", {"1": "PVDD", "2": "GND"},
          lcsc="C88733", role="PVDD bulk, zone B")
    C(f"C{nr()}", "33nF/50V", n("CMUTE"), pkg="0603")
    if z == "A":   # controller, 24 dB, nominal 600 kHz (2 x 100k = 50k)
        R(f"R{nr()}", "20k", n("GAIN"), "GND")
        R(f"R{nr()}", "100k", n("GAIN"), n("AVDD"))
        R(f"R{nr()}", "100k", n("FREQ"), "GND")
        R(f"R{nr()}", "100k", n("FREQ"), "GND")
        R(f"R{nr()}", "47k", "AMP_OSCM", n("AVDD"))
        R(f"R{nr()}", "47k", "AMP_OSCP", "GND")
    else:          # peripheral 1, 24 dB, oscillator off
        R(f"R{nr()}", "75k", n("GAIN"), "GND")
        R(f"R{nr()}", "47k", n("GAIN"), n("AVDD"))
        P(f"R{nr()}", "Device:R", "0R", A + "R0402",
          {"1": n("FREQ"), "2": n("AVDD")}, lcsc="C17168", basic=True)
    # inputs: single-ended, IN_M AC-grounded
    C(f"C{nr()}", "10uF/25V", "AMP_IN_L", n("IN1P"), pkg="0805")
    C(f"C{nr()}", "10uF/25V", n("IN1M"), pkg="0805")
    C(f"C{nr()}", "10uF/25V", "AMP_IN_R", n("IN2P"), pkg="0805")
    C(f"C{nr()}", "10uF/25V", n("IN2M"), pkg="0805")
    # bootstraps
    for b, o in (("BST1M", "OUT1M"), ("BST1P", "OUT1P"), ("BST2M", "OUT2M"),
                 ("BST2P", "OUT2P")):
        C(f"C{nr()}", "33nF/50V", n(b), n(o), pkg="0603")
    # output filters + snubbers
    lbase = 1 if z == "A" else 5
    jbase = 6 if z == "A" else 10
    # L1 top-left, L2 bottom-left, L3 bottom-right, L4 top-right (anchors.py).
    # The amp's inversion is undone in the DAC stage (U11 inputs swapped), so
    # OUTx_P is the + terminal here.
    outs = (("OUT1M", "SPK_L-"), ("OUT1P", "SPK_L+"), ("OUT2M", "SPK_R-"),
            ("OUT2P", "SPK_R+"))
    for k, (o, spk) in enumerate(outs):
        net = f"{z}_{spk}"
        P(f"L{lbase + k}", A + "PSPMAA0805-100M-ANP", "10uH", A + "x",
          {"1": n(o), "2": net}, lcsc="C2962885",
          role="output filter 10 uH, Isat 10 A (= TPA3221 OC limit)")
        C(f"C{nr()}", "1uF/50V", net, pkg="0805")
        R(f"R{nr()}", "4.7R", net, n(o + "_SN"), pkg="0603")
        C(f"C{nr()}", "1nF/50V", n(o + "_SN"), pkg="0603")
    # speaker terminals: one 2-way 5 mm screw terminal per channel; the
    # enclosure's banana sockets / binding posts wire to these
    for k, ch in enumerate(("L", "R")):
        P(f"J{jbase + k}", A + "KF301-5.0-2P", f"SPK {z} {ch}", A + "x",
          {"1": f"{z}_SPK_{ch}+", "2": f"{z}_SPK_{ch}-"}, lcsc="C474881", mpn="KF301-5.0-2P",
          role=f"zone {z} {ch} speaker terminal, 17 A", nc_rest=True)


zone("A", 200, ultra=False)
zone("B", 300, ultra=True)

# ------------------------------------------------------------ B12 UI
sheet("ui")
P("SW1", A + "EC11E1834403", "EC11", A + "SW-TH_EC11E1834403",
  {"A": "ENC_A", "B": "ENC_B", "C": "GND", "D": "ENC_SW",
   "E": "GND", "F": "GND", "G": "GND"}, lcsc="C361165", plus_only=True, role="volume knob [Plus]",
  nc_rest=True)
P("SW2", A + "TS-1187A-B-A-B", "BTN1", A + "KEY-SMD_4P-L5.1-W5.1-P3.70-LS6.5",
  {"1": "BTN_1", "2": "BTN_1", "3": "GND", "4": "GND"}, lcsc="C318884",
  basic=True, mpn="TS-1187A-B-A-B", plus_only=True, role="source button [Plus]")
P("SW3", A + "TS-1187A-B-A-B", "BTN2", A + "KEY-SMD_4P-L5.1-W5.1-P3.70-LS6.5",
  {"1": "BTN_2", "2": "BTN_2", "3": "GND", "4": "GND"}, lcsc="C318884",
  basic=True, mpn="TS-1187A-B-A-B", plus_only=True, role="preset button [Plus]")
# Front-facing IRM-3638T at the front edge, lens through the front wall (the
# top-looking IRM-H638T sat >21 mm under the lid; review-2 DFM-4, as on Lite)
P("U18", A + "IRM-3638T_C42421366", "IRM-3638T", A + "OPTO-TH_IRM-3638T",
  {"1": "IR_IN", "2": "GND", "3": "IR_VCC"}, lcsc="C42421366",
  plus_only=True, role="38 kHz IR receiver, front-facing [Plus]")
R("R400", "100R", "+3V3", "IR_VCC", plus_only=True)
C("C400", "4.7uF", "IR_VCC", pkg="0603", plus_only=True)
P("D3", A + "KT-0603R", "KT-0603R", A + "x",
  {"n:K": "GND", "n:A": "LED_A"}, lcsc="C2286", basic=True,
  role="status LED, red (Vf ~2 V)")
R("R401", "470R", "LED_STATUS", "LED_A")
# B14 FFC to ultra-front [Ultra]
# pin map agreed with the ultra-front worker (261028-145644, their 11:03 map):
# rails mirror (1/20, 2/19, 3/18) so a reversed cable shorts nothing; the
# front board carries its own TCA9535 (0x27) on the shared I2C bus.
FFC = {"1": "GND", "2": "+3V3", "3": "+5V", "4": "GND", "5": "FFC_SCK",
       "6": "GND", "7": "FFC_MOSI", "8": "LCD_CS", "9": "LCD_DC",
       "10": "LCD_BL", "11": "GND", "12": "I2C_SCL", "13": "I2C_SDA",
       "14": "EXP_INT", "15": "IR_IN", "16": "ENC_A", "17": "ENC_B",
       "18": "+5V", "19": "+3V3", "20": "GND", "21": "GND", "22": "GND"}
R("R402", "33R", "LCD_SCLK", "FFC_SCK", dnp=True, role="SPI series term [Ultra]")
R("R403", "33R", "LCD_MOSI", "FFC_MOSI", dnp=True, role="SPI series term [Ultra]")
P("J3", A + "FPC-05F-20PH20", "FFC 20P", A + "x",
  dict(FFC), lcsc="C2856803", dnp=True, role="front-board FFC [Ultra]",
  nc_rest=True)
