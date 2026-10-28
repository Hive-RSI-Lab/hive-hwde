"""Golden board 2: usbbuck4 - 4-layer USB-FS + buck (60 x 45 mm).

STM32F103C8T6 (USB FS device) + AP63203 5V->3.3V buck + USB micro-B +
8 MHz crystal + SWD + MCO clock-out header. Stackup: F.Cu / In1.Cu (GND
plane) / In2.Cu (power plane: 3V3 + VBUS island + GND strip) / B.Cu.

Verification-relevant features baked in for the S1 mutants:
  - USB_DP/USB_DM differential pair J1 -> U1 on F.Cu -> diffpair-skew target
  - MCO clock net transitions F.Cu -> B.Cu at (141.0,123.0) with a GND
    return via at (141.9,123.4); In2 carries a GND strip under the B.Cu
    corridor so the reference net stays GND -> missing-return-via target
    (mutant deletes the (141.9,123.4) via; next GND via is >2 mm away)
  - buck output current path (L1 -> C11 -> plane vias)
  - polarized D1 (power LED) for CPL rotation coverage

This board is S9-S11's placement/routing testbed: netlist is real, every
net routes, schematic parity is exact.
"""

DESIGN = {
    "name": "usbbuck4",
    "title": "Golden 2: 4-layer USB-FS + buck",
    "layers": 4,
    "outline": (100.0, 100.0, 160.0, 145.0),
    "sch": {
        "paper": "A3",
        "pin_number_fixups": [
            # kicad-sch-api writes the USB shield pin as "6"; the library
            # and every Connector_USB footprint call it "SH"
            {"lib_id": "Connector:USB_B_Micro", "pin_name": "Shield",
             "wrong": "6", "right": "SH"},
        ],
        "rails": [
            # J1 (USB) VBUS/GND pins are power_out: those nets are driven,
            # a PWR_FLAG there would conflict. 3V3 is fed through L1
            # (passive), so it does need the flag.
            {"net": "GND", "sym": "power:GND", "pos": (40.64, 254.0),
             "flag": False},
            {"net": "+3V3", "sym": "power:+3V3", "pos": (40.64, 259.08),
             "flag": True},
            {"net": "VBUS", "sym": "power:VBUS", "pos": (40.64, 264.16),
             "flag": False},
        ],
    },
    "components": [
        {
            "ref": "U1", "sym": "MCU_ST_STM32F1:STM32F103C8Tx",
            "fp": "Package_QFP:LQFP-48_7x7mm_P0.5mm", "value": "STM32F103C8T6",
            "sch": (203.2, 152.4), "pcb": (135.5, 117.5, 0),
            "expect": {"7": "NRST", "44": "BOOT0", "34": "PA13", "37": "PA14",
                       "5": "PD0", "6": "PD1", "29": "PA8", "32": "PA11",
                       "33": "PA12", "9": "VDDA", "1": "VBAT"},
            "pins": {
                "1": "+3V3", "2": "NC", "3": "NC", "4": "NC",
                "5": "OSC_IN", "6": "OSC_OUT", "7": "NRST", "8": "GND",
                "9": "+3V3", "10": "NC", "11": "NC", "12": "NC",
                "13": "NC", "14": "NC", "15": "NC", "16": "NC",
                "17": "NC", "18": "NC", "19": "NC", "20": "NC",
                "21": "NC", "22": "NC", "23": "GND", "24": "+3V3",
                "25": "NC", "26": "NC", "27": "NC", "28": "NC",
                "29": "MCO", "30": "NC", "31": "NC", "32": "USB_DM",
                "33": "USB_DP", "34": "SWDIO", "35": "GND", "36": "+3V3",
                "37": "SWCLK", "38": "NC", "39": "NC", "40": "NC",
                "41": "NC", "42": "NC", "43": "NC", "44": "BOOT0",
                "45": "NC", "46": "NC", "47": "GND", "48": "+3V3",
            },
        },
        {
            "ref": "U2", "sym": "Regulator_Switching:AP63203WU",
            "fp": "Package_TO_SOT_SMD:TSOT-23-6", "value": "AP63203WU",
            "sch": (81.28, 60.96), "pcb": (110.0, 105.5, 0), "ref_at": (0.0, 3.4),
            "expect": {"1": "FB", "2": "EN", "3": "IN", "4": "GND", "5": "SW",
                       "6": "BST"},
            "pins": {"1": "+3V3", "2": "VBUS", "3": "VBUS", "4": "GND",
                     "5": "SW", "6": "BST"},
        },
        {
            "ref": "J1", "sym": "Connector:USB_B_Micro",
            "fp": "Connector_USB:USB_Micro-B_Amphenol_10118194_Horizontal",
            "value": "USB_micro", "sch": (40.64, 152.4), "pcb": (103.4, 131.5, 270),
            "pins": {"1": "VBUS", "2": "USB_DM", "3": "USB_DP", "4": "NC",
                     "5": "GND", "SH": "GND"},
        },
        {
            "ref": "J2", "sym": "Connector_Generic:Conn_01x04",
            "fp": "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical",
            "value": "SWD", "sch": (60.96, 152.4), "pcb": (156.5, 110.5, 270),
            "ref_at": (-3.5, 3.2),
            "pins": {"1": "+3V3", "2": "SWDIO", "3": "SWCLK", "4": "GND"},
        },
        {
            "ref": "J3", "sym": "Connector_Generic:Conn_01x02",
            "fp": "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
            "value": "MCO_OUT", "sch": (60.96, 203.2), "pcb": (146.0, 141.2, 90),
            "ref_at": (0.0, -2.8),
            "pins": {"1": "MCO", "2": "GND"},
        },
        {
            "ref": "L1", "sym": "Device:L", "fp": "Inductor_SMD:L_1210_3225Metric",
            "value": "4.7uH", "sch": (106.68, 60.96), "pcb": (118.5, 105.5, 270),
            "pins": {"1": "SW", "2": "+3V3"},
        },
        {
            "ref": "Y1", "sym": "Device:Crystal_GND24",
            "fp": "Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm", "value": "8MHz",
            "sch": (281.94, 137.16), "pcb": (121.5, 127.5, 0),
            "pins": {"1": "OSC_IN", "2": "GND", "3": "OSC_OUT", "4": "GND"},
        },
        {
            "ref": "C1", "sym": "Device:C", "fp": "Capacitor_SMD:C_0603_1608Metric",
            "value": "100nF", "sch": (152.4, 240.03), "pcb": (132.0, 110.0, 0),
            "pins": {"1": "+3V3", "2": "GND"},
        },
        {
            "ref": "C2", "sym": "Device:C", "fp": "Capacitor_SMD:C_0603_1608Metric",
            "value": "100nF", "sch": (167.64, 240.03), "pcb": (140.5, 125.0, 0),
            "pins": {"1": "+3V3", "2": "GND"},
        },
        {
            "ref": "C3", "sym": "Device:C", "fp": "Capacitor_SMD:C_0603_1608Metric",
            "value": "100nF", "sch": (182.88, 240.03), "pcb": (143.2, 113.8, 0),
            "pins": {"1": "+3V3", "2": "GND"},
        },
        {
            "ref": "C4", "sym": "Device:C", "fp": "Capacitor_SMD:C_0603_1608Metric",
            "value": "100nF", "sch": (198.12, 240.03), "pcb": (130.8, 124.5, 90),
            "pins": {"1": "+3V3", "2": "GND"},
        },
        {
            "ref": "C5", "sym": "Device:C", "fp": "Capacitor_SMD:C_0805_2012Metric",
            "value": "10uF", "sch": (99.06, 240.03), "pcb": (145.4, 108.0, 90),
            "pins": {"1": "+3V3", "2": "GND"},
        },
        {
            "ref": "C7", "sym": "Device:C", "fp": "Capacitor_SMD:C_0603_1608Metric",
            "value": "22pF", "sch": (281.94, 152.4), "pcb": (116.5, 125.6, 180),
            "pins": {"1": "OSC_IN", "2": "GND"},
        },
        {
            "ref": "C8", "sym": "Device:C", "fp": "Capacitor_SMD:C_0603_1608Metric",
            "value": "22pF", "sch": (297.18, 152.4), "pcb": (125.9, 129.9, 270),
            "pins": {"1": "OSC_OUT", "2": "GND"},
        },
        {
            "ref": "C9", "sym": "Device:C", "fp": "Capacitor_SMD:C_0603_1608Metric",
            "value": "100nF", "sch": (139.7, 220.98), "pcb": (131.0, 129.0, 90),
            "pins": {"1": "NRST", "2": "GND"},
        },
        {
            "ref": "C10", "sym": "Device:C", "fp": "Capacitor_SMD:C_0805_2012Metric",
            "value": "10uF", "sch": (63.5, 71.12), "pcb": (104.8, 105.5, 90),
            "pins": {"1": "VBUS", "2": "GND"},
        },
        {
            "ref": "C11", "sym": "Device:C", "fp": "Capacitor_SMD:C_0805_2012Metric",
            "value": "22uF", "sch": (121.92, 71.12), "pcb": (124.0, 105.5, 90),
            "pins": {"1": "+3V3", "2": "GND"},
        },
        {
            "ref": "C12", "sym": "Device:C", "fp": "Capacitor_SMD:C_0603_1608Metric",
            "value": "100nF", "sch": (93.98, 71.12), "pcb": (110.0, 102.2, 0),
            "pins": {"1": "BST", "2": "SW"},
        },
        {
            "ref": "C13", "sym": "Device:C", "fp": "Capacitor_SMD:C_0805_2012Metric",
            "value": "4.7uF", "sch": (40.64, 180.34), "pcb": (104.0, 122.8, 90),
            "pins": {"1": "VBUS", "2": "GND"},
        },
        {
            "ref": "R1", "sym": "Device:R", "fp": "Resistor_SMD:R_0603_1608Metric",
            "value": "10k", "sch": (160.02, 220.98), "pcb": (138.0, 106.2, 90),
            "pins": {"1": "BOOT0", "2": "GND"},
        },
        {
            "ref": "R3", "sym": "Device:R", "fp": "Resistor_SMD:R_0603_1608Metric",
            "value": "1k5", "sch": (281.94, 190.5), "pcb": (146.5, 124.0, 90),
            "pins": {"1": "USB_DP", "2": "+3V3"},
        },
        {
            "ref": "R4", "sym": "Device:R", "fp": "Resistor_SMD:R_0603_1608Metric",
            "value": "1k", "sch": (281.94, 106.68), "pcb": (148.0, 135.0, 0),
            "pins": {"1": "+3V3", "2": "LED_A"},
        },
        {
            # polarized part for CPL rotation coverage
            "ref": "D1", "sym": "Device:LED", "fp": "LED_SMD:LED_0805_2012Metric",
            "value": "LED_pwr", "sch": (299.72, 106.68), "pcb": (152.5, 135.0, 180),
            "pins": {"1": "GND", "2": "LED_A"},
        },
    ],
    "pcb": {
        "tracks": [
            # ---- left-column fan-out (translate of blinky2's proven set)
            # OSC_IN pin5 lane x=127.6, OSC_OUT pin6 lane x=128.1
            {"net": "OSC_IN", "layer": "F.Cu", "width": 0.25,
             "pts": [(131.338, 116.75), (127.6, 116.75), (127.6, 125.6),
                     (117.275, 125.6)]},
            {"net": "OSC_IN", "layer": "F.Cu", "width": 0.25,
             "pts": [(118.8, 125.6), (118.8, 128.35), (120.4, 128.35)]},
            {"net": "OSC_OUT", "layer": "F.Cu", "width": 0.25,
             "pts": [(131.338, 117.25), (128.1, 117.25), (128.1, 126.65),
                     (122.6, 126.65)]},
            {"net": "OSC_OUT", "layer": "F.Cu", "width": 0.25,
             "pts": [(125.9, 126.65), (125.9, 129.125)]},
            # NRST pin7 lane x=128.6 -> C9.1
            {"net": "NRST", "layer": "F.Cu", "width": 0.25,
             "pts": [(131.338, 117.75), (128.6, 117.75), (128.6, 129.775),
                     (131.0, 129.775)]},
            # VSSA pin8 -> via between the lanes and the pad column
            {"net": "GND", "layer": "F.Cu", "width": 0.25,
             "pts": [(131.338, 118.25), (129.3, 118.25), (129.3, 121.0)]},
            # VDDA pin9 via the x=130.0 channel -> C4.1
            {"net": "+3V3", "layer": "F.Cu", "width": 0.25,
             "pts": [(131.338, 118.75), (130.0, 118.75), (130.0, 125.275),
                     (130.8, 125.275)]},
            # C4 3V3 via stub
            {"net": "+3V3", "layer": "F.Cu", "width": 0.25,
             "pts": [(130.8, 125.275), (130.8, 126.4)]},
            # ---- VBAT(1), VDD_3(48), VDD_1(24), VDD_2(36) plane stubs
            {"net": "+3V3", "layer": "F.Cu", "width": 0.25,
             "pts": [(131.338, 114.75), (129.4, 114.75)]},
            {"net": "+3V3", "layer": "F.Cu", "width": 0.25,
             "pts": [(132.75, 113.3375), (132.75, 111.2)]},
            {"net": "+3V3", "layer": "F.Cu", "width": 0.25,
             "pts": [(138.25, 121.6625), (138.25, 123.9), (137.9, 123.9)]},
            {"net": "+3V3", "layer": "F.Cu", "width": 0.25,
             "pts": [(139.6625, 114.75), (141.4, 114.75), (141.4, 113.8)]},
            # decoupler 3V3 pads join their pin vias
            {"net": "+3V3", "layer": "F.Cu", "width": 0.25,
             "pts": [(131.225, 110.0), (131.225, 111.2), (132.75, 111.2)]},
            {"net": "+3V3", "layer": "F.Cu", "width": 0.25,
             "pts": [(139.725, 125.0), (139.725, 123.9), (137.9, 123.9)]},
            {"net": "+3V3", "layer": "F.Cu", "width": 0.25,
             "pts": [(142.425, 113.8), (141.4, 113.8)]},
            # ---- GND stubs to plane vias
            {"net": "GND", "layer": "F.Cu", "width": 0.25,   # VSS_3 pin 47
             "pts": [(133.25, 113.3375), (133.25, 112.25), (134.1, 112.25),
                     (134.1, 111.0)]},
            {"net": "GND", "layer": "F.Cu", "width": 0.25,   # VSS_1 pin 23
             "pts": [(137.75, 121.6625), (137.75, 123.0), (136.6, 123.0)]},
            {"net": "GND", "layer": "F.Cu", "width": 0.25,   # VSS_2 pin 35
             "pts": [(139.6625, 115.25), (142.3, 115.25), (142.3, 114.85)]},
            {"net": "GND", "layer": "F.Cu", "width": 0.25,
             "pts": [(132.775, 110.0), (134.0, 110.0)]},     # C1.2
            {"net": "GND", "layer": "F.Cu", "width": 0.25,
             "pts": [(141.275, 125.0), (141.275, 126.3)]},   # C2.2
            {"net": "GND", "layer": "F.Cu", "width": 0.25,
             "pts": [(143.975, 113.8), (144.9, 113.8)]},     # C3.2
            {"net": "GND", "layer": "F.Cu", "width": 0.25,
             "pts": [(130.8, 123.725), (130.8, 122.9)]},     # C4.2
            {"net": "GND", "layer": "F.Cu", "width": 0.25,
             "pts": [(138.0, 105.375), (138.0, 104.4)]},     # R1.2
            # ---- BOOT0 pin44 -> R1.1
            {"net": "BOOT0", "layer": "F.Cu", "width": 0.25,
             "pts": [(134.75, 113.3375), (134.75, 110.4), (138.0, 110.4),
                     (138.0, 107.025)]},
            # ---- SWD (translate of blinky2 shapes to J2 at x=156.5)
            {"net": "SWCLK", "layer": "F.Cu", "width": 0.25,
             "pts": [(138.25, 113.3375), (138.25, 111.9), (151.42, 111.9),
                     (151.42, 110.5)]},
            {"net": "SWDIO", "layer": "F.Cu", "width": 0.25,
             "pts": [(139.6625, 115.75), (153.96, 115.75), (153.96, 110.5)]},
            # ---- MCO: PA8(29) F.Cu -> via -> B.Cu corridor -> J3.1
            {"net": "MCO", "layer": "F.Cu", "width": 0.25,
             "pts": [(139.6625, 118.25), (141.0, 118.25), (141.0, 123.0)]},
            {"net": "MCO", "layer": "B.Cu", "width": 0.25,
             "pts": [(141.0, 123.0), (140.4, 123.0), (140.4, 138.6),
                     (146.0, 138.6), (146.0, 141.2)]},
            # ---- LED chain from 3V3 via R4
            {"net": "LED_A", "layer": "F.Cu", "width": 0.25,
             "pts": [(148.825, 135.0), (151.5625, 135.0)]},
            {"net": "GND", "layer": "F.Cu", "width": 0.25,
             "pts": [(153.4375, 135.0), (154.6, 135.0)]},    # D1.1 cathode

            # ---- USB differential pair (diffpair-skew mutant target):
            # DM inner lane x=142.75, DP outer lane x=143.4
            {"net": "USB_DM", "layer": "F.Cu", "width": 0.25,
             "pts": [(104.8, 130.85), (106.85, 130.85), (106.85, 134.5),
                     (142.75, 134.5), (142.75, 116.75), (139.8, 116.75)]},
            {"net": "USB_DP", "layer": "F.Cu", "width": 0.25,
             "pts": [(104.8, 131.5), (106.3, 131.5), (106.3, 135.15),
                     (143.4, 135.15), (143.4, 116.25), (139.8, 116.25)]},
            {"net": "USB_DP", "layer": "F.Cu", "width": 0.25,   # R3 pull-up tap
             "pts": [(143.4, 124.825), (146.5, 124.825)]},
            # R3.2 3V3 feed: north out of the GND strip to a plane via;
            # R4 (power LED) rides the same feed via R3.2
            {"net": "+3V3", "layer": "F.Cu", "width": 0.25,
             "pts": [(146.5, 123.175), (146.5, 120.4)]},
            {"net": "+3V3", "layer": "F.Cu", "width": 0.25,
             "pts": [(147.3, 135.0), (147.3, 123.175), (146.5, 123.175)]},
            # ---- VBUS: J1 -> island via; C13, C10 taps; C10 -> U2.IN + EN tie
            {"net": "VBUS", "layer": "F.Cu", "width": 0.5,
             "pts": [(104.8, 130.2), (106.2, 130.2)]},
            {"net": "VBUS", "layer": "F.Cu", "width": 0.5,
             "pts": [(104.0, 123.75), (104.0, 124.4)]},
            {"net": "VBUS", "layer": "F.Cu", "width": 0.5,
             "pts": [(104.8, 106.45), (104.8, 108.0)]},
            {"net": "VBUS", "layer": "F.Cu", "width": 0.5,
             "pts": [(104.8, 106.45), (108.2, 106.45), (108.862, 106.45)]},
            {"net": "VBUS", "layer": "F.Cu", "width": 0.5,
             "pts": [(108.2, 106.45), (108.2, 105.5), (108.862, 105.5)]},
            # ---- buck: SW node, BST cap, FB sense, 3V3 output
            {"net": "SW", "layer": "F.Cu", "width": 0.5,
             "pts": [(111.138, 105.5), (112.6, 105.5), (112.6, 102.2),
                     (111.0, 102.2)]},
            {"net": "SW", "layer": "F.Cu", "width": 0.8,
             "pts": [(112.6, 104.1), (117.9, 104.1)]},
            {"net": "BST", "layer": "F.Cu", "width": 0.25,
             "pts": [(109.225, 102.2), (109.225, 103.4), (111.138, 103.4),
                     (111.138, 104.55)]},
            {"net": "+3V3", "layer": "F.Cu", "width": 0.25,   # FB sense
             "pts": [(108.862, 104.55), (108.0, 104.55)]},
            {"net": "+3V3", "layer": "F.Cu", "width": 0.8,
             "pts": [(118.5, 106.9), (121.3, 106.9), (121.3, 106.45),
                     (124.0, 106.45), (125.5, 106.45)]},
            {"net": "+3V3", "layer": "F.Cu", "width": 0.8,
             "pts": [(121.3, 106.9), (121.3, 108.0)]},
            # ---- C5 bulk, misc GND stubs
            {"net": "+3V3", "layer": "F.Cu", "width": 0.25,
             "pts": [(145.4, 108.95), (145.4, 110.0)]},
            {"net": "GND", "layer": "F.Cu", "width": 0.25,
             "pts": [(145.4, 107.05), (145.4, 106.0)]},       # C5.2
            {"net": "GND", "layer": "F.Cu", "width": 0.25,
             "pts": [(104.8, 104.55), (104.8, 103.5)]},       # C10.2
            {"net": "GND", "layer": "F.Cu", "width": 0.25,
             "pts": [(124.0, 104.55), (124.0, 103.4)]},       # C11.2
            {"net": "GND", "layer": "F.Cu", "width": 0.25,
             "pts": [(104.0, 121.85), (104.0, 121.2)]},       # C13.2
            {"net": "GND", "layer": "F.Cu", "width": 0.25,
             "pts": [(111.138, 106.45), (111.138, 107.6)]},   # U2.4
            {"net": "GND", "layer": "F.Cu", "width": 0.25,
             "pts": [(104.8, 132.8), (104.8, 133.7)]},        # J1.5
            {"net": "GND", "layer": "F.Cu", "width": 0.25,   # J1 shield tabs
             "pts": [(102.1, 128.9), (102.1, 134.1)]},
            {"net": "GND", "layer": "F.Cu", "width": 0.25,
             "pts": [(122.6, 128.35), (122.6, 129.3)]},       # Y1.2
            {"net": "GND", "layer": "F.Cu", "width": 0.25,
             "pts": [(120.4, 126.65), (119.6, 126.65)]},      # Y1.4
            {"net": "GND", "layer": "F.Cu", "width": 0.25,
             "pts": [(115.725, 125.6), (114.9, 125.6)]},      # C7.2
            {"net": "GND", "layer": "F.Cu", "width": 0.25,
             "pts": [(125.9, 130.675), (125.9, 131.6)]},      # C8.2
            {"net": "GND", "layer": "F.Cu", "width": 0.25,
             "pts": [(131.0, 128.225), (131.0, 127.3), (131.2, 127.3)]},  # C9.2
        ],
        "vias": [
            # buck / USB power taps
            {"net": "VBUS", "at": (106.2, 130.2)},
            {"net": "VBUS", "at": (104.0, 124.4)},
            {"net": "VBUS", "at": (104.8, 108.0)},
            {"net": "+3V3", "at": (108.0, 104.55)},           # FB sense tap
            {"net": "+3V3", "at": (121.3, 108.0)},
            {"net": "+3V3", "at": (125.5, 106.45)},
            {"net": "+3V3", "at": (145.4, 110.0)},            # C5.1
            {"net": "GND", "at": (145.4, 106.0)},             # C5.2
            {"net": "GND", "at": (104.8, 103.5)},             # C10.2
            {"net": "GND", "at": (124.0, 103.4)},             # C11.2
            {"net": "GND", "at": (104.0, 121.2)},             # C13.2
            {"net": "GND", "at": (111.138, 107.6)},           # U2.4
            {"net": "GND", "at": (104.8, 133.7)},             # J1.5
            {"net": "GND", "at": (122.6, 129.3)},             # Y1.2
            {"net": "GND", "at": (119.6, 126.65)},            # Y1.4
            {"net": "GND", "at": (114.9, 125.6)},             # C7.2
            {"net": "GND", "at": (125.9, 131.6)},             # C8.2
            {"net": "GND", "at": (131.2, 127.3)},             # C9.2
            # 3V3 plane taps
            {"net": "+3V3", "at": (129.4, 114.75)},
            {"net": "+3V3", "at": (132.75, 111.2)},
            {"net": "+3V3", "at": (137.9, 123.9)},
            {"net": "+3V3", "at": (141.4, 113.8)},
            {"net": "+3V3", "at": (130.8, 126.4)},
            {"net": "+3V3", "at": (146.5, 120.4)},           # R3.2/R4 feed
            # GND plane taps
            {"net": "GND", "at": (129.3, 121.0)},            # VSSA
            {"net": "GND", "at": (134.1, 111.0)},            # pin 47
            {"net": "GND", "at": (136.6, 123.0)},            # pin 23
            {"net": "GND", "at": (142.3, 114.85)},           # pin 35
            {"net": "GND", "at": (134.0, 110.0)},            # C1.2
            {"net": "GND", "at": (141.275, 126.3)},          # C2.2
            {"net": "GND", "at": (144.9, 113.8)},            # C3.2
            {"net": "GND", "at": (130.8, 122.9)},            # C4.2
            {"net": "GND", "at": (138.0, 104.4)},            # R1.2
            {"net": "GND", "at": (154.6, 135.0)},            # D1 cathode
            # MCO layer-transition via + its GND return via
            # (the return via is the missing-return-via mutant target!)
            {"net": "MCO", "at": (141.0, 123.0)},
            {"net": "GND", "at": (141.9, 123.4)},
        ],
        "zones": [
            # In1: solid GND reference plane
            {"net": "GND", "layer": "In1.Cu",
             "rect": (100.6, 100.6, 159.4, 144.4), "min_thickness": 0.25},
            # In2: 3V3 power plane with a VBUS island (left strip) and a
            # GND strip under the MCO B.Cu corridor
            {"net": "+3V3", "layer": "In2.Cu",
             "rect": (100.6, 100.6, 159.4, 144.4), "min_thickness": 0.25,
             "priority": 0},
            {"net": "VBUS", "layer": "In2.Cu",
             "rect": (100.6, 100.6, 106.8, 144.4), "min_thickness": 0.25,
             "priority": 1},
            {"net": "GND", "layer": "In2.Cu",
             "rect": (138.5, 121.0, 153.0, 144.4), "min_thickness": 0.25,
             "priority": 1},
            # B.Cu: GND fill
            {"net": "GND", "layer": "B.Cu",
             "rect": (100.6, 100.6, 159.4, 144.4), "min_thickness": 0.25},
        ],
        "silk": [
            {"text": "usbbuck4", "at": (110.0, 142.5), "layer": "F.SilkS"},
        ],
    },
}
