# silk_over_copper

KiCad DRC found silk printed into a pad's mask aperture ("Silkscreen clipped by solder mask"); ink on bare pad copper blocks solder wetting and hides the joint.

- Emitted by: kicad-cli DRC via kc.py   Gate: `drc_routed` (P7, err+warn, max 0). NOT the P6 `drc` gate:
  every observed instance is severity `warning` (319/319 across `boards/**/*.json`).
- Fixer domain: silk   Scripts you may use: place_edit.py, render.py
- Fields on the violation: `pos` (silk point), `layer` (F/B.Silkscreen), `net` (the PAD's net), `refs` (union of both items),
  `items[0]` = the silk ("Segment of J1 on F.Silkscreen" + uuid), `items[1]` = the pad ("PTH pad 19
  [/poe/SHIELD] of J1" + uuid). Read BOTH: same ref = library defect, different refs = label/placement.

## Is it real?
- Real but NARROW: this rule is silk vs pad mask apertures only; tracks and vias never triggered it across
  283 sampled violations, so a refdes on a bare track is invisible here (LEARNINGS 1831).
- Zero here is not silk-clean, and neither is `check_silk` = 0: its "pad centre covered OR >=50% of pad"
  rule found 12 where DRC found 68 on the same board (LEARNINGS 568).
- Opposite error too: stroke-edge-to-copper geometry sees overlaps DRC does not - of four library silk dots
  on pad 1, only ONE fired (LEARNINGS 628-636). Prove silk with `kc.py drc`, never with a model.
- Bad input, not board: easyeda2kicad parks Reference at a blanket (0,-4.0) mm (LEARNINGS 1958) and ships
  sub-0.15 mm F.SilkS artifact dots on pad 1 (LEARNINGS 615-624).
- Read the live `min_silk_clearance` in `boards/<b>/kicad/<b>.kicad_pro` (lumina-carrier:152 = 0.0); "0 ->
  fires on actual clipping" is verified for silk_edge_clearance only (1833). 573's 0.25 is one board's recipe.

## Fix ladder (cheapest first)
1. Different refs and the silk is a Reference/Value field: move the label.
   `place_edit.py --pcb <b>.kicad_pcb --ops ops.json` with
   `{"op":"move_text","ref":"R12","field":"reference","x":<mm>,"y":<mm>,"deg":<opt>}` (board-frame absolute,
   idempotent, atomic rollback). Size off the INKED box - 1.162 mm at size 1.0 / thickness 0.15, not
   GetTextBox's 1.6965 (LEARNINGS 1821-1824). Both angles on all four sides, crowded parts first (692-695).
2. Still colliding: relieve structurally. A 3-char refdes at size 1.0 needs ~3.5 x 1.75 mm, so nothing fits
   in a channel under ~4 mm (LEARNINGS 696-699). Nudge the loosest neighbour with `place_edit.py` `move` -
   only if the board is UNROUTED (step 4).
3. Same ref (own silk on own pad): escalate as a library edit. Measured recipe: delete the artifact circle;
   where the outline still sits under the bar NARROW the stroke (0.25 -> 0.20; 0.15 is JLC's floor) rather
   than move coordinates (LEARNINGS 628-644). Needs approval + `lib/EDITS.md`; `lib_refdes_norm.py` is the
   systemic fix and runs before board_init. Neither is in your whitelist.
4. Routed board needing a footprint move: escalate. Order is rip the nets first (`route_edit` remove by
   uuid), then move, then re-route; a move also orphans GND stubs and stitching vias into `track_dangling`
   / `via_dangling` that fail `drc_routed` (LEARNINGS 1750-1766). route_edit is router domain, not yours.
5. Escalate: name the class (cross-ref label / cross-ref placement / library defect / routed move), the refs
   from BOTH item strings, and the live `min_silk_clearance`. Do not waive - this is a real fab defect.

## Do not
- Do not trust `check_silk.py` `refs`: it names the PAD's owner, not the silk's (LEARNINGS 571).
- Do not hand-roll a text box: a field's stored `at` ANGLE is absolute while its POSITION is local; adding
  the two mis-rotates every rotated label (LEARNINGS 1767-1770).
- Do not move a part on a routed board before ripping its nets (LEARNINGS 1765).
- Do not read placement legality as silk legality: place_edit/place_metrics have no silk model, and a
  courtyard-legal move landed 39 silk warnings (LEARNINGS 1750-1754, 467-469).
- Do not diff board files by hash - KiCad regenerates UUIDs every save (place_edit.py:22).
- Do not push silk stroke below 0.15 mm, or delete silk, without a library approval.

## Verify
```
.venv\Scripts\python.exe .claude\skills\hwde\scripts\place_edit.py --pcb boards\<b>\kicad\<b>.kicad_pcb --ops ops.json --out-report work\silk\edit.json
.venv\Scripts\python.exe .claude\skills\hwde\scripts\kc.py drc boards\<b>\kicad\<b>.kicad_pcb --parity --all-track-errors --out work\silk\drc.json
.venv\Scripts\python.exe .claude\skills\hwde\scripts\gate.py --gate drc_routed boards\<b>\kicad\<b>.kicad_pcb --out work\silk\gate.json
.venv\Scripts\python.exe .claude\skills\hwde\scripts\render.py boards\<b>\kicad\<b>.kicad_pcb --views top --out-dir work\silk
```
Read the render back. Iterate in a scratch copy of pcb+pro+dru+sch - reproduces `drc_routed` exactly at
~3.4 s/run, zero risk to the live board (LEARNINGS 1838).

## Sources
- LEARNINGS 2026-07-29 [silk][place_edit][kicad] inked box 1.16 vs GetTextBox 1.70 (line 1821)
- LEARNINGS 2026-07-28 [easyeda2kicad][drc] silk-on-pad dot fix recipe (628); library silk under 0.25 mm
  (615); [placement][drc][silk] Reference parked 4 mm off-origin (687)
- LEARNINGS 2026-07-30 [place_edit][placement][silk] moving a part on a ROUTED board (1750);
  2026-07-29 [parts][silk] blanket (0,-4.0) refdes (1958)
- LEARNINGS 2026-07-27 P6 gate is courtyard-blind (561); 2026-07-23 courtyard packing silk-blind (467)
- kc.py:12-22, lib/place_swig.py:19-22, check_silk.py:43,276-285, gates.yaml:52,60, boards/lumina-carrier/reports/drc-place.json:1017, boards/lumina-carrier/kicad/lumina-carrier.kicad_pro:152
