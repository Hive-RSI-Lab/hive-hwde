# track_dangling

KiCad DRC "Track has unconnected end": one endpoint of one track segment shares no coincident
vertex with same-net copper (track end, via, pad, or same-net fill).

- Emitted by: kicad-cli DRC via scripts/kc.py (`check: "track_dangling"`, severity `warning`)
  Gate: `drc_routed` (P7, fails on error AND warning, max_count 0). The P6 `drc` gate is
  error-only and passes with these still on the board - never use it as proof.
- Fixer domain: router   Scripts you may use: route_edit.py, kc.py, render.py,
  stitch_vias.py, route_cleanup.py
- Fields on the violation: `pos` [x,y] = the free end; `net`; `layer`; `refs` usually empty;
  `msg` always the generic "Track has unconnected end". The diagnosis is in `items[0].msg`
  ("Track [+12V] on F.Cu, length 0.0247 mm"); `items[0].uuid` is the segment to act on.
  Exactly one item in all 91 recorded live instances; schema at kc.py:90-121.

## Is it real?
- No known false-positive class: KiCad joins copper at COINCIDENT VERTICES, not at
  overlapping geometry, so the finding is always geometrically true. What varies is whether
  the fix is "remove" or "reconnect". Never judge from a render - a bridge ending 0.0368 mm
  from a stub end, deep inside its 0.250 mm half-width, still fired this (LEARNINGS 1724).
- Crumb class: `items[0].msg` length < 0.05 mm is a sub-grid KRT crumb, connectivity-safe to
  delete (LEARNINGS 467). Only 4 of the 19 distinct ends in this repo's DRC reports qualified.
- Orphan class: a part moved or a via was deleted, leaving its GND stub / escape behind. Dead
  copper - remove the whole chain, do not reconnect it (LEARNINGS 1750).
- Bad input: DRC over stale pours. Refill in the same DRC call (LEARNINGS 1445).

## Fix ladder (cheapest first)
1. Classify by the length in `items[0].msg`. If < 0.05 mm -> crumb: one route_edit ops file
   of `{"op":"remove","uuid":...}` per crumb uuid in your cluster. Same sweep route_auto
   already runs on KRT output (route_auto.py:194-202).
2. Orphaned stub/escape: walk the same-net chain from the free end in the board text; remove
   the whole dead chain (segments plus any via that only served it) in one ops file.
3. Genuine reconnect: remove the offending segment, then re-add it with the endpoint snapped
   to the EXACT coordinate of the neighbouring same-net vertex. Endpoint-in-via and
   endpoint-in-pad count as connected; endpoint-in-track-body does not.
4. Real gap: `add_track` from the exact free-end coordinate to the exact target vertex, width
   matched to the same-net copper abutting the endpoints. Refill if it crosses a pour.
5. route_cleanup.py --pcb <board> --dry-run --out-report r.json ONLY, then cherry-pick: `ops`
   is ordered dangling-segments, dangling-vias, loops, corners, so the first
   (`dangling_segments` + `dangling_vias`) ops are the safe removals (route_cleanup.py:519-536).
6. Escalate: tell the orchestrator this free end is a real routing gap needing a re-route of
   the net (route_auto/route_critical, not a fixer edit); give net, layer, pos.

## Do not
- Do not run route_cleanup.py without --dry-run: its loop-breaker regressed on all three live
  boards and can drop a load-bearing plane-mediated segment (PROGRESS.md:101, V13).
- Do not put `remove <uuid at P>` and an `add_*` at position P in ONE ops file: route_edit
  adds before it removes, the add is deduped as "exists", and the file rolls back
  (LEARNINGS 1553). Two invocations.
- Do not "nudge close enough": sub-0.3 mm mismatches split check_diffpair's net graph while the
  copper still overlaps; snap to the exact vertex coordinate, do not approximate (LEARNINGS 1724).
- Do not fix this by moving a footprint: wrong domain, and a move on a routed board orphans
  more stubs and vias than it heals (LEARNINGS 1750).
- Do not stop at zero track_dangling: confirm via_dangling did not grow (same defect on a
  via; a via bonded on only ONE layer dangles even inside a plane fill - LEARNINGS 453).

## Verify
```
.venv\Scripts\python.exe .claude\skills\hwde\scripts\route_edit.py --pcb boards\<ws>\kicad\<board>.kicad_pcb --ops work\fix\ops.json --out-report work\fix\edit.json
.venv\Scripts\python.exe .claude\skills\hwde\scripts\kc.py drc boards\<ws>\kicad\<board>.kicad_pcb --parity --all-track-errors --refill --save-board --out work\fix\drc.json
.venv\Scripts\python.exe .claude\skills\hwde\scripts\gate.py --gate drc_routed boards\<ws>\kicad\<board>.kicad_pcb
```

## Sources
- LEARNINGS 2026-07-30 [kicad][connectivity][route_edit] endpoint in VIA/PAD connects, in a TRACK body does not (line 1724); [place_edit][placement][silk] moving a part orphans stubs and vias (line 1750)
- LEARNINGS 2026-07-23 [placement][routing] KRT leaves sub-grid crumbs (line 467); [stitch][geometry] single-layer-bond vias dangle (line 453)
- LEARNINGS 2026-07-29 [route_edit][kicad] adds BEFORE removes (line 1553); [routing][gates] refill KRT output before DRC (line 1445)
- PROGRESS.md:101 - V13 route_cleanup loop-breaker demoted to dry-run cherry-pick
- .claude/skills/hwde/reference/gates.yaml:60 - drc_routed err+warn, max_count 0
- .claude/skills/hwde/scripts/fix_dispatch.py:46-62 - router domain script list
