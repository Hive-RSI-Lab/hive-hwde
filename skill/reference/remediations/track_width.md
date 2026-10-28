# track_width

A copper track is narrower than the minimum width in force at that item. Two authorities emit this same
check id - the board-setup floor (`.kicad_pro` `design_settings.rules.min_track_width`) and named
`.kicad_dru` rules. The `msg` says which fired, and that decides the fix.

- Emitted by: kicad-cli DRC (`<board>.kicad_dru` auto-loads, no flag), normalized by `scripts/kc.py`
  `normalize_violation`.   Gate: `drc` (P6, errors only), `drc_routed` (P7, error+warning)
- Fixer domain: router   Scripts you may use: route_edit.py, kc.py, render.py, stitch_vias.py, route_cleanup.py
- Fields on the violation: `pos` [x,y] mm, `layer`, `net`, `refs` (EMPTY - tracks have no refdes), `severity`, `msg` =
  `Track width (rule 'NAME' min width X; actual Y)` or `(board setup constraints min width X; actual Y)`,
  `items[0].uuid` (the segment - act on this), `items[0].msg` = `Track [NET] on F.Cu, length L mm` (L is
  your crumb detector).

## Is it real?
- `actual` is exact; the THRESHOLD may be wrong. Boards built before the T1 fab-floor hardening carry
  `min_track_width: 0.1`, under the 0.1016 / 0.127 / 0.1524 profiles - lumina-carrier sat at drc_routed
  0/0 and still failed `dfm` with 189 sub-fab traces (LEARNINGS 1939, 1808). Read the pro, not the msg.
- Check-blind: a hand-written `.kicad_dru` REPLACES rules_gen output, so `aiee_track_width_floor` (plus
  the annular/hole floors) vanishes silently (same entries). If `msg` says "board setup constraints" and
  the `.kicad_dru` has no `aiee_*` rule, the fab floor is unenforced -> fix the input, not the track.
- Wrong min: when two rules of one constraint type match an item, the LATER in file order wins
  (LEARNINGS 222). rules_gen emits baseline first, per-net last (rules_gen.py:104 vs :130).
- Bad input: DRC on a board copy OUTSIDE the project dir loses the sibling `.kicad_pro`/`.kicad_dru`
  and quotes KiCad DEFAULTS (LEARNINGS 754). Confirm where the report was produced.
- Real but unfixable as a width: an `aiee_pwr_width_<NET>` floor can be geometrically unmeetable at a
  fine-pitch pad - 1.465 mm widest connectable track vs a 1.75 mm rule at a USB-C VBUS pad (LEARNINGS
  782). Ladder step 4.
- Crumb: `items[0].msg` length far under the required width (lumina-carrier +12V, 0.1414 mm long at
  0.1298 mm wide, work/p7/drc_r1_new.json). Only sub-0.05 mm KRT crumbs delete safely (LEARNINGS 467).

## Fix ladder (cheapest first)
1. Read `msg`. If "board setup constraints", check the floor first: `.kicad_pro` `min_track_width` must
   be >= `jlc_capabilities.yaml` `design_rules[profile].min_trace_width_mm`. Lower -> go to step 5 now.
2. Crumbs: `route_cleanup.py --pcb <board> --dry-run --out-report <r>.json`; sub-0.05 mm segments sit
   BELOW its touch tolerance (LEARNINGS 467), so if it does not name your uuids, remove them in step 3.
3. Widen in place: grep `items[0].uuid` in the `.kicad_pcb` for the segment start/end, then one ops file
   `{"version":1,"ops":[{"op":"remove","uuid":..},{"op":"add_track","start":[..],"end":[..],"width":W,
   "layer":"F.Cu","net":"NET"}]}`, W = max(required min, abutting same-net width).
4. Cannot fit (a clearance violation appears at the same pos, or the pad fan-in is pinched): do NOT neck
   back. Escalate to the `plane` domain - a zone is not a track, so the width rule does not apply and the
   filler necks around foreign pads (LEARNINGS 782). Measure first: `route_critical.py --pad-window`
   reports each power pad's width ceiling vs its DRU floor. Concrete invocation:
   `planes_gen.py --constraints <planes-only sidecar>` with `"connect": "solid"` on the fan-in zone
   (solid pad connection - thermal relief would spoke-starve a 5 A pad; LEARNINGS 782/791).
5. Escalate: a P5 input defect - `rules_gen` must regenerate the `.kicad_dru` floors and the `.kicad_pro`
   `min_track_width` from the fab profile, then re-route/re-gate. Copper edits cannot fix a wrong floor.

## Do not
- Do not widen to the quoted min when that min is a legacy 0.1 mm pro floor - it is below the fab.
- Do not delete a `.kicad_dru` rule to clear the finding; that is exactly how the fab floor was lost.
- Do not neck a power net at a fine-pitch pad to satisfy a per-net width rule.
- Do not trust a min from a DRC run on a staged copy, nor append a broad rule after a specific one.
- Do not ignore `route_cleanup` exit 1 (`cleanup_regression`) - board left MODIFIED, restore the snapshot.

## Verify
```
cd C:\dev\ai-ee3
.venv\Scripts\python.exe .claude\skills\hwde\scripts\route_edit.py --pcb boards\<ws>\kicad\<board>.kicad_pcb --ops work\fix-<id>.json --out-report work\fix-<id>-edit.json
.venv\Scripts\python.exe .claude\skills\hwde\scripts\gate.py --gate drc_routed boards\<ws>\kicad\<board>.kicad_pcb --out work\drc_after.json
grep -n "min_track_width" boards\<ws>\kicad\<board>.kicad_pro
grep -n "aiee_track_width_floor" -A 3 boards\<ws>\kicad\<board>.kicad_dru
```

## Sources
- LEARNINGS 2026-07-22 [kicad-cli][drc] .kicad_dru auto-loads, rule name in description (214); later rule wins (222)
- LEARNINGS 2026-07-23 [placement][routing] KRT sub-grid crumbs; route_cleanup self-detects (line 467)
- LEARNINGS 2026-07-28 [drc][kicad-cli] DRC outside the project dir changes the rules (line 754)
- LEARNINGS 2026-07-28 [routing][kicad][drc] net-wide track_width floor UNMEETABLE - pour it (line 782)
- LEARNINGS 2026-07-29 [board_init][rules_gen][dfm][gates] min_track_width 0.1 (1939); repeat 2026-07-30 (1808)
- .claude/skills/hwde/scripts/rules_gen.py:104, :130, :294 - kc.py:90, :394
- .claude/skills/hwde/scripts/route_edit.py:51 - cluster_violations.py:79 - fix_dispatch.py:46
- .claude/skills/hwde/reference/gates.yaml:52,60 - boards/lumina-carrier/work/p7/drc_r1_new.json
