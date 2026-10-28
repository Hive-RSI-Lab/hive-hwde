# gnd_stub_long

A decoupling capacitor's GROUND pad sits more than 5 mm (GND_STUB_WARN_MM) from the nearest
same-net ground via: the return leg of the decoupling loop is a long stub, which dominates the
loop inductance however close the cap sits to its pin.

- Emitted by: scripts/check_decoupling.py (kind="gnd_stub_long")   Gate: verify (P8, via verify_all.py)
- Fixer domain: placement (cluster_violations.py)   Scripts you may use: place_edit.py, place_metrics.py, render.py
- Fields: pos, refs [cap, ic], the association's cap/ic/pin/gnd net, and the measured stub
  length in the msg. Severity: warning (advisory - the loop_nh term carries the error ladder).

## Is it real?
- Usually yes, and usually cheap: the distance is to the nearest GND VIA, so a cap sitting on
  perfectly good GND pour still fires when no via anchors that pour region to the plane. The
  fix is a via, not a move.
- 2-layer boards with a bottom GND pour: the stub is real only if the cap's GND pad reaches the
  pour through a long trace. Render the region; a pad directly on the pour with no nearby via
  still needs the via for the RETURN path, not for DC connectivity.
- Co-fired with decoupler_loop on the same cap: the ground leg dominates that estimate - fix
  this first and re-run before touching the cap position.

## Fix ladder (cheapest first)
1. Add a GND stitch via next to the cap's ground pad: stitch_vias.py targets the net and
   respects clearances and the drill floor - prefer it over hand-placed vias. Near THT pad
   drills also eyeball the via-drill-to-pad-drill spacing: KiCad DRC skips same-net
   drill-to-drill (LEARNINGS 2026-07-28 line 819).
2. No legal via position (dense fan-out): nudge the cap along its pin escape until a candidate
   opens - place_edit.py --ops, then re-run this check. After P7 routing has started, validate
   ANY move with kc.py drc, not with this check - both this check and the place gate passed a
   move that SHORTED lumina-carrier (LEARNINGS 2026-07-29 line 1470).
3. Refill zones after any copper edit near pours (kc.py drc --refill --save-board), then
   re-gate.
4. Escalate when the stub is structural (cap bank placed across a keepout from the plane):
   that is a placement-plan defect; name the region and the affected associations in OPEN.

## Do not
- Do not fix the number by moving the cap TOWARD the via and away from its pin: you trade the
  return stub for rail distance one-for-one and the loop gets no better - add the via instead.
- Do not hand-write via s-expressions; stitch_vias.py/route_edit.py are the writers, and raw
  edits skip the clearance and drill-floor models.
- Do not validate post-P7 edits with this check or the place gate alone (LEARNINGS 2026-07-29
  line 1470); only DRC sees routed copper.
- Do not delete the association from decoupling.json to silence the warning - the metadata is
  the schematic's decoupling INTENT; removing it blinds every later run.

## Verify
```
.venv\Scripts\python.exe .claude\skills\hwde\scripts\check_decoupling.py --pcb <ws>\kicad\<board>.kicad_pcb --metadata <ws>\kicad\decoupling.json
.venv\Scripts\python.exe .claude\skills\hwde\scripts\gate.py --gate drc_routed <ws>\kicad\<board>.kicad_pcb
.venv\Scripts\python.exe .claude\skills\hwde\scripts\gate.py --gate verify <ws>\kicad\<board>.kicad_pcb
```

## Sources
- LEARNINGS 2026-07-29 [place][gates][routing] place gate + check_decoupling blind to routed
  copper (line 1470)
- LEARNINGS 2026-07-28 [routing][stitch][drc] DRC skips via-drill vs same-net pad drill (line 819)
- check_decoupling.py (GND_STUB_WARN_MM, ground-leg model); stitch_vias.py (candidate model);
  cluster_violations.py (placement domain)
- Measured 2026-08-06 on boards/lumina-carrier: gnd_stub_long 10.1 mm warning (1 finding),
  correctly warning-severity per the P8 digest triage
