"""hwde-led-test root (and only) sheet: 5 V header input, bulk cap, one
green LED with a series resistor.

Nets (architecture/sheets.md): +5V (J1.1, C1.1, R1.1), GND (J1.2, C1.2,
D1.K), /LED_A (R1.2 - D1.A).
Pin tables from `schlib.py --pins` on lib/aiee.kicad_sym (no IC, so no
datasheet-extract JSONs): J1/C1/R1 pads 1,2; D1 pad 1 = K, pad 2 = A.
R1 = 470R (P3 decision: Vf 3.1 V, 680R lead value gave < 3 mA).
C1 is the input bulk cap at J1, not an IC decoupler: the decoupling metadata
records it against J1.1, the +5V rail's source.

Rebuild:
  eval "$(/work/bin/hwde env)"
  PYTHONPATH=$HWDE_SKILL/scripts:$HWDE_SKILL/scripts/lib $HWDE_PY kicad/gen/root.py
Outputs: ../hwde-led-test.kicad_sch + .kicad_pro + ../decoupling.json
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
BOARD = HERE.parents[2]

import schlib  # noqa: E402

# kicad-sch-api's symbol cache never reads the project sym-lib-table:
# register the pulled library explicitly.
with contextlib.redirect_stdout(io.StringIO()), \
        contextlib.redirect_stderr(io.StringIO()):
    schlib.ksa.get_symbol_cache().add_library_path(
        str(BOARD / "lib" / "aiee.kicad_sym"))

NAME = "hwde-led-test"


def build() -> schlib.Sheet:
    sh = schlib.Sheet(NAME, title="hwde-led-test: 5 V LED indicator",
                      pwr_base=100)

    # B1: input header, pin 1 = +5V, pin 2 = GND
    sh.add_component("aiee:PZ254V-11-02P", "J1", "PZ254V-11-02P",
                     at=(50.8, 76.2), footprint="aiee:HDR-TH_2P-P2.54-V-M",
                     expect={"1": "1", "2": "2"})
    sh.wire_pins("J1", {"1": "+5V", "2": "GND"})
    sh.power_symbol_at_pin("J1", "1", "power:+5V")
    sh.power_symbol_at_pin("J1", "2", "power:GND")

    # B2: input bulk cap across the header
    sh.add_component("aiee:CL10A475KO8NNNC", "C1", "4.7uF",
                     at=(76.2, 76.2), footprint="aiee:C0603",
                     expect={"1": "1", "2": "2"})
    sh.wire_pins("C1", {"1": "+5V", "2": "GND"})
    sh.power_symbol_at_pin("C1", "1", "power:+5V")
    sh.power_symbol_at_pin("C1", "2", "power:GND")

    # B3: series resistor + LED (K = pad 1, A = pad 2)
    sh.add_component("aiee:0603WAF4700T5E", "R1", "470R",
                     at=(101.6, 76.2), footprint="aiee:R0603",
                     expect={"1": "1", "2": "2"})
    sh.wire_pins("R1", {"1": "+5V", "2": "LED_A"})
    sh.power_symbol_at_pin("R1", "1", "power:+5V")
    sh.add_component("aiee:0603Green509-620mcd", "D1", "KT-0603G",
                     at=(127.0, 76.2), footprint="aiee:LED0603-RD",
                     expect={"1": "K", "2": "A"})
    sh.wire_pins("D1", {"1": "GND", "2": "LED_A"})
    sh.power_symbol_at_pin("D1", "1", "power:GND")

    # Rails enter on a passive header pin: PWR_FLAG marks them driven.
    sh.power_flag("+5V", at=(50.8, 91.44), sym=None, flag=True)
    sh.power_flag("GND", at=(50.8, 99.06), sym=None, flag=True)
    return sh


def main(argv=None) -> int:
    out_dir = Path(argv[0]) if argv else HERE.parents[1]  # .../kicad
    try:
        sh = build()
        sch = sh.save(out_dir)
        # C1 is the +5V rail's bulk reservoir at the input header: record it
        # against J1.1 (the rail's source) so check_pdn sees the rail held up.
        sh.emit_decoupling(out_dir / "decoupling.json", extra=[
            {"cap": "C1", "ic": "J1", "pin": "1", "rail": "+5V",
             "value": "4.7uF"}])
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"script": "gen.root", "status": "error",
                          "error": f"{type(exc).__name__}: {exc}"}))
        return 2
    print(json.dumps({"script": "gen.root", "status": "pass",
                      "root": str(sch),
                      "decoupling_associations": len(sh.decoupling)},
                     indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
