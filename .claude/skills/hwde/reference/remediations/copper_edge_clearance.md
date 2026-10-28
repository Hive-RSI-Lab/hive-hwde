# copper_edge_clearance

Copper (pad, track, via, zone fill) sits closer to an Edge.Cuts shape than the copper-to-edge minimum, or crosses it (`actual 0.0000 mm`).
Two authorities emit this id and `msg` names which fired: the board-setup floor (`.kicad_pro` `min_copper_edge_clearance`) or the `.kicad_dru` rule `aiee_edge_clearance_floor`.

- Emitted by: kicad-cli DRC, normalized by `scripts/kc.py` `normalize_violation`.   Gate: `drc` (P6, errors), `drc_routed` (P7, error+warning); gerber twin `dfm_copper_to_edge` at `dfm` (P9).
- Fixer domain: router (cluster_violations.py:84)   Scripts you may use: route_edit.py, kc.py, render.py, stitch_vias.py, route_cleanup.py (fix_dispatch.py:46)
- Fields on the violation: `pos`/`layer` describe the EDGE.CUTS shape, NOT the defect (kc.py:114); `net`; `refs` (empty for tracks); `items[0]` = that Edge.Cuts shape + uuid;
  `items[1]` = the copper (`Pad N [NET] of REF on F.Cu` / `Track [NET] on F.Cu, length L` / `Zone [NET] on In1.Cu`) + uuid + pos - work from that one.

## Is it real?
- Bogus region: cluster_violations groups by `pos` (cluster_violations.py:105,:126), so all violations on one outline shape collapse into ONE region on the
  outline anchor - blinky `pos` [9.38, 8.95] for a pad at [58.68, 11.659] (reports/place/drc_seed.json). Navigate by `items[1].pos`, never the region.
- Pre-placement artifact - 108 of the 112 recorded occurrences. board_init shelf-packs parts ignoring the outline (LEARNINGS 231) and exempts only SILK
  edge checks (board_init.py:225-234); lumina-carrier P5 shipped 27 as "components still stacked at the origin" (log/P5-digest.md:15-18), 26 of them
  `actual 0.0000 mm` with copper outside `outline_bbox`. Pre-P6 placement clears them; do not route around them.
- Outline defect, not copper defect: an interior Edge.Cuts `gr_rect` silently BECOMES the outline (`geom._parse_outline`, LEARNINGS 964) - an `items[0]`
  rect that is not the board perimeter exonerates the copper.
- Check-blind twin: `dfm_check.check_copper_to_edge` returns silently on `outline.is_empty` (dfm_check.py:183-188), which is what an arc outline parses
  to (LEARNINGS 908) - a clean `dfm` gate is never evidence against this finding.
- Stale fill: if `items[1]` is a `Zone`, refill BEFORE believing it - a fill predating a copper edit produced 371 phantom zone violations on
  a raw KRT output board (LEARNINGS 1445).
- Threshold: 0.3 mm on every JLC profile (jlc_capabilities.yaml:40), pushed into `.kicad_dru` and `.kicad_pro` by rules_gen
  (rules_gen.py:113, :294); a quoted 0.5 is a board setup stricter than the fab floor (lumina-carrier.kicad_pro:145): real, not fab-fatal.

## Fix ladder (cheapest first)
1. Read `items[1].msg` - it decides everything: `Zone` -> 2, `Track`/`Via` -> 3, `Pad` -> 5. If `items[1].pos` is outside the outline bbox, go to 6.
2. Zone: `kc.py drc <board> --refill --save-board`, re-gate. If it survives, the pour rect is the outline BBOX inset by `--inset-mm` (default 0.5,
   planes_gen.py:81,:158-173) - a non-rectangular outline needs a bigger inset: `plane` domain, escalate if not yours.
3. Track/via: grep `items[1].uuid` in the `.kicad_pcb` for start/end, then ONE ops file `{"version":1,"ops":[{"op":"remove","uuid":..},{"op":"add_track",
   "start":[..],"end":[..],"width":W,"layer":"F.Cu","net":"NET"}]}` pulled inward by (required - actual) plus margin; vias get stitch_vias'
   `EDGE_MARGIN = 1.0` mm (stitch_vias.py:83), not the bare floor.
4. Whole run hugs the edge: re-route the net instead of nudging segments - route_critical.py passes `--board-edge-clearance` from `min_copper_to_edge`
   (route_critical.py:260-261,:439).
5. Pad: route_edit cannot move a pad. Escalate to `placement` (place_edit.py) or board_init (grow the outline), reporting
   `requires_pipeline_rewind` (fixer.md:40); on a routed board the move also strands that part's copper (fix_dispatch.py:68-71).
6. Escalate: many violations on one Edge.Cuts uuid, or an interior rect as `items[0]`, is a P5 outline/placement defect - a router fixer cannot fix the
   board shape.

## Do not
- Do not navigate by `pos`, `layer` or the cluster region - all three describe the Edge.Cuts shape.
- Do not draw a new Edge.Cuts shape around offending copper: an interior rect replaces the outline and every area/DFM/plane consumer then silently reads
  the wrong board (LEARNINGS 964).
- Do not grow the corner radius to escape a corner hit - it is clamped to the mounting-hole inset, and moving holes inward drives them into a
  neighbour's courtyard (LEARNINGS 927).
- Do not measure with a min-over-endpoints helper (a crossing reads as positive clearance, LEARNINGS 1971); use shapely `.distance()`.
- Do not clear these one by one at P5/P6-seed - placement voids the work.

## Verify
```
cd C:\dev\ai-ee3
.venv\Scripts\python.exe .claude\skills\hwde\scripts\route_edit.py --pcb boards\<ws>\kicad\<board>.kicad_pcb --ops work\fix-<id>.json --out-report work\fix-<id>-edit.json
.venv\Scripts\python.exe .claude\skills\hwde\scripts\kc.py drc boards\<ws>\kicad\<board>.kicad_pcb --refill --save-board --out work\drc_refill.json
.venv\Scripts\python.exe .claude\skills\hwde\scripts\gate.py --gate drc_routed boards\<ws>\kicad\<board>.kicad_pcb --out work\drc_after.json
```

## Sources
- LEARNINGS 2026-07-22 [swig][kicad] shelf-pack import + board_init acceptance (231)
- LEARNINGS 2026-07-28 [geom][layout] inner gr_rect BECOMES the outline (964); arc outline -> POLYGON EMPTY (908); [layout] corner radius clamped to the inset (927)
- LEARNINGS 2026-07-29 [routing][gates] refill before DRC (1445); [geometry][fixer] min-over-endpoints reads a SHORT as clearance (1971)
- kc.py:90,:114 - cluster_violations.py:84,:105,:126 - fix_dispatch.py:46,:68 - agents/fixer.md:40 - board_init.py:225-234 - rules_gen.py:113,:294
- dfm_check.py:183 - planes_gen.py:81,:158-173 - stitch_vias.py:83 - route_critical.py:260,:439 - jlc_capabilities.yaml:40 - gates.yaml:52,:60,:104
- boards/lumina-carrier/work/board_init.json + log/P5-digest.md:15-18, pd-trigger/reports/route_auto.json, stm32-blinky/reports/place/drc_seed.json
