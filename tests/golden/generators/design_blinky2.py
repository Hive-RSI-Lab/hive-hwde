"""Golden board 1: blinky2 - 2-layer STM32F103 blinky (50 x 40 mm).

STM32F103C8T6 + AMS1117-3.3 LDO + 8 MHz crystal + LED on PA5 + SWD header.
Verification-relevant features baked in for the S1 mutants:
  - crystal (OSC_IN/OSC_OUT) traces on F.Cu over the continuous B.Cu GND
    pour -> plane-split-under-clock mutant target
  - 0.8 mm 3V3 feed trace from the LDO -> undersized-power-trace target
    (the (118.5,106.95)->(118.5,110.5) segment)
  - C1 decoupler at VDD_3 (pin 48) -> decoupler-moved-15mm target
    (relocation corridor at (132..141, 105.5..108) kept free on purpose)
  - D1 LED is polarized, mounted at 180 deg -> cpl-rotation target
  - D1/R2 pads exposed on F.Cu -> silk-over-pad target

Stdlib-only: imported by sch_build.py (venv) AND pcb_build.py (bundled).
All schematic coords are 1.27 mm multiples; PCB coords are free (mm).

Routing discipline (hand-authored Manhattan copper):
  - left-column fan-out lanes at x = 120.1 / 120.6 / 121.1 (upper pin takes
    the outer lane, so exit horizontals never cross lower verticals)
  - F.Cu GND pour (107..126.5, 114.5..134.3) supplies VSSA, crystal caps and
    C4/C9 grounds; B.Cu is one solid GND reference pour
  - 3V3 loops: west feed bus + top corridor (y=104.2) + two east bridges
    around the SWD tracks
"""

