# mating_model_reversed

A horizontal connector whose copper faces its edge but whose 3D model lies
behind the contact pads, so every render (and every review of a render)
shows the mouth facing into the board. Error severity: fails the P6 `place`
gate and the P8 `verify` gate (check_mating).

- Emitted by: scripts/lib/matinglib.py, through place_metrics.py (family
  `mating`) and check_mating.py.
- Fixer domain: library. Scripts: lib_pull.py, fp_verify.py, render.py.
- Fields: `connector` (ref), `mouth` (where the copper puts the mouth, board
  frame), `model_mouth` (where the render shows it), `model_span_mm` (how far
  the model reaches behind and ahead of the contact pads along the copper's
  mouth; ahead is under 1 mm when this fires).

## Is it real?
- The copper is read first and trusted: the contact pads are the back of the
  housing, and the courtyard and silk reach toward the mouth. Check that
  against the datasheet drawing before touching anything.
- Only the model's bounding box is read, so a model pushed back by a wrong
  offset reads like one turned round only when it sits wholly behind the
  pads. A side render settles it: `render.py --views left,right` shows the
  mouth face (an opening) or the back (pins, a closed face).
- A connector with no .wrl beside the board is never failed; its fact has
  `model_span_mm: null`.

## Fix ladder
1. Model turned 180 degrees (PCB-0023-A J101/J102/J501, an edge-launch SMA
   with rotate (0 270 180)): change the model's rotate in the library
   footprint so the mouth points along the copper's mouth (for that SMA,
   flip the z rotation by 180), re-pull it onto the board, render a side
   view to confirm.
2. Copper turned instead (the datasheet puts the mouth where the model
   does): that is a footprint fault, so fix the footprint with lib_pull.py
   and fp_verify.py, then re-place the part: flag
   `requires_pipeline_rewind`.

## Do not
- Rotate the placed part to make the render look right: the copper was
  right, and turning it makes the board wrong.
- Pass it because the top render "looks fine" at a glance: PCB-0023-A went
  to the owner with three SMAs drawn facing into the board.

## Verify
- `scripts/check_mating.py --pcb <board>`: status pass, and the connector's
  fact shows `model_span_mm` reaching several mm ahead.
- Side render shows the mouth face at the board edge.

## Sources
- Owner, 2026-10-06, on the PCB-0023-A highlights doc: "SMAs backwards. I
  thought we were doing checks for this kinda thing".
- learnings.d/2026-10-06-edge-sma-named-like-a-vertical-one.md
- .claude/skills/hwde/scripts/lib/matinglib.py - model_span_mm,
  model_reversed
