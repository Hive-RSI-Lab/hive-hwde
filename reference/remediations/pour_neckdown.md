# pour_neckdown

A zone FILL on a power net pinches below the IPC-2152 width for the rail's budget somewhere
between its via attachment points: eroding the fill by required/2 splits the vias into separate
components (polygon-erosion equivalent of a medial-axis min-width). A trunk neck throttles the
whole rail even when every track passes.

- Emitted by: scripts/check_current.py (kind="pour_neckdown")   Gate: verify (P8, via verify_all.py)
- Fixer domain: router (cluster_violations.py)   Scripts you may use: route_edit.py, kc.py, render.py, stitch_vias.py, route_cleanup.py
- Fields: pos (a representative point of the SPLIT, not the whole neck), layer, net, neck_mm,
  required_mm, current_a. Severity: error for a declared power entry - plane_fed does NOT downgrade
  it (a trunk neck is real); WARNING with `derived: true` when the entry was synthesized return-net
  coverage (T6: the budget is then a heuristic, max of the declared rails).

## Is it real?
- Check `current_a` first. An override region covering the reported pos re-tests the fill at the
  override current; a declared regulator-feed region that should cover it but does not is an input
  fix, not a copper fix. On a `derived: true` finding the budget is the LARGEST declared rail - the
  return may legitimately spread over parallel paths; judge the geometry before spending copper.
- The neck position is a sample: the fill was eroded until the via set split, and pos is a
  representative point of one component. Render the layer and find the actual pinch (foreign-pad
  antipads and keepout channels are the usual cause) before editing.
- A pour that necks around a fine-pitch connector is usually the fan-in geometry problem: on
  pd-trigger NO legal 1.75 mm VBUS track could reach the USB-C pads at all - the pour IS the fix
  there, and its neck width is what the pad column allows (LEARNINGS 2026-07-28 line 782).
- The 5 A return class (LEARNINGS 2026-07-28 line 832): autorouted GND contact-pad attachments are
  0.2 mm tracks (0.80 A) and one via per pad - the pour lobe repair below is the proven fix.

## Fix ladder (cheapest first)
1. Confirm the input: `checked[]` entry (current_a, dt_c, required_mm_by_layer) vs the
   board-directory constraints.json; `stackup_assumed: true` means the copper weights are
   defaults - fix the stackup block, not the board.
2. Widen the pinch in place: grow the zone polygon (planes_gen sidecar region) or clear the
   obstruction (move a via/track crowding the channel via route_edit.py remove + re-add).
   Refill and re-check: `kc.py drc --refill --save-board` then check_current.py.
3. Pinch caused by foreign pads that cannot move: add a SECOND lobe/zone spanning the neck at a
   distinct priority (planes_gen planes-only sidecar; priority 1 makes the neighbouring pour
   yield; cross-net overlap at distinct priorities is legal - LEARNINGS 2026-07-28 line 832).
   Solid pad connects for high-current pads: the zone must not thermal-relief a 2.5 A pad.
4. Neck is load-bearing but the budget is wrong (parallel return paths, transient-only worst
   case): escalate for an `overrides` region or a corrected current_a in constraints.json -
   constraints are not a router file; put the measured neck width in OPEN.
5. Escalate `requires_pipeline_rewind` when the fix needs new outer-layer copper on a board
   whose escapes are shorter than the via pitch: it cannot be retrofitted at P8
   (LEARNINGS 2026-07-29 line 1588 - 26 of 44 clusters had ZERO feasible positions).

## A pour that fills differently in a scratch copy

`pcbnew.ZONE_FILLER` (and every script that saves through pcbnew) fills with
only the rules it finds beside the board file. A copy in `$TMPDIR` without the
board's `.kicad_pro` and `.kicad_dru` loses the custom clearance rules (a
tight courtyard clearance, say), and a pour through a BGA field can break into
islands. `kicad-cli pcb drc --refill-zones` refills correctly, so DRC can show
0 unconnected while the STORED fill, which the Gerbers use, is open. Check the
stored fill: load the board, `BuildConnectivity()`, and
`GetConnectivity().GetUnconnectedCount(False)` must be 0. Fix: copy the
`.kicad_pro` and `.kicad_dru` beside every scratch copy, or finish in the
project dir with `kicad-cli pcb drc --refill-zones --save-board`.

## Stitching GND through a power pour

`stitch_vias` at a regular pitch (`--pitch auto`) through a power pour on an
inner layer can fail `pour_neckdown` even when the board was clean and DRC
passes: the antipads (about 0.96 mm across) chain along the pitch and narrow
the pour. One reported board went from clean to 3.33 mm and 2.30 mm on two
rails against 3.50 mm needed for 5 A, at a 4.28 mm pitch.

- The reported `pos` is a representative point, not the pinch, so removing vias
  near it does not help, and checkerboard thinning did not either.
- What worked: test each planned via in the pour on its own and keep it only if
  `check_current` still reports no `pour_neck`. That dropped 6 of 278 vias.
  Write the planned positions, apply them one at a time on a scratch copy
  (with the `.kicad_dru` beside it, above), and keep a via only if the neck
  stays clear.
- Do this before the verify gate, which is slow on a big board.

## Do not
- Do not bridge the neck with a rule-width TRACK through the same channel: the DRU width floor
  that applies to tracks is why the pour was used; a zone is width-rule-exempt, a track is not
  (LEARNINGS 2026-07-28 line 782).
- Do not waive a plane_fed pour neck as "advisory like the rest": plane_fed downgrades via
  clusters and segments only - the pour IS the declared trunk, its neck carries the rail.
- Do not refill by hand-editing fill polygons; only `kc.py drc --refill --save-board` writes
  fills the toolchain accepts.
- Do not trust `gate place` or check_decoupling after editing copper - only DRC sees routed
  copper (LEARNINGS 2026-07-29 line 1470).

## Verify
```
bin/pcb-flow check_current --pcb <ws>/kicad/<board>.kicad_pcb --constraints <ws>/kicad/constraints.json
bin/pcb-flow gate --gate verify <ws>/kicad/<board>.kicad_pcb
bin/pcb-flow gate --gate drc_routed <ws>/kicad/<board>.kicad_pcb
```

## Sources
- LEARNINGS 2026-07-28 [routing][kicad][drc] fan-in unmeetable by any track - pour it (line 782)
- LEARNINGS 2026-07-28 [routing][placement] 5 A GND return choke; pour lobe + via field fix (line 832)
- LEARNINGS 2026-07-29 [check_current][gates] plane-fed rail; doubling not retrofittable (line 1588)
- LEARNINGS 2026-07-29 [place][gates][routing] only DRC is an oracle after P7 (line 1470)
- check_current.py (pour_neck erosion + override re-test + derived return coverage);
  planes_gen.py (planes-only sidecar, priority, connect); fix_dispatch.py (router domain)