DESIGN = {
    "name": "blinky2",
    "title": "Golden 1: 2-layer STM32 blinky",
    "layers": 2,
    "outline": (100.0, 100.0, 150.0, 140.0),
    "sch": {
        "paper": "A3",
        "rails": [
            {"net": "GND", "sym": "power:GND", "pos": (40.64, 254.0), "flag": True},
            {"net": "+3V3", "sym": "power:+3V3", "at_pin": ("C5", "1")},
            {"net": "+5V", "sym": "power:+5V", "pos": (40.64, 264.16), "flag": True},
        ],
    },
    "components": [
        {
            "ref": "U1", "sym": "MCU_ST_STM32F1:STM32F103C8Tx",
            "fp": "Package_QFP:LQFP-48_7x7mm_P0.5mm", "value": "STM32F103C8T6",
            "sch": (203.2, 152.4), "pcb": (128.0, 118.0, 0),
            "expect": {"7": "NRST", "44": "BOOT0", "34": "PA13", "37": "PA14",
                       "5": "PD0", "6": "PD1", "15": "PA5", "24": "VDD",
                       "9": "VDDA", "1": "VBAT"},
            "pins": {
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
            },
        },
        {
            "ref": "U2", "sym": "Regulator_Linear:AMS1117-3.3",
            "fp": "Package_TO_SOT_SMD:SOT-223-3_TabPin2", "value": "AMS1117-3.3",
            "sch": (81.28, 60.96), "pcb": (111.6, 106.0, 0),
            "expect": {"1": "GND", "2": "VO", "3": "VI"},
            "pins": {"1": "GND", "2": "+3V3", "3": "+5V"},
        },
        {
            "ref": "J1", "sym": "Connector_Generic:Conn_01x02",
            "fp": "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
            "value": "PWR_5V", "sch": (40.64, 60.96), "pcb": (102.8, 103.5, 90),
            "ref_at": (0.0, 3.2),
            "pins": {"1": "+5V", "2": "GND"},
        },
        {
            "ref": "J2", "sym": "Connector_Generic:Conn_01x04",
            "fp": "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical",
            "value": "SWD", "sch": (60.96, 152.4), "pcb": (146.5, 110.5, 270),
            "ref_at": (-3.5, 3.2),
            "pins": {"1": "+3V3", "2": "SWDIO", "3": "SWCLK", "4": "GND"},
        },
        {
            "ref": "Y1", "sym": "Device:Crystal_GND24",
            "fp": "Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm", "value": "8MHz",
            "sch": (281.94, 137.16), "pcb": (114.0, 128.0, 0),
            "pins": {"1": "OSC_IN", "2": "GND", "3": "OSC_OUT", "4": "GND"},
        },
        {
            "ref": "C1", "sym": "Device:C", "fp": "Capacitor_SMD:C_0603_1608Metric",
            "value": "100nF", "sch": (152.4, 240.03), "pcb": (124.5, 110.5, 0),
            "pins": {"1": "+3V3", "2": "GND"},
        },
        {
            "ref": "C2", "sym": "Device:C", "fp": "Capacitor_SMD:C_0603_1608Metric",
            "value": "100nF", "sch": (167.64, 240.03), "pcb": (133.0, 125.5, 0),
            "pins": {"1": "+3V3", "2": "GND"},
        },
        {
            "ref": "C3", "sym": "Device:C", "fp": "Capacitor_SMD:C_0603_1608Metric",
            "value": "100nF", "sch": (182.88, 240.03), "pcb": (135.7, 114.3, 0),
            "pins": {"1": "+3V3", "2": "GND"},
        },
        {
            "ref": "C4", "sym": "Device:C", "fp": "Capacitor_SMD:C_0603_1608Metric",
            "value": "100nF", "sch": (198.12, 240.03), "pcb": (123.3, 125.0, 90),
            "pins": {"1": "+3V3", "2": "GND"},
        },
        {
            "ref": "C7", "sym": "Device:C", "fp": "Capacitor_SMD:C_0603_1608Metric",
            "value": "22pF", "sch": (281.94, 152.4), "pcb": (109.0, 126.1, 180),
            "pins": {"1": "OSC_IN", "2": "GND"},
        },
        {
            "ref": "C8", "sym": "Device:C", "fp": "Capacitor_SMD:C_0603_1608Metric",
            "value": "22pF", "sch": (297.18, 152.4), "pcb": (118.4, 130.4, 270),
            "pins": {"1": "OSC_OUT", "2": "GND"},
        },
        {
            "ref": "C5", "sym": "Device:C", "fp": "Capacitor_SMD:C_0805_2012Metric",
            "value": "10uF", "sch": (99.06, 71.12), "pcb": (117.6, 106.0, 90),
            "ref_at": (0.0, 2.9),
            "pins": {"1": "+3V3", "2": "GND"},
        },
        {
            "ref": "C6", "sym": "Device:C", "fp": "Capacitor_SMD:C_0805_2012Metric",
            "value": "10uF", "sch": (63.5, 71.12), "pcb": (106.0, 111.0, 0),
            "pins": {"1": "+5V", "2": "GND"},
        },
        {
            "ref": "C9", "sym": "Device:C", "fp": "Capacitor_SMD:C_0603_1608Metric",
            "value": "100nF", "sch": (139.7, 220.98), "pcb": (123.5, 129.5, 90),
            "pins": {"1": "NRST", "2": "GND"},
        },
        {
            "ref": "R1", "sym": "Device:R", "fp": "Resistor_SMD:R_0603_1608Metric",
            "value": "10k", "sch": (160.02, 220.98), "pcb": (130.5, 106.5, 90),
            "pins": {"1": "BOOT0", "2": "GND"},
        },
        {
            "ref": "R2", "sym": "Device:R", "fp": "Resistor_SMD:R_0603_1608Metric",
            "value": "470", "sch": (281.94, 106.68), "pcb": (127.1, 129.5, 0),
            "pins": {"1": "LED", "2": "LED_A"},
        },
        {
            # polarized part at a deliberate 180: cpl-rotation mutant flips it
            "ref": "D1", "sym": "Device:LED", "fp": "LED_SMD:LED_0805_2012Metric",
            "value": "LED_red", "sch": (299.72, 106.68), "pcb": (131.5, 129.5, 180),
            "pins": {"1": "GND", "2": "LED_A"},
        },
    ],
    "pcb": {
        "tracks": [
            # ---- 5V input: J1.1 -> C6.1 bulk cap, branch to U2.VI
            {"net": "+5V", "layer": "F.Cu", "width": 1.0,
             "pts": [(102.8, 103.5), (102.8, 111.0), (105.05, 111.0)]},
            {"net": "+5V", "layer": "F.Cu", "width": 1.0,
             "pts": [(102.8, 108.3), (108.45, 108.3)]},
            # ---- 3V3 main feed: U2.VO (small pin 2 AND tab) -> C5.1 -> head
            {"net": "+3V3", "layer": "F.Cu", "width": 0.5,
             "pts": [(108.45, 106.0), (114.75, 106.0)]},
            {"net": "+3V3", "layer": "F.Cu", "width": 0.8,
             "pts": [(114.75, 106.0), (116.1, 106.0), (116.1, 106.95),
                     (118.9, 106.95)]},
            # undersized-power-trace mutant target segment:
            {"net": "+3V3", "layer": "F.Cu", "width": 0.8,
             "pts": [(118.5, 106.95), (118.5, 110.5)]},
            # ---- 3V3 west bus to C1.1 (decoupler-moved mutant re-joins here)
            {"net": "+3V3", "layer": "F.Cu", "width": 0.5,
             "pts": [(118.5, 110.5), (122.5, 110.5), (123.725, 110.5)]},
            # ---- 3V3 top corridor to J2.1
            {"net": "+3V3", "layer": "F.Cu", "width": 0.5,
             "pts": [(118.9, 106.95), (118.9, 104.2), (146.5, 104.2),
                     (146.5, 110.5)]},
            # ---- 3V3 east bridge a: corridor -> C3.1/pin36 cluster
            {"net": "+3V3", "layer": "F.Cu", "width": 0.25,
             "pts": [(142.7, 104.2), (142.7, 113.4), (134.925, 113.4),
                     (134.925, 114.3)]},
            # ---- 3V3 east bridge b: corridor -> C2.1/pin24 cluster
            {"net": "+3V3", "layer": "F.Cu", "width": 0.25,
             "pts": [(145.35, 104.2), (145.35, 121.4), (132.225, 121.4),
                     (132.225, 125.5)]},
            # ---- 3V3 risers: VBAT(1), VDD_3(48), VDD_1(24), VDD_2(36)
            {"net": "+3V3", "layer": "F.Cu", "width": 0.25,
             "pts": [(122.5, 110.5), (122.5, 115.25), (123.838, 115.25)]},
            {"net": "+3V3", "layer": "F.Cu", "width": 0.25,
             "pts": [(122.5, 112.2), (125.25, 112.2), (125.25, 113.838)]},
            {"net": "+3V3", "layer": "F.Cu", "width": 0.25,
             "pts": [(130.75, 122.162), (130.75, 125.5), (132.225, 125.5)]},
            {"net": "+3V3", "layer": "F.Cu", "width": 0.25,
             "pts": [(132.162, 115.25), (134.925, 115.25), (134.925, 114.3)]},
            # ---- VDDA(9) via the x=122.5 channel -> C4.1 (pad 1, south side)
            {"net": "+3V3", "layer": "F.Cu", "width": 0.25,
             "pts": [(123.838, 119.25), (122.5, 119.25), (122.5, 125.775),
                     (123.3, 125.775)]},
            # ---- VDDA star feed: west bus -> B.Cu hop -> C4.1 (keeps the
            # B.Cu jog 4+ mm east of the OSC return corridor)
            {"net": "+3V3", "layer": "B.Cu", "width": 0.4,
             "pts": [(121.0, 110.5), (125.1, 110.5), (125.1, 124.9),
                     (124.4, 124.9)]},
            {"net": "+3V3", "layer": "F.Cu", "width": 0.25,
             "pts": [(124.4, 124.9), (124.4, 125.775), (123.75, 125.775)]},
            # ---- crystal (clock traces over B.Cu pour: plane-split target)
            {"net": "OSC_IN", "layer": "F.Cu", "width": 0.25,
             "pts": [(123.838, 117.25), (120.1, 117.25), (120.1, 126.1),
                     (109.775, 126.1)]},
            {"net": "OSC_IN", "layer": "F.Cu", "width": 0.25,
             "pts": [(111.3, 126.1), (111.3, 128.8), (112.9, 128.8)]},
            {"net": "OSC_OUT", "layer": "F.Cu", "width": 0.25,
             "pts": [(123.838, 117.75), (120.6, 117.75), (120.6, 127.2),
                     (115.1, 127.2)]},
            {"net": "OSC_OUT", "layer": "F.Cu", "width": 0.25,
             "pts": [(118.4, 127.2), (118.4, 129.625)]},
            # ---- NRST pin7 lane x=121.1 -> C9.1
            {"net": "NRST", "layer": "F.Cu", "width": 0.25,
             "pts": [(123.838, 118.25), (121.1, 118.25), (121.1, 130.275),
                     (123.5, 130.275)]},
            # ---- BOOT0 pin44 -> R1.1
            {"net": "BOOT0", "layer": "F.Cu", "width": 0.25,
             "pts": [(127.25, 113.838), (127.25, 111.2), (130.5, 111.2),
                     (130.5, 107.325)]},
            # ---- LED chain: PA5(15) -> R2.1, R2.2 -> D1.2 (anode)
            {"net": "LED", "layer": "F.Cu", "width": 0.25,
             "pts": [(126.25, 122.162), (126.25, 129.5)]},
            {"net": "LED_A", "layer": "F.Cu", "width": 0.25,
             "pts": [(127.925, 129.5), (130.5625, 129.5)]},
            # ---- SWD
            {"net": "SWCLK", "layer": "F.Cu", "width": 0.25,
             "pts": [(130.75, 113.838), (130.75, 112.4), (141.42, 112.4),
                     (141.42, 110.5)]},
            {"net": "SWDIO", "layer": "F.Cu", "width": 0.25,
             "pts": [(132.162, 116.25), (133.4, 116.25), (133.4, 120.2),
                     (143.96, 120.2), (143.96, 110.5)]},
            # ---- GND stubs (pour or via terminated)
            {"net": "GND", "layer": "F.Cu", "width": 0.25,   # VSSA pin 8
             "pts": [(123.838, 118.75), (121.8, 118.75), (121.8, 121.5)]},
            {"net": "GND", "layer": "F.Cu", "width": 0.25,   # VSS_3 pin 47
             "pts": [(125.75, 113.838), (125.75, 112.75), (126.6, 112.75),
                     (126.6, 111.5)]},
            {"net": "GND", "layer": "F.Cu", "width": 0.25,   # VSS_1 pin 23
             "pts": [(130.25, 122.162), (130.25, 123.6), (129.7, 123.6),
                     (129.7, 124.1)]},
            {"net": "GND", "layer": "F.Cu", "width": 0.25,   # VSS_2 pin 35
             "pts": [(132.162, 115.75), (134.1, 115.75), (134.1, 116.0)]},
            {"net": "GND", "layer": "F.Cu", "width": 0.25,
             "pts": [(125.275, 110.5), (125.275, 109.2)]},   # C1.2
            {"net": "GND", "layer": "F.Cu", "width": 0.25,
             "pts": [(133.775, 125.5), (134.9, 125.5)]},     # C2.2
            {"net": "GND", "layer": "F.Cu", "width": 0.25,
             "pts": [(136.475, 114.3), (137.8, 114.3)]},     # C3.2
            {"net": "GND", "layer": "F.Cu", "width": 0.25,
             "pts": [(130.5, 105.675), (130.5, 105.05)]},    # R1.2
            {"net": "GND", "layer": "F.Cu", "width": 0.25,
             "pts": [(132.4375, 129.5), (133.5, 129.5)]},    # D1.1 cathode
            {"net": "GND", "layer": "F.Cu", "width": 0.25,
             "pts": [(105.34, 103.5), (105.34, 105.1)]},     # J1.2
            {"net": "GND", "layer": "F.Cu", "width": 0.25,
             "pts": [(108.45, 103.7), (110.1, 103.7)]},      # U2.1
            {"net": "GND", "layer": "F.Cu", "width": 0.25,
             "pts": [(117.6, 105.05), (117.6, 103.8)]},      # C5.2
            {"net": "GND", "layer": "F.Cu", "width": 0.25,
             "pts": [(106.95, 110.5), (108.1, 110.5)]},      # C6.2
            {"net": "GND", "layer": "F.Cu", "width": 0.25,
             "pts": [(138.88, 110.5), (138.88, 111.7)]},     # J2.4
        ],
        "vias": [
            # 3V3 hop for the VDDA star feed
            {"net": "+3V3", "at": (121.0, 110.5)},
            {"net": "+3V3", "at": (124.4, 124.9)},
            # GND via inside the fan-out fill pocket (ties the pocket island
            # and the VSSA stub to the B.Cu reference pour)
            {"net": "GND", "at": (121.8, 121.5)},
            # GND stub terminations
            {"net": "GND", "at": (126.6, 111.5)},
            {"net": "GND", "at": (129.7, 124.1)},
            {"net": "GND", "at": (134.1, 116.0)},
            {"net": "GND", "at": (125.275, 109.2)},
            {"net": "GND", "at": (134.9, 125.5)},
            {"net": "GND", "at": (137.8, 114.3)},
            {"net": "GND", "at": (130.5, 105.05)},
            {"net": "GND", "at": (133.5, 129.5)},
            {"net": "GND", "at": (105.34, 105.1)},
            {"net": "GND", "at": (110.1, 103.7)},
            {"net": "GND", "at": (117.6, 103.8)},
            {"net": "GND", "at": (108.1, 110.5)},
            {"net": "GND", "at": (138.88, 111.7)},
            # F.Cu pour <-> B.Cu pour ties (all inside the F pour rect)
            {"net": "GND", "at": (117.5, 116.5)},
            {"net": "GND", "at": (117.5, 122.0)},
            {"net": "GND", "at": (124.5, 127.5)},
            {"net": "GND", "at": (116.8, 130.5)},
            {"net": "GND", "at": (123.0, 132.0)},
            {"net": "GND", "at": (121.0, 133.0)},
            {"net": "GND", "at": (108.0, 125.5)},
            {"net": "GND", "at": (109.5, 124.0)},
            {"net": "GND", "at": (107.9, 132.5)},
        ],
        "zones": [
            # B.Cu: solid GND reference pour (the return-path golden surface)
            {"net": "GND", "layer": "B.Cu",
             "rect": (100.6, 100.6, 149.4, 139.4), "min_thickness": 0.25},
            # F.Cu: GND fill over the fan-out / crystal quarter. 0.3 zone
            # clearance (default 0.5 starves the 0.75 mm fan-out pocket)
            {"net": "GND", "layer": "F.Cu",
             "rect": (107.0, 114.5, 126.5, 134.3), "min_thickness": 0.25,
             "clearance": 0.3},
        ],
        "silk": [
            {"text": "blinky2", "at": (108.0, 136.5), "layer": "F.SilkS"},
        ],
    },
}
