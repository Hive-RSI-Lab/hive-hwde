#!/bin/sh
# core: schematic -> placed board, deterministic (P4 netlist .. P6 place gate).
# Run from the board workspace (boards/core).
set -e
H="sh $HIVE_MODULES/hive-hwde/bin/hwde"
$H kc netlist kicad/core.kicad_sch --out kicad/core.net >/dev/null
$H board_init --netlist kicad/core.net --name core --out kicad --layers 4 \
  --outline 120x100 --mounting-holes 4 --mounting-hole-fp MountingHole:MountingHole_3.2mm_M3_Pad \
  --schematic kicad/core.kicad_sch \
  --fp-lib "$PWD/lib" --out-report reports/board_init.json >/dev/null 2>&1 || true
$H rules_gen --constraints kicad/constraints.json --layers 4 \
  --out-dru kicad/core.kicad_dru --pro kicad/core.kicad_pro \
  --out reports/rules_gen.json >/dev/null
cat kicad/gen/extra_rules.dru >>kicad/core.kicad_dru
python3 kicad/gen/pro_vias.py >/dev/null
cp kicad/core.kicad_pcb /tmp/core_anchored.kicad_pcb
~/.cache/hwde/bin/python3 kicad/gen/satellites.py
$H place_edit --pcb kicad/core.kicad_pcb --ops anneal/manual.ops.json >/dev/null
$H place_edit --pcb kicad/core.kicad_pcb --ops anneal/satellites.ops.json >/dev/null
$H gate --gate place kicad/core.kicad_pcb --workspace "$PWD" --out reports/place.json >/dev/null 2>&1 && echo "place: pass" || echo "place: FAIL"
