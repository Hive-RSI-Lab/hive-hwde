# Real findings for the gate leg of bin/strength.py

erc and drc_routed run KiCad, which the planted faults don't (they run
offline, in seconds). So their findings are kept here: each file is a
report `kc.py` wrote, holding one finding of each (source, check, severity)
picked from real runs. bin/strength.py puts each one, alone, in a report
with this file's own header and checks that the gate fails it. Nothing in
them says they were planted: they are what KiCad said.

erc.json, from `hwde kc erc` (KiCad 10.0.6) on two copies of
tests/golden/blinky2/blinky2.kicad_sch:
- `python erc_mutate.py <sch> <out> wire`: three wires taken out
  (pin_not_connected, power_pin_not_driven, label_dangling);
- `python erc_mutate.py <sch> <out> flag`: both PWR_FLAGs taken out
  (power_pin_not_driven, unconnected_wire_endpoint).
The header is the wire run's.

drc.json, from `hwde kc drc --parity --all-track-errors` (KiCad 10.0.6):
- blinky2 with one track segment and R2's footprint taken out
  (`python drc_mutate.py <pcb> <out>`): unconnected_items, track_dangling,
  missing_footprint. The header is this run's.
- tests/golden/mutants/silk-over-pad: silk_overlap, silk_over_copper.
- pinned/core/reports/route_auto.json (hifi-receiver-2's core board, a
  routing pass's own DRC): hole_clearance, copper_edge_clearance,
  hole_to_hole, silk_edge_clearance, extra_footprint.

A finding added here is a new planted fault the gate must fail; the
directory is in the score's paths (tests/golden/).
