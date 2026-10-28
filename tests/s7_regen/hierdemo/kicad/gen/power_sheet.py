"""hierdemo power sheet: AMS1117-3.3 LDO, VIN in (hier), +3V3 out (global
power symbol). Demonstrates: place_ic_with_decoupling on a child sheet,
the hier_pin `at` variant, power symbols making rails global (GND/+3V3
need NO sheet pin)."""
from __future__ import annotations

import schlib

C0805 = "Capacitor_SMD:C_0805_2012Metric"


def build() -> schlib.Sheet:
    sh = schlib.Sheet("power", title="hierdemo: power", paper="A4",
                      pwr_base=20)
    sh.place_ic_with_decoupling(
        "U3", "Regulator_Linear:AMS1117-3.3", "AMS1117-3.3",
        at=(81.28, 60.96), pins={"1": "GND", "2": "+3V3", "3": "VIN"},
        footprint="Package_TO_SOT_SMD:SOT-223-3_TabPin2",
        expect={"1": "GND", "2": "VO", "3": "VI"},
        decoupling=[
            # "VIN" is this sheet's wiring label; the net crosses the sheet
            # pin and takes the root's name, so the metadata records /VIN.
            {"cap": "C10", "pin": "3", "rail": "VIN", "rail_net": "/VIN",
             "value": "10uF", "footprint": C0805},
            {"cap": "C11", "pin": "2", "rail": "+3V3", "value": "10uF",
             "footprint": C0805},
        ],
        caps_at=(60.96, 88.9), caps_dx=20.32)
    # rails global via power symbols hung off the IC pin stubs
    sh.power_symbol_at_pin("U3", "1", "power:GND")
    sh.power_symbol_at_pin("U3", "2", "power:+3V3")
    # VIN leaves this sheet: hier cluster in free area, binds by local label
    sh.hier_pin("VIN", shape="input", at=(40.64, 60.96))
    return sh
