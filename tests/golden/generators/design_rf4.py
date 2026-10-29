"""Golden board 3: rf4 - 4-layer sub-GHz RF front end (45 x 35 mm).

RFM95W-868S2 LoRa module + pi matching network (C-L-C) + 50-ohm feed
trace + edge-mount SMA + SPI header. Stackup: F.Cu / In1.Cu (solid GND)
/ In2.Cu (3V3) / B.Cu (GND).

Verification-relevant features baked in for the S1 mutants:
  - RF_FEED: 0.35 mm 50-ohm trace at y=122 from the pi network to the SMA
    over solid In1 GND -> plane-split-under-clock mutant target (the RF
    feed is this board's "clock": the mutant slots In1 under it)
  - dense via population (stitch fence flanking the feed, perimeter ring,
    under-module field, ~80 vias) -> S4 performance stress case (<30 s)
  - F.Cu coplanar GND pour around the feed with fence vias
  - pi match C14 (shunt) - L2 (series) - C15 (shunt): passive matching
    network parts for datasheet/BOM-class checks

Layout notes:
  - U1 castellated pads are 2.95 mm wide: signal lanes west of the module
    run at x = 101.25..103.60 (0.47 pitch), river-routed to the bottom
    header with no crossings (lane order = row order = target order)
  - SMA pads are 5.08 mm long in X; the connector sits at x=141.9 so pad
    copper stays >=0.5 mm from the board edge (golden corpus convention)
"""

# perimeter ring, fence, and module field vias (all GND)
_STITCH = (
    # fence flanking the RF feed (two rows, 2 mm pitch)
    [(x / 10.0, 120.6) for x in range(1292, 1413, 20)]
    + [(x / 10.0, 123.4) for x in range(1292, 1413, 20)]
    # top perimeter
    + [(x / 10.0, 101.4) for x in range(1020, 1441, 30)]
    # bottom perimeter
    + [(x / 10.0, 133.6) for x in range(1020, 1441, 30)]
    # left perimeter (gap where the signal lanes run)
    + [(101.4, y) for y in (104.4, 107.4, 129.6, 132.6)]
    # right perimeter above the SMA
    + [(143.6, y) for y in (104.4, 107.4, 110.4, 113.4)]
    # under-module field (In1 <-> B.Cu)
    + [(x, y) for x in (109.0, 113.0, 117.0) for y in (111.0, 115.0, 119.0)]
)

