# insufficient_transition_vias

A via cluster on a budgeted power net holds fewer vias than `ceil(current_a / via_amps)`. check_current unions
every via of the net within 2.0 mm into one cluster and charges each cluster the FULL rail budget - net-wide,
never per-branch.

- Emitted by: check_current.py (check_current.py:197-210)   Gate: verify (P8 verify_all.py, fail_severities [error], max_count 0)
- Fixer domain: router   Scripts you may use: route_edit.py, kc.py, render.py, stitch_vias.py, route_cleanup.py
- Fields on the violation: `pos` [x,y] = cluster CENTROID, not a via centre; `net`; `vias` (found); `required`; severity always error. `layer` is always null and `items[]` carries NO uuid - locate vias by matching coordinates in the board file text. Per-net facts in `checked[]`: `current_a`, `dt_c`, `via_clusters` (no `via_amps` - read it from constraints.json).

## Is it real?
- Arithmetically correct, but the RULE is known-unsatisfiable for a plane-fed rail. Where the trunk is a pour,
  every via is a single-pin leaf tap by construction: lumina-carrier `+3V3` (1.0 A, 0.5 A/via) had 27 clusters of
  exactly ONE via, and a machine-measured sweep placed a companion in only 2 of 44 clusters - 26 had ZERO
  candidates because the outer-layer stub is shorter than the 0.8 mm via-to-via pitch (LEARNINGS line 1588).
- `overrides` cannot reach this check: `segment_current` feeds only the track-width test, so a bulk-cap or fuse
  tap carrying milliamps is still charged the whole rail (LEARNINGS line 808; check_current.py:199 uses `budget`).
- Bad-input class that fakes it: an inflated `current_a`. lumina-carrier `V48_RAW` was authored at 1.5 A from a
  transient cap-dump basis and corrected at P8 to 1.0 A (see `_p8_basis` in the board's constraints.json). A
  transient peak is the wrong basis for a continuous IPC-2152/via rating. Report it; do not edit constraints.

## Fix ladder (cheapest first)
1. Measure before acting: find the via(s) near `pos` in the .kicad_pcb text and check whether the same-net outer
   copper around them exceeds ~0.8 mm. If not, no companion fits and steps 2-3 are wasted attempts.
2. Room exists -> `route_edit.py --pcb <board> --ops <ops.json>` with
   `{"op":"add_via","at":[x,y],"size":S,"drill":D,"net":"<net>"}`, placed within 2.0 mm of the existing via so
   union-find merges them into ONE cluster (check_current.py:55,78). Match size/drill to the net's existing vias.
3. Current-carrying SMD tap whose plane is on another layer -> the real fix is a pour lobe plus a via FIELD, not
   more single vias (LEARNINGS line 832). That needs planes_gen, outside the router whitelist: report
   `requires_pipeline_rewind` / plane domain.
4. If the net can live on ONE layer, deleting the transition clears the finding outright - a via-free pour has no
   cluster to fail (pd-trigger shipped VBUS with 0 vias, check_current 0 violations; LEARNINGS line 808). Only if
   the re-route stays inside your cluster's scope.
5. Escalate: name the geometrically infeasible clusters with their measured candidate counts, state that the
   durable fix is fanning the rail out with 2-via taps at P5/P7 and cannot be retrofitted at P8 (LEARNINGS 1588),
   and hand back the levers the orchestrator owns: correct `current_a`, or justify a higher `via_amps`.

## Do not
- Delete a via to tidy a cluster: removing the wrong member of a 3-via cluster splits it into two 1-via clusters
  and ADDS two violations. Moving the via instead of deleting it holds the count constant (LEARNINGS line 1735).
- Drop a companion via near a THT pad without computing that pad's drill yourself - KiCad DRC does not test a via
  drill against a same-net THT pad drill, so 0.12 mm hole-edge spacing ships green and fails at P9 (LEARNINGS 819).
- Add a via bonding same-net copper on only ONE layer: a plane fill alone is not a bond, KiCad flags via_dangling
  and drc_routed fails on warnings (LEARNINGS line 453).
- Expect `stitch_vias.py` to fix it: pad stitching SKIPS any pad already within 0.2 mm of a same-net via
  (stitch_vias.py:1-14) - exactly your 1-via cluster.
- Raw-edit constraints.json to raise `via_amps` or lower `current_a`. Not your domain.
- Assume a parallel path shares the current: check_current has no connectivity graph, and every segment audited on
  lumina-carrier turned out to be a graph bridge carrying the whole rail (LEARNINGS line 1902).

## Verify
```
cd C:\dev\ai-ee3
.venv\Scripts\python.exe .claude\skills\hwde\scripts\check_current.py --pcb boards\lumina-carrier\kicad\lumina-carrier.kicad_pcb --constraints boards\lumina-carrier\kicad\constraints.json
.venv\Scripts\python.exe .claude\skills\hwde\scripts\gate.py --gate drc_routed boards\lumina-carrier\kicad\lumina-carrier.kicad_pcb
.venv\Scripts\python.exe .claude\skills\hwde\scripts\gate.py --gate verify boards\lumina-carrier\kicad\lumina-carrier.kicad_pcb
```
Compare `checked[].via_clusters` before/after - it must not increase. Run drc_routed as well: an added via can
regress via_dangling / hole_to_hole, which the verify gate never looks at.

## Sources
- LEARNINGS 2026-07-28 [routing][check_current] via-count rule is NET-WIDE (line 808)
- LEARNINGS 2026-07-29 [check_current][gates] unsatisfiable for a PLANE-fed rail, overrides cannot reach it (1588)
- LEARNINGS 2026-07-29 [check_current][gates] no bridge awareness (line 1902)
- LEARNINGS 2026-07-23 [stitch][geometry] via-candidate obstacles = wired copper; single-layer bonds dangle (453)
- LEARNINGS 2026-07-28 [routing][stitch][drc] hole_to_hole blind to same-net THT pad drill (line 819)
- LEARNINGS 2026-07-28 [routing][placement] USB-C SMD GND needs pour lobe + via field (line 832)
- LEARNINGS 2026-07-30 [stitch_vias][dfm] stitch 0.35 mm from a thermal-via drill; move-not-delete recovery (1735)
- check_current.py:55,78,197-210 | cluster_violations.py:41 | fix_dispatch.py:46-62 | gates.yaml:80-88
- boards/lumina-carrier/kicad/constraints.json (power entries) | boards/lumina-carrier/reports/checks/check_current.json (41 live instances)
