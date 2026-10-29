"""Generator for hierdemo: a 3-sheet hierarchical design proving the SPEC
P4 multi-sheet pattern end to end - one generator per sheet (this file +
power_sheet.py + load_sheet.py), root stitches with schlib.Project.

Topology: J1 5V input -> power sheet (VIN hier pin) -> AMS1117 -> +3V3
(global power symbol, consumed by the load sheet with NO sheet pin) ->
LED chain -> CTL hier pin -> J2. GND is global everywhere.

Rebuild:  .venv/Scripts/python tests/s7_regen/hierdemo/kicad/gen/root.py
Outputs:  ../hierdemo.kicad_sch + power/load sheets + .kicad_pro +
          ../decoupling.json (merged from all sheets).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[5]
sys.path.insert(0, str(REPO / ".claude" / "skills" / "hwde" / "scripts"))
sys.path.insert(0, str(HERE.parent))  # sibling sheet generators

import schlib  # noqa: E402
import load_sheet  # noqa: E402
import power_sheet  # noqa: E402


def build() -> schlib.Project:
    root = schlib.Sheet("hierdemo", title="hierdemo: root", paper="A4")
    root.add_component("Connector_Generic:Conn_01x02", "J1", "PWR_IN",
                       at=(40.64, 60.96),
                       footprint="Connector_PinHeader_2.54mm:"
                                 "PinHeader_1x02_P2.54mm_Vertical")
    root.wire_pins("J1", {"1": "VIN", "2": "GND"})
    root.add_component("Connector_Generic:Conn_01x02", "J2", "CTL_OUT",
                       at=(40.64, 86.36),
                       footprint="Connector_PinHeader_2.54mm:"
                                 "PinHeader_1x02_P2.54mm_Vertical")
    root.wire_pins("J2", {"1": "CTL", "2": "GND"})
    # VIN comes in on a connector (passive pins): PWR_FLAG marks it driven.
    root.power_flag("VIN", at=(40.64, 111.76), sym=None, flag=True)
    root.power_flag("GND", at=(40.64, 121.92), sym="power:GND", flag=True)

    proj = schlib.Project(root)
    proj.add_sheet(power_sheet.build(), at=(101.6, 55.88),
                   size=(30.48, 20.32), nets=["VIN"])
    proj.add_sheet(load_sheet.build(), at=(101.6, 86.36),
                   size=(30.48, 20.32), nets=["CTL"])
    return proj


def main(argv=None) -> int:
    out_dir = Path(argv[0]) if argv else HERE.parents[1]  # .../kicad
    try:
        proj = build()
        sch = proj.save(out_dir, decoupling=out_dir / "decoupling.json")
    except Exception as exc:  # noqa: BLE001  (SPEC 6: any error -> exit 2)
        print(json.dumps({"script": "gen.hierdemo", "status": "error",
                          "error": f"{type(exc).__name__}: {exc}"}))
        return 2
    print(json.dumps({
        "script": "gen.hierdemo", "status": "pass",
        "files": sorted(str(p) for p in out_dir.glob("*.kicad_sch")),
        "root": str(sch),
        "decoupling_associations": len(proj.decoupling),
    }, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
