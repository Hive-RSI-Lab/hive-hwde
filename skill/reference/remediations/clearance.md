# clearance

Two copper items sit closer than the clearance constraint governing them. msg carries everything:
"Clearance violation (rule 'NAME' clearance X mm; actual Y mm)" - constraint that FIRED, need, actual.

- Emitted by: kicad-cli pcb drc, normalized by scripts/kc.py (check = "clearance")
  Gate: drc (P6, error-only) and drc_routed (P7, error+warning, max_count 0)
- Fixer domain: router   Scripts you may use: route_edit.py, kc.py, render.py, stitch_vias.py,
  route_cleanup.py
- Fields on the violation: check, severity, pos (= items[0].pos), layer, net (FIRST bracketed net seen,
  so it can name the wrong side), refs, msg, items[] = exactly TWO {msg, pos, uuid}. items[1] is the
  other side - read its msg ("Track", "Via", "Pad 2 [GND] of C61", "PTH pad 21") before deciding what may move. Constraint = msg rule '([^']+)' (LEARNINGS 214), else netclass 'NAME', else bare "( clearance X mm" = board setup, NOT the DRU (67 of 743 corpus clearance violations, e.g. lumina-carrier work/p7/drc_base.json).

## Is it real?
- Both items are pads of the SAME footprint -> package geometry, not a routing defect. Real: Pad 1
  [V48_RAW] of C61 vs Pad 2 [GND] of C61, 0.590 vs 0.635 (lumina-carrier reports/drc-place.json).
  No copper edit fixes it; cure is a DRU same-courtyard exclusion (LEARNINGS 1280; form at
  lumina-carrier.kicad_dru:69), not your domain -> escalate.
- Pads of TWO DIFFERENT footprints -> placement defect, also not routable copper. Real: C10 pad 1
  vs U1 pad 11 at 0.0779 (stm32-blinky reports/place/drc_seed.json). Escalate to placement.
- Board not sitting next to its own .kicad_pro / .kicad_dru / fp-lib-table: KiCad silently falls back
  to DEFAULT rules, so the numbers come from another rule set (LEARNINGS 754). Check this first if
  the rule name is unfamiliar.
- Numbers that contradict the DRU you just read: two same-constraint rules can both match and the
  LATER one in file order wins (LEARNINGS 222). Trust the rule name in msg only.
- Not a false positive but the classic blind spot: the other item may be an UNNETTED pad
  ("unconnected-(U1-PA1-Pad11)"). DRC sees those, a model built from BoardGeom.nets does not - 37 such
  pads on lumina-carrier (LEARNINGS 1538). Iterate bg.pads_of(), keep pads with no net.

## Fix ladder (cheapest first)
1. Classify from items[].msg. Pad-vs-pad -> step 5. Track or Via on either side -> that item has a
   uuid you can act on.
2. Gain needed = (required - actual) from msg, plus margin. Remove the offender by uuid and re-add
   it displaced: route_edit.py --pcb <board> --ops <ops.json> (add_track {start,end,width,layer,
   net}, add_via {at,size,drill,net}, remove {uuid}).
3. If a stitching via is the offender, delete it by uuid rather than nudge it, then re-run
   stitch_vias.py --pcb <board> --clearance <the rule's mm> --dry-run before applying.
4. If an edit crossed a zone fill, refill before re-gating: kc.py drc --refill --save-board.
5. Escalate: report rule name, required/actual, both item descriptions, and the class - "package
   geometry, needs a DRU courtyard exclusion", "needs a part moved (placement)", or "rule floor
   exceeds what this package can hold". A fired `aiee_clearance_floor` is the FAB floor from jlc
   capabilities (rules_gen.py:106, min_clearance_mm), never relaxable; gate.py has no waiver path, so an unsatisfiable rule gets re-scoped upstream, not tolerated.

## Do not
- Do not move a clearance value onto a NETCLASS to make the router honour it: netclasses are
  pad-blind and unscopable; 0.635 mm produced 30 instant pad-pair errors (LEARNINGS 1294).
- Do not raise KRT's --clearance expecting a floor: it CAPS the netclass map, pulled HV nets down
  to 0.2 mm, and silently yielded 480 violations (LEARNINGS 1522).
- Do not replace a track/via at UNCHANGED geometry in one ops file: route_edit adds before it
  removes, the add dedups as "exists", the whole file rolls back (LEARNINGS 1553). Two calls.
- Do not trust a hand-rolled segment distance in a pre-flight: min-over-endpoints reports a
  crossing SHORT as positive clearance (LEARNINGS 1971). Use shapely .distance().
- Do not run route_cleanup.py to "tidy" the region: no rollback after apply, damage surfaces only
  as cleanup_regression / exit 1 (route_cleanup.py:30).

## Verify
```
.venv\Scripts\python.exe .claude\skills\hwde\scripts\route_edit.py --pcb boards\<ws>\kicad\<board>.kicad_pcb --ops boards\<ws>\work\fix-<id>.json --out-report boards\<ws>\work\fix-<id>.rep.json
.venv\Scripts\python.exe .claude\skills\hwde\scripts\kc.py drc boards\<ws>\kicad\<board>.kicad_pcb --refill --save-board --all-track-errors --out boards\<ws>\work\drc-after.json
.venv\Scripts\python.exe .claude\skills\hwde\scripts\gate.py --gate drc_routed boards\<ws>\kicad\<board>.kicad_pcb --out boards\<ws>\work\gate-after.json
```

## Sources
- LEARNINGS 2026-07-22 [kicad-cli][drc] .kicad_dru auto-loaded, rule name in msg (214); A.NetName token, LATER rule wins (222)
- LEARNINGS 2026-07-28 [drc][kicad-cli] DRC on a board copy outside the project dir changes the rules (754)
- LEARNINGS 2026-07-29 [routing] net-wide HV clearance makes fine-pitch pads unroutable (1280); netclass clearance is PAD-BLIND (1294)
- LEARNINGS 2026-07-29 [krt][clearance] KRT --clearance is a CAP not a floor (1522); [geom][drc] bg.nets omits UNNETTED pads (1538)
- LEARNINGS 2026-07-29 [route_edit] adds BEFORE it removes (1553); [geometry][fixer] min-over-endpoints reports a SHORT as clearance (1971)
- scripts/kc.py:55,90; cluster_violations.py:79; fix_dispatch.py:46; route_edit.py:52; stitch_vias.py:580; route_cleanup.py:30; rules_gen.py:106
- reference/gates.yaml:52,60; boards/lumina-carrier/reports/drc-place.json; boards/stm32-blinky/reports/place/drc_seed.json
