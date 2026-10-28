# corridor_void

A high-speed net's return corridor (centerline buffered by k x the chain's widest track, k default 3.0, flat-capped)
leaves the single connected reference-net copper component on the adjacent layer. Only deficit >= 0.05 mm2 survives.

- Emitted by: scripts/check_return_path.py (via verify_all.py)   Gate: verify (P8, fail_severities [error])
- Fixer domain: plane   Scripts you may use: planes_gen.py, plane_repair.py, stitch_vias.py, route_edit.py, kc.py
- Fields on the violation: pos [x,y] (representative point INSIDE the deficit), layer = the
  REFERENCE layer, signal_layer = where the trace runs, net = the signal net, reference_net,
  crossing_len_mm, area_mm2, polygon (deficit outline), severity. items[] carries msg+pos and
  NO uuid (checklib.py:59) - ground by coordinates/polygon, never remove-by-uuid.

## Is it real?
- STACKUP CLASS, usually not fixable in copper. The reference layer is adjacent copper compared
  against the DECLARED reference net. On F.Cu / In1=GND / In2=+3V3 / B.Cu the nearest plane to any B.Cu trace is
  +3V3, so every GND-referenced B.Cu run reports corridor_void however good the layout is - all 13 lumina-carrier
  findings were this, and declaring a net can only RAISE the count (LEARNINGS 2026-07-30, line 1774).
- severity=warning means crossing_len_mm < 0.01 (CROSSING_ERROR_MM, check_return_path.py:68):
  a corridor-edge nick, not the trace running over a void. The verify gate fails on error only
  (gates.yaml verify), so warning-only clusters do not block - do not cut copper for them.
- Lone-antipad artifacts are ALREADY excised (item radius + 0.65 mm disks around every via punching the ref layer
  and own/ref-net through pads, check_return_path.py:111-126). A survivor is structural - slot, moat, antipad chain
  - or a foreign through-pad FIELD, not excised on purpose (LEARNINGS 2026-07-11 [geometry], line 194).
- Stale fill fakes it: assert_fresh() rejects only UNFILLED zones unless --verify-fill is passed
  (geom.py:776-791), so copper edited without a refill is judged against an old fill polygon.

## Fix ladder (cheapest first)
1. Triage by severity/crossing_len_mm. Warning-only cluster -> no copper edit; report it.
2. Rule out stale fill: refill (kc.py drc <board> --refill --save-board; --save-board REQUIRES
   --refill, kc.py:461-462), re-run the check with --verify-fill. If it vanishes you are done.
3. Test the stackup class: read the actual net of the plane on the violation's `layer`. If it is not `reference_net`,
   no plane edit can clear it -> escalate. The real fix is a per-SIGNAL-layer reference map in constraints.json
   high_speed ("reference": {"B.Cu": ...}, check_return_path.py:82-86 and :254) - a file NOT in allowed_scripts.
4. Split plane: plane_repair.py --pcb <board> --net <reference_net> --layer <layer> --flag-only.
   On plane_split, re-run with --repair (it bridges, refills and re-analyzes itself; on exit 1
   restore the pre-fix snapshot per your work-order guidance).
5. Void inside one connected pour (plane_repair reports nothing): planes_gen.py --pcb <board>
   --no-thermal-vias (see Do not) only ADDS a pour - a planned region already >= 80% covered by
   existing fill is SKIPPED (planes_gen.py:48-49). Foreign copper = ROUTER fix, escalate.
6. Escalate, naming the class: "stackup waiver class, <signal_layer> over <adjacent plane net>; needs a per-signal-
   layer reference in constraints.json or a recorded waiver" / "requires_pipeline_rewind: reference-layer route at <pos>".

## Do not
- Do not run planes_gen.py without --no-thermal-vias on a routed board: ep_pads regrids vias into
  the largest netted SMD pad blind to footprints already shipping vias-in-pad (U22 got 21 extra
  -> 24 hole_to_hole + 2 holes_co_located, LEARNINGS 2026-07-29, line 1327). Same entry: same-net
  plane regions that merely TOUCH still read as zones_intersect in KiCad 10 - set distinct priorities.
- Do not hand-check containment inclusively - plane regions deliberately ABUT the band they
  exclude, so boundary vertices are legitimate copper (LEARNINGS 2026-07-29, line 1373).
- Do not stitch_vias into the corridor to "improve the return path": each added via punches a new
  antipad in the reference layer and antipad chains survive excision as structural deficit
  (check_return_path.py:24-33) - you can raise the count.
- Do not reach for route_cleanup.py: not in your domain, and it regressed on all three live
  boards - dry-run-inspect only (PROGRESS.md:101, V13).

## Verify
```
cd C:\dev\ai-ee3
.venv\Scripts\python .claude\skills\hwde\scripts\check_return_path.py --pcb <ws>\<board>.kicad_pcb --constraints <ws>\constraints.json --verify-fill --out <ws>\reports\return_path.json
.venv\Scripts\python .claude\skills\hwde\scripts\gate.py --gate verify <ws>\<board>.kicad_pcb
```
exit 0 = pass, 1 = ANY violation incl. warning-only (checklib.py:80), 2 = error (stale fill lands here).

## Sources
- LEARNINGS 2026-07-11 [geometry][shapely] Spec-literal return-path corridors FP on every legit board (194)
- LEARNINGS 2026-07-29 [planes_gen][drc] Two footprint facts planes_gen cannot see (1327); [geometry][keepout][planes] Keepout checks need STRICT-interior containment (1373)
- LEARNINGS 2026-07-30 [check_return_path][stackup] F/GND/+3V3/B fails every B.Cu trace by construction (1774)
- scripts/check_return_path.py:24-33,62-68,82-86,111-126,254,269-280 ; lib/checklib.py:46-62 ; lib/geom.py:776-791
- scripts/plane_repair.py:1-40,714-724 ; planes_gen.py:450-462 ; fix_dispatch.py:75-87 ; cluster_violations.py:39
- reference/gates.yaml:80-88 ; PROGRESS.md:101
