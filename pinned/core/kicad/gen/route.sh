#!/bin/sh
# core: P7 chain on a placed board. Run from boards/core.
H="sh $HIVE_MODULES/hive-hwde/bin/hwde"
export HWDE_KRT_DIR=/hive/tools/kicad-routing-tools/KiCadRoutingTools-0.19.0/plugins
python3 kicad/gen/pro_vias.py
python3 kicad/gen/planes.py
$H planes_gen --pcb kicad/core.kicad_pcb --out-report reports/planes_gen.json >/dev/null 2>&1; echo "planes_gen $?"
python3 kicad/gen/power_zones.py
$H planes_gen --pcb kicad/core.kicad_pcb --constraints kicad/power_zones.constraints.json --no-thermal-vias --out-report reports/planes_power.json >/dev/null 2>&1; echo "power zones $?"
~/.cache/hwde/bin/python3 kicad/gen/pre_route.py 2>/dev/null | tail -1
$H planes_gen --pcb kicad/core.kicad_pcb --out-report reports/planes_refill.json >/dev/null 2>&1; echo "refill $?"
$H stitch_vias --pcb kicad/core.kicad_pcb --nets "PVDD,VBUS,VBUS_IN,+3V3,GND" --out-report reports/stitch_vias.json >/dev/null 2>&1; echo "stitch $?"
cp kicad/core.kicad_pcb ~/work/core_prerouted.kicad_pcb
$H route_auto --pcb kicad/core.kicad_pcb --out-report reports/route_auto.json > ~/work/ra.out 2>&1; echo "route_auto $?"
cp kicad/core.kicad_pcb ~/work/core_routed.kicad_pcb
