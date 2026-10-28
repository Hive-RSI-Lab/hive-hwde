# dfm_trace_width

A conductor on an exported copper gerber is narrower than the fab profile's
`min_trace_width_mm`. Measured on the GERBER, not the board: it is what JLC's CAM sees.

- Emitted by: dfm_check.py `check_trace_width` (scripts/dfm_check.py:125, via lib/gerblib.py)   Gate: dfm (reference/gates.yaml:104, fail_severities [error], max_count 0)
- Fixer domain: router (cluster_violations.FIXER_HINTS:61)   Scripts you may use: route_edit.py, kc.py, render.py, stitch_vias.py, route_cleanup.py (fix_dispatch.DOMAINS:46)
- Fields on the violation: `pos` = the segment MIDPOINT in board mm (dfm_check._mid), `layer` = copper layer name, `width_mm`, `min_mm`, `severity` "error", `msg`. There is NO `net`, NO `refs`, NO `items[].uuid` - gerbers carry no net data (checklib.violation call at dfm_check.py:136 passes net=None, refs=[]). Because `net` is null, clustering is spatial only, so one order can hold hundreds of segments on both outer layers.

## Is it real?
- The measurement itself is trustworthy: width comes straight from the aperture (`aperture.equivalent_width("mm")`, gerblib.py:129), so the arc-tessellation error class that fakes `dfm_annular_ring` (LEARNINGS 2026-07-24 [gerbonara][gerber][geometry], line 478) does not apply here. Zone fills are Regions, not trace_lines, so a pour never triggers this.
- Almost every occurrence in this repo is REAL but is a RULES defect, not a routing mistake: `board_init.write_pro` hard-codes `min_track_width: 0.1`, below every JLC profile you will meet (0.1016 / 0.127 / 0.1524; only `6layer_1oz`'s 0.0889 is finer, jlc_capabilities.yaml:106), so the router legally lays 0.1000 mm and DRC passes 0/0. On lumina-carrier that was 189 of 194 dfm errors, a 1.6 micrometre gap (LEARNINGS 2026-07-29 [board_init][rules_gen][dfm][gates] line 1939; 2026-07-30 line 1808; 2026-07-30 [gates][dfm] line 2011). Do not treat a clean `drc_routed` as evidence the finding is bogus.
- The check is BLIND in the false-negative direction on heavy copper: `gate.py:74 run_dfm` never passes `copper_oz`, and `dfm_check.run` defaults it to 1.0 (dfm_check.py:547), so a 2 oz board is graded against the 1 oz floor. Read `capability_key` in the report; if the build is 2 oz, the real floor is 0.1524 and the gate is under-reporting - escalate rather than fixing to the printed `min_mm`.
- Zero tolerance: the comparison is `w + 1e-9 < lo` (dfm_check.py:134), unlike the 2e-3 `GEOM_TOL_MM` used elsewhere. A trace at exactly `min_mm` passes, with no margin for anything.

## Fix ladder (cheapest first)
1. Classify before editing. `min_trace_width_mm` (measured minimum) and `capability_key` are top-level facts in dfm_check.py's OWN report only - the gate result carries just `counts` + `failing` (gate.py:130-141). If every `width_mm` is one repeated value just under `min_mm`, this is the board-wide floor defect - one bulk pass fixes all of it; do not hand-work segments.
2. For each violation, locate the track: grep the .kicad_pcb for the `(segment ...)` whose start/end MIDPOINT equals `pos` (endpoints will not match `pos`), take its `(uuid ...)`, `(net ...)`, `(layer ...)`.
3. Batch ONE ops file `{"version": 1, "ops": [...]}` (route_edit.py:11): per segment a `remove {uuid}` plus an `add_track {start, end, width, layer, net}` reusing the EXACT original endpoint coordinates - KiCad joins copper at coincident vertices, not overlapping geometry (LEARNINGS 2026-07-30 [kicad][connectivity][route_edit], line 1724). Width = the abutting same-net copper's width if that already clears the floor, else the floor plus margin (see Do not).
4. If any edited track crosses a zone, refill before re-gating: `kc.run_drc(cli, board, refill=True, save_board=True)` - refill without `save_board` is NOT written back (kc.py:229-232) - or `kicad-cli pcb drc --refill-zones --save-board` (fix_dispatch DOMAINS router guidance, fix_dispatch.py:58).
5. If a segment cannot be widened because foreign pads pinch the fan-in (measured case: no legal track reaches a 0.5 mm-pitch USB-C VBUS pad; LEARNINGS 2026-07-28 [routing][kicad][drc] line 782), do NOT neck or shave clearance. Report it in OPEN as a `plane`-domain fix (a zone is not a track).
6. Escalate the root cause in OPEN regardless of outcome: `.kicad_pro.min_track_width` must be raised to `jlc_capabilities[profile].min_trace_width_mm` and `rules_gen` re-run with `--pro` so `aiee_track_width_floor` exists. `rules_gen.py` is NOT in your allowed scripts - say so and let the orchestrator do it, or the next routing pass re-lays 0.1 mm.

## Do not
- Do not widen to exactly `min_mm`. The lumina-carrier board sat 1.6 micrometres over a fab floor and that was flagged as leaving nothing for process wander (LEARNINGS 2026-07-30 line 1705, margin note).
- Do not raw-edit widths in the .kicad_pcb. route_edit is the pipeline's only routing writer and it verifies every add to 1e-3 mm before the atomic swap (route_edit.py:16-22).
- Do not match `pos` against segment endpoints - it is the midpoint of the gerber line.
- Do not silence this with `dfm_check.py --skip copper`: that also disables `dfm_clearance` and `dfm_copper_to_edge`.
- Do not re-verify with a cached `--fab-dir`. The gate re-exports gerbers to a scratch dir every run (dfm_check.py:557); a stale dir measures pre-fix copper.
- Do not hand-write a `.kicad_dru` that REPLACES rules_gen's output - that is how the fab floors went missing here in the first place (LEARNINGS line 1808).
- Do not size a track off its netclass without reading the board's own `.kicad_pro`/`.kicad_dru` first: boards generated before the T1 netclass split carry ONE flattened `Power` class at the WIDEST width, so netclass-sizing a 20 mA net gives it a 5 A width and breaks its pad fan-in (LEARNINGS 2026-07-28 [routing][rules_gen][freerouting], line 798). Size from the segment's own required width, not from the class.

## Verify

```
.venv\Scripts\python.exe .claude\skills\hwde\scripts\route_edit.py --pcb <ws>\kicad\<board>.kicad_pcb --ops <ws>\work\widen_ops.json --out-report <ws>\work\widen_report.json
.venv\Scripts\python.exe .claude\skills\hwde\scripts\gate.py --gate dfm <ws>\kicad\<board>.kicad_pcb --out <ws>\work\dfm_after.json
.venv\Scripts\python.exe .claude\skills\hwde\scripts\gate.py --gate drc_routed <ws>\kicad\<board>.kicad_pcb --out <ws>\work\drc_after.json
.venv\Scripts\python.exe .claude\skills\hwde\scripts\dfm_check.py --pcb <ws>\kicad\<board>.kicad_pcb --out <ws>\work\dfm_facts.json
```

Both gates must pass: widening copper is the classic way to trade a `dfm_trace_width` error for a
`dfm_clearance` or DRC clearance error. In `dfm_after.json` check `counts.total` and that no
`dfm_trace_width` is left in `failing`; in `dfm_facts.json` that `min_trace_width_mm` now clears the
profile floor.

## Sources
- LEARNINGS 2026-07-29 [board_init][rules_gen][dfm][gates] board_init writes min_track_width 0.1, below every JLC profile (line 1939)
- LEARNINGS 2026-07-30 [board_init][rules_gen][dfm] min_track_width 0.1 - second time the same defect bit (line 1808)
- LEARNINGS 2026-07-30 [gates][dfm] drc_routed 0/0 does NOT imply fabricable (line 2011)
- LEARNINGS 2026-07-30 [board_init][dfm][gates] min_hole_to_hole 0.25 - sub-fab floor, margin note (line 1705)
- LEARNINGS 2026-07-30 [kicad][connectivity][route_edit] KiCad joins copper at coincident vertices (line 1724)
- LEARNINGS 2026-07-28 [routing][kicad][drc] net-wide track_width floor unmeetable at a fine-pitch pad - pour it (line 782)
- LEARNINGS 2026-07-28 [routing][rules_gen][freerouting] rules_gen flattens power nets to the widest netclass (line 798)
- LEARNINGS 2026-07-24 [gerbonara][gerber][geometry] ArcPoly.outline drops curvature (line 478)
- .claude/skills/hwde/scripts/dfm_check.py:125-139, :84-87, :547, :557
- .claude/skills/hwde/scripts/lib/gerblib.py:121-151
- .claude/skills/hwde/scripts/gate.py:74-87, :130-141
- .claude/skills/hwde/scripts/kc.py:221-232
- .claude/skills/hwde/scripts/route_edit.py:11-30, :52
- .claude/skills/hwde/scripts/cluster_violations.py:61, :144-160
- .claude/skills/hwde/scripts/fix_dispatch.py:46-61
- .claude/skills/hwde/reference/gates.yaml:104-118
- .claude/skills/hwde/reference/jlc_capabilities.yaml:34, :52, :70, :88, :106
