# decoupler_loop

A decoupling capacitor's estimated loop inductance exceeds its class threshold: 0.7 nH/mm of
trace (rail Manhattan distance + ground leg) + 1 nH per via, judged per cap value class
(bulk 30/60 nH, mid 10/20, hf 6/12 warn/error; per-association `max_loop_nh` overrides).

- Emitted by: scripts/check_decoupling.py (kind="decoupler_loop")   Gate: verify (P8, via verify_all.py)
- Fixer domain: placement (cluster_violations.py)   Scripts you may use: place_edit.py, place_metrics.py, render.py
- Fields: pos, net (rail), refs [cap, ic], loop_nh, limit_nh, plus the association's cap/ic/pin.
  Severity ladder: warning at the class warn threshold, error at 2x-class (err) threshold.

## Is it real?
- It is an ESTIMATE from a heuristic (0.7 nH/mm, 1 nH/via), not a field solve - trust the
  RANKING more than the number. A warning a few nH over on a bulk cap is routine; an hf-class
  cap at 2x its limit is a real high-frequency bypass failure.
- Check the association first: decoupling.json is schematic-generation metadata. A cap serving
  a different pin than the metadata says (or a stale refdes) reports kind=metadata_mismatch
  separately - if that fired too, fix the metadata, not the placement.
- The via count is modelled (0 or 2 rail vias + 1 ground via), so a cap on the wrong side of
  the board carries +3 nH of via penalty that no nudge removes - that is a side/flip decision.
- Value-class mismatch: a bulk cap flagged against an hf threshold means the value string
  parsed wrong (multi-token values like "10uF 25V X5R" are handled, but check
  `checked[].loop_nh` and the class before moving anything).

## Fix ladder (cheapest first)
1. Confirm which term dominates: loop_nh = 0.7 * (rail_mm + gnd_leg_mm) + vias. Read the
   association's facts; a long GND leg usually co-fires gnd_stub_long - fix the ground side
   first (one via next to the cap's GND pad kills both).
2. Move the cap closer to its pin: place_edit.py --ops (absolute ops). BEFORE P7 routing the
   place gate + this check are the oracle. AFTER routing has started, any move must be
   validated with kc.py drc / the drc_routed gate - place gate and check_decoupling both
   passed a move that SHORTED lumina-carrier (LEARNINGS 2026-07-29 line 1470).
3. On a routed board a cap move strands its copper: remove + re-route the affected nets
   (route_edit.py removals, then re-route) and refill zones before re-gating. Flag the
   re-route in your summary.
4. Loop dominated by vias (cap on the far side): escalate - flipping a placed-and-routed part
   is a placement-domain decision with re-route cost, not a nudge.
5. Escalate `requires_pipeline_rewind` when the only fix is re-planning the fan-out (e.g. the
   rail reaches the pin through a plane tap far from the cap).

## Do not
- Do not chase single-nH improvements by shuffling a routed board: the estimate's noise is
  larger than that, and every move risks stranded copper for no real gain.
- Do not validate a post-P7 move with this check alone - it does not see routed copper
  (LEARNINGS 2026-07-29 line 1470). DRC is the oracle; this check is the pre-filter.
- Do not edit decoupling.json to relax a threshold just to pass the gate; per-association
  overrides exist for justified cases and need the reason in OPEN.
- Do not move the IC. The cap serves the pin; the IC placement serves the whole board.

## Verify
```
.venv\Scripts\python.exe .claude\skills\hwde\scripts\check_decoupling.py --pcb <ws>\kicad\<board>.kicad_pcb --metadata <ws>\kicad\decoupling.json
.venv\Scripts\python.exe .claude\skills\hwde\scripts\gate.py --gate drc_routed <ws>\kicad\<board>.kicad_pcb
.venv\Scripts\python.exe .claude\skills\hwde\scripts\gate.py --gate verify <ws>\kicad\<board>.kicad_pcb
```

## Sources
- LEARNINGS 2026-07-29 [place][gates][routing] place gate + check_decoupling blind to routed
  copper - the C35 short (line 1470)
- check_decoupling.py (loop model, value classes, metadata_mismatch);
  cluster_violations.py (placement domain); fix_dispatch.py (placement guidance)
- Measured 2026-08-06 on boards/lumina-carrier: decoupler_loop 13.7 nH warning (1 finding),
  correctly warning-severity per the P8 digest triage
