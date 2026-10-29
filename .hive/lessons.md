# Lessons

What was tried and why it didn't stay. Read before working here.

- 2026-10-29: "hwde's score counts workarounds and hand edits on the hi-fi product boards, not only hwde-led-test" (PR #126, aa1ec482) was reverted: the tests went red after it merged: probe pwr-clearance: stands (0s) stands: Pwr_1p8mm at 0.2 mm for a 5 V rail (floor 0.127 mm) | probe render-3d-loaders: stands (0s) stands: no KiCad 3D model loaders (libs3d_plugin_*) under any usr/lib/*/kicad/plugins/3d|usr/lib*/kicad/plugins/3d|usr/local/lib/*/kicad/plugins/3d|usr/local/lib*/kicad | probe sch-uuids: fixed (0s) fixed: root.py reproduces every UUID of the committed schematic | probe symdir: stands (0s) stands: kicad-sch-api finds no power:GND, Device:R in (none) | probe via-hole-floor: stands (0s) stands: vias packed by clearance alone break the 0.5 mm hole-to-hole floor: Def…