DESIGN = {
    "name": "rf4",
    "title": "Golden 3: 4-layer sub-GHz RF front end",
    "layers": 4,
    "outline": (100.0, 100.0, 145.0, 135.0),
    "sch": {
        "paper": "A3",
        "rails": [
            {"net": "GND", "sym": "power:GND", "pos": (40.64, 254.0),
             "flag": True},
            {"net": "+3V3", "sym": "power:+3V3", "pos": (40.64, 259.08),
             "flag": True},
        ],
    },
    "components": [
        {
            "ref": "U1", "sym": "RF_Module:RFM95W-868S2",
            "fp": "RF_Module:HOPERF_RFM9XW_SMD", "value": "RFM95W-868S2",
            "sch": (152.4, 127.0), "pcb": (113.0, 115.0, 0),
            "expect": {"9": "ANT", "13": "3.3", "2": "MISO", "6": "RESET"},
            "pins": {
                "1": "GND", "2": "MISO", "3": "MOSI", "4": "SCK",
                "5": "NSS", "6": "RESET", "7": "DIO5", "8": "GND",
                "9": "RF1", "10": "GND", "11": "NC", "12": "NC",
                "13": "+3V3", "14": "NC", "15": "NC", "16": "NC",
            },
        },
        {
            "ref": "J1", "sym": "Connector:Conn_Coaxial",
            "fp": "Connector_Coaxial:SMA_Amphenol_132289_EdgeMount",
            "value": "SMA", "sch": (254.0, 127.0), "pcb": (141.9, 122.0, 0),
            "pins": {"1": "RF_FEED", "2": "GND"},
        },
        {
            "ref": "J2", "sym": "Connector_Generic:Conn_01x08",
            "fp": "Connector_PinHeader_2.54mm:PinHeader_1x08_P2.54mm_Vertical",
            "value": "SPI", "sch": (76.2, 127.0), "pcb": (104.5, 132.0, 90),
            "ref_at": (0.0, -2.6),
            "pins": {"1": "+3V3", "2": "GND", "3": "MISO", "4": "MOSI",
                     "5": "SCK", "6": "NSS", "7": "RESET", "8": "DIO5"},
        },
        {
            "ref": "L2", "sym": "Device:L", "fp": "Inductor_SMD:L_0603_1608Metric",
            "value": "6.8nH", "sch": (215.9, 127.0), "pcb": (125.4, 122.0, 0),
            "pins": {"1": "RF1", "2": "RF_FEED"},
        },
        {
            "ref": "C14", "sym": "Device:C", "fp": "Capacitor_SMD:C_0603_1608Metric",
            "value": "5.6pF", "sch": (203.2, 137.16), "pcb": (123.7, 119.3, 90),
            "ref_at": (3.2, -6.5),
            "pins": {"1": "RF1", "2": "GND"},
        },
        {
            "ref": "C15", "sym": "Device:C", "fp": "Capacitor_SMD:C_0603_1608Metric",
            "value": "8.2pF", "sch": (228.6, 137.16), "pcb": (127.9, 124.2, 270),
            "pins": {"1": "RF_FEED", "2": "GND"},
        },
        {
            "ref": "C1", "sym": "Device:C", "fp": "Capacitor_SMD:C_0603_1608Metric",
            "value": "100nF", "sch": (101.6, 127.0), "pcb": (124.3, 114.0, 0),
            "ref_at": (0.0, -2.4),
            "pins": {"1": "+3V3", "2": "GND"},
        },
        {
            "ref": "C2", "sym": "Device:C", "fp": "Capacitor_SMD:C_0805_2012Metric",
            "value": "10uF", "sch": (114.3, 127.0), "pcb": (125.0, 116.2, 0),
            "ref_at": (3.6, 0.0),
            "pins": {"1": "+3V3", "2": "GND"},
        },
    ],
    "pcb": {
        "tracks": [
            # ---- RF chain: ANT -> RF1 -> L2 -> RF_FEED -> SMA (0.35 mm)
            {"net": "RF1", "layer": "F.Cu", "width": 0.35,
             "pts": [(120.525, 122.0), (124.625, 122.0)]},
            {"net": "RF1", "layer": "F.Cu", "width": 0.35,
             "pts": [(123.7, 122.0), (123.7, 120.075)]},      # C14 tap
            {"net": "RF_FEED", "layer": "F.Cu", "width": 0.35,
             "pts": [(126.175, 122.0), (141.9, 122.0)]},
            {"net": "RF_FEED", "layer": "F.Cu", "width": 0.35,
             "pts": [(127.9, 122.0), (127.9, 123.425)]},      # C15 tap
            # ---- shunt cap grounds into the coplanar pour + explicit vias
            {"net": "GND", "layer": "F.Cu", "width": 0.35,
             "pts": [(123.7, 118.525), (123.7, 117.75)]},
            {"net": "GND", "layer": "F.Cu", "width": 0.35,
             "pts": [(127.9, 124.975), (127.9, 125.75)]},
            # ---- 3V3: pin13 -> C1.1 -> plane via; C2 bulk rides the branch
            {"net": "+3V3", "layer": "F.Cu", "width": 0.4,
             "pts": [(120.525, 114.0), (122.36, 114.0), (123.525, 114.0)]},
            {"net": "+3V3", "layer": "F.Cu", "width": 0.25,
             "pts": [(122.36, 114.0), (122.36, 116.2), (124.05, 116.2)]},
            # ---- module GND pins to plane vias
            {"net": "GND", "layer": "F.Cu", "width": 0.4,
             "pts": [(105.475, 108.0), (103.9, 108.0)]},      # pin 1
            {"net": "GND", "layer": "F.Cu", "width": 0.4,
             "pts": [(105.475, 122.0), (104.3, 122.0), (104.3, 123.4)]},  # pin 8
            {"net": "GND", "layer": "F.Cu", "width": 0.4,
             "pts": [(120.525, 120.0), (122.36, 120.0)]},     # pin 10
            # ---- decoupler grounds
            {"net": "GND", "layer": "F.Cu", "width": 0.25,
             "pts": [(125.075, 114.0), (125.9, 114.0)]},      # C1.2
            {"net": "GND", "layer": "F.Cu", "width": 0.25,
             "pts": [(125.95, 116.2), (126.75, 116.2)]},      # C2.2
            # ---- SPI river: west lanes -> bottom corridor -> header
            # (0.47 pitch: 0.2 clearance exactly-at-limit is a DRC coin-flip)
            {"net": "MISO", "layer": "F.Cu", "width": 0.25,
             "pts": [(105.475, 110.0), (101.25, 110.0), (101.25, 126.75),
                     (109.58, 126.75), (109.58, 132.0)]},
            {"net": "MOSI", "layer": "F.Cu", "width": 0.25,
             "pts": [(105.475, 112.0), (101.72, 112.0), (101.72, 126.28),
                     (112.12, 126.28), (112.12, 132.0)]},
            {"net": "SCK", "layer": "F.Cu", "width": 0.25,
             "pts": [(105.475, 114.0), (102.19, 114.0), (102.19, 125.81),
                     (114.66, 125.81), (114.66, 132.0)]},
            {"net": "NSS", "layer": "F.Cu", "width": 0.25,
             "pts": [(105.475, 116.0), (102.66, 116.0), (102.66, 125.34),
                     (117.2, 125.34), (117.2, 132.0)]},
            {"net": "RESET", "layer": "F.Cu", "width": 0.25,
             "pts": [(105.475, 118.0), (103.13, 118.0), (103.13, 124.87),
                     (119.74, 124.87), (119.74, 132.0)]},
            {"net": "DIO5", "layer": "F.Cu", "width": 0.25,
             "pts": [(105.475, 120.0), (103.6, 120.0), (103.6, 124.4),
                     (122.28, 124.4), (122.28, 132.0)]},
            # ---- header power stubs
            {"net": "+3V3", "layer": "F.Cu", "width": 0.4,
             "pts": [(104.5, 132.0), (104.5, 130.6)]},        # J2.1
            {"net": "GND", "layer": "F.Cu", "width": 0.4,
             "pts": [(107.04, 132.0), (107.04, 130.6)]},      # J2.2
        ],
        "vias": [
            # 3V3 plane taps
            {"net": "+3V3", "at": (122.36, 114.0)},
            {"net": "+3V3", "at": (104.5, 130.6)},
            # GND plane taps (stubs)
            {"net": "GND", "at": (103.9, 108.0)},             # pin 1
            {"net": "GND", "at": (104.3, 123.4)},             # pin 8
            {"net": "GND", "at": (122.36, 120.0)},            # pin 10
            {"net": "GND", "at": (125.9, 114.0)},             # C1.2
            {"net": "GND", "at": (126.75, 116.2)},            # C2.2
            {"net": "GND", "at": (123.7, 117.75)},            # C14.2
            {"net": "GND", "at": (127.9, 125.75)},            # C15.2
            {"net": "GND", "at": (107.04, 130.6)},            # J2.2
            # SMA ground pads: stitch through the pads themselves
            {"net": "GND", "at": (140.5, 117.75)},
            {"net": "GND", "at": (143.3, 117.75)},
            {"net": "GND", "at": (140.5, 126.25)},
            {"net": "GND", "at": (143.3, 126.25)},
        ] + [{"net": "GND", "at": xy} for xy in _STITCH],
        "zones": [
            # In1: THE solid GND reference (plane-split mutant slots this)
            {"net": "GND", "layer": "In1.Cu",
             "rect": (100.6, 100.6, 144.4, 134.4), "min_thickness": 0.25},
            # In2: 3V3 plane
            {"net": "+3V3", "layer": "In2.Cu",
             "rect": (100.6, 100.6, 144.4, 134.4), "min_thickness": 0.25},
            # B.Cu: GND
            {"net": "GND", "layer": "B.Cu",
             "rect": (100.6, 100.6, 144.4, 134.4), "min_thickness": 0.25},
            # F.Cu: coplanar ground around the feed + SMA launch
            {"net": "GND", "layer": "F.Cu",
             "rect": (122.8, 118.0, 144.4, 130.0), "min_thickness": 0.25,
             "clearance": 0.3},
        ],
        "silk": [
            {"text": "rf4", "at": (135.5, 132.5), "layer": "F.SilkS"},
        ],
    },
}
