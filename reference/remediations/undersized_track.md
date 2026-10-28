# undersized_track

One routed track SEGMENT is narrower than the IPC-2152 minimum for its net's budgeted
current. No branch attribution: every segment is tested against the FULL rail budget
unless an `overrides` region covers its midpoint (check_current.py:5-7, :101-106).

- Emitted by: scripts/check_current.py (kind="undersized_track", :161-176)   Gate: verify (P8; verify_all.py:45, gates.yaml:80)
- Fixer domain: router (cluster_violations.py:42)   Scripts you may use: route_edit.py, kc.py, render.py, stitch_vias.py, route_cleanup.py (fix_dispatch.py:46-48)
- Fields on the violation: pos [x,y] = segment midpoint, layer, net, refs (ALWAYS empty here), items[].pos only - NO items[].uuid, so find the item by matching `segment.start`/`segment.end` in the board text; plus width_mm, required_mm, current_a (the current applied to THIS segment: override or budget).

## Is it real?
- Wrong input, not wrong board: two constraints.json copies exist (architecture/ and the
  board dir; the board dir is canonical from P5). A stale current_a gave 61 vs 53
  undersized_track on lumina-carrier, with no warning (LEARNINGS 2026-07-29 line 1929).
  Compare the report's `checked[].current_a` to the board-dir file before touching copper.
- Assumed stackup: with no `(stackup)` block geom assumes 0.035 mm outer / 0.0152 mm inner
  (geom.py:74-75, :408) and the report sets `stackup_assumed: true`. required = area/cu_mm
  (check_current.py:73-75), so inner requirements run ~2.3x outer, and a 2 oz-outer profile
  (jlc_capabilities.yaml `2layer_2oz`) is modelled as 1 oz -> ~2x overstated. Fix the input.
- "There is a parallel path" is UNPROVABLE by the script: it has no connectivity graph and
  cannot tell a bridge (cut edge, carries the whole rail) from one of four parallel feeds.
  All five segments investigated on lumina-carrier were bridges - real defects - and that
  had to be proved by hand (LEARNINGS 2026-07-29 line 1902). Never waive without tracing.

## Fix ladder (cheapest first)
1. Confirm the input: `checked[].current_a`, `dt_c`, `required_mm_by_layer`,
   plus top-level `stackup_assumed`, vs the board-directory constraints.json. Mismatch -> step 5.
2. Widen in place: locate `segment.start`/`segment.end` in the .kicad_pcb text, take that
   item's `(uuid ...)`, then ONE route_edit op list
   `{"version":1,"ops":[{"op":"remove","uuid":U},{"op":"add_track","start":..,"end":..,"width":..,"layer":..,"net":..}]}`
   (route_edit.py:11-14). Width = max(required_mm, same-net copper abutting both endpoints)
   (fix_dispatch.py:56-57). Widen the whole run - one short narrow neighbour just moves it.
3. Edit crossed a zone fill -> refill before re-gating: `kc.run_drc(refill=True)` (fix_dispatch.py:58-60).
4. Fan-in that cannot hold the width: on sibling board pd-trigger the widest legal VBUS
   track reaching the USB-C pad was 1.465 mm against a 1.75 mm requirement - geometrically
   unmeetable (LEARNINGS 2026-07-28 line 782). Do not neck it; escalate for a ZONE there
   (plane domain - a zone is not a track).
5. Escalate, naming which it is: (a) stale/duplicate constraints.json, (b) missing stackup
   block or wrong copper weight, (c) a graph-PROVEN parallel path needing an `overrides`
   entry {near:[x,y], radius_mm, current_a} (check_current.py:29-31; constraints are not a
   router file), (d) a fan-in needing a pour. Include your measured widths.

## Do not
- Do not use the .kicad_pro floor as a width: it is a fab MINIMUM, never a design width.
  (Boards initialized before T1 are worse than that - board_init shipped min_track_width
  0.1 mm, below EVERY JLC profile (4L 1oz 0.1016, 2L 0.127, 2oz 0.1524), which is how 189
  dfm errors survived drc_routed 0/0: LEARNINGS 2026-07-29 line 1939, 2026-07-30 line 2011.
  T1 sources those floors from the fab profile at ERROR severity - on ANY board, compare the
  .kicad_pro floor to reference/jlc_capabilities.yaml yourself before believing it.)
- Do not size off the DRU/netclass: a board generated before T1's netclass split carries every
  power net in ONE `Power` class at the widest width, so 20 mA nets carried a 5 A width
  (LEARNINGS 2026-07-28 line 798). Size from the segment's own required width.
- Do not add an `overrides` region to silence an untraced segment; overrides feed ONLY the
  track-width test, never vias or pour necks (LEARNINGS 2026-07-29 line 1588).
- Do not run route_cleanup after adding a parallel widening path: its LOOPS pass deletes
  the longest segment of any track-only cycle (route_cleanup.py:15-19).
- Never raw-edit the .kicad_pcb; route_edit is the only routing writer (route_edit.py:3-5).

## Verify
```
.venv\Scripts\python.exe .claude\skills\hwde\scripts\check_current.py --pcb <ws>\kicad\<board>.kicad_pcb --constraints <ws>\kicad\constraints.json
.venv\Scripts\python.exe .claude\skills\hwde\scripts\gate.py --gate verify <ws>\kicad\<board>.kicad_pcb
.venv\Scripts\python.exe .claude\skills\hwde\scripts\gate.py --gate drc_routed <ws>\kicad\<board>.kicad_pcb
```

## Sources
- LEARNINGS 2026-07-29 [check_current][gates] no bridge awareness (line 1902)
- LEARNINGS 2026-07-29 [constraints][gates] two constraints.json, 61 vs 53 (line 1929)
- LEARNINGS 2026-07-28 [routing][kicad][drc] net-wide track_width floor unmeetable (line 782)
- LEARNINGS 2026-07-28 [routing][rules_gen][freerouting] one Power netclass (line 798)
- LEARNINGS 2026-07-29 [check_current][gates] overrides feed only the width test (line 1588)
- LEARNINGS min_track_width 0.1: 2026-07-29 [board_init][rules_gen][dfm][gates] (1939), 2026-07-30 [gates][dfm] (2011)
- check_current.py:5,29,51,71,101,161; lib/geom.py:74,408; fix_dispatch.py:46,56;
  route_edit.py:11; route_cleanup.py:15; gates.yaml:80; jlc_capabilities.yaml:33
