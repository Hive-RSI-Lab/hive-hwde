"""hierdemo load sheet: LED chain off the global +3V3 rail, switched
through the CTL hier pin. Demonstrates: hier_pin ref/pad variant (the
hierarchical label rides the pin stub), consuming a rail another sheet
drives (no sheet pin for +3V3)."""
from __future__ import annotations

import schlib


def build() -> schlib.Sheet:
    sh = schlib.Sheet("load", title="hierdemo: load", paper="A4",
                      pwr_base=40)
    sh.add_component("Device:R", "R3", "1k", at=(101.6, 60.96),
                     footprint="Resistor_SMD:R_0603_1608Metric")
    sh.wire_pins("R3", {"1": "+3V3", "2": "LED_K"})
    sh.power_symbol_at_pin("R3", "1", "power:+3V3")
    sh.add_component("Device:LED", "D2", "LED_green", at=(121.92, 71.12),
                     footprint="LED_SMD:LED_0805_2012Metric")
    sh.wire_pin("D2", "2", "LED_K")           # anode from the resistor
    sh.hier_pin("CTL", shape="passive", ref="D2", pad="1")  # cathode out
    return sh
