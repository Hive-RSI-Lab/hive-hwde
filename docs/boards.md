# Boards built with hwde in other projects

hwde's real boards are built in the projects that need them, not in this
repo: the hi-fi receiver's boards are in hifi-receiver-2's `boards/`. This
table records each one the score (`bin/hwde score`) counts beside the boards
under this repo's `boards/`. A row is the board as its project's main
holds it: the commit whose board passed, and the gate steps it reached (the
same five as `check-all`: `erc`, `drc_routed`, `verify`, `fab`, `dfm`).

The score reads this file, never the other project's clone, so it stays the
same for one commit of this repo. The rows of
[workarounds.md](workarounds.md) name these boards like any other, so a
shim or hand edit a product board needed costs the score its steps, and a
fix in hwde that lets the board drop it raises the score.

**Who writes a row:** the worker who takes a board through hwde's gates in
its own project. When the board passes, add or update its row here with
the commit, and for every board-local script or hand edit that stands in
for a defect in hwde (the "board-local" lines of a board's snags file), add
a row to workarounds.md with a probe, or extend an existing row's `boards`
when it's the same cause. `bin/hwde check-all` re-runs only this repo's
boards; a board here is re-checked by its own project's test.

| board | project | commit | steps | where |
|---|---|---|---|---|
| lite-main | hifi-receiver-2 | 87b6fce | erc, drc_routed, verify, fab, dfm | `boards/lite-main`: 2 layers, 130 parts (USB-C PD, ESP32, codec, 2 x 43 W class-D); its snags are `HWDE-SNAGS.md` |
| core | hifi-receiver-2 | e8fef47 | erc, drc_routed, verify, fab, dfm | `boards/core`: 4 layers, 278 parts (Plus/Ultra core); its board-local scripts are in `kicad/gen/`, listed in its README |

**core's verify under the new checks.** Its row records the verify gate as
passed at e8fef47, under the checks hwde had then. `check_straps` and
`check_antenna` (issue #130) now fail core with errors: the GPIO0 strap and
six TCA9555 pins (review-2 DIGITAL-1/2), and fill under U7's antenna on all
four layers (DIGITAL-19). Those are design findings for hifi-receiver-2 to
fix, not workarounds. The row stays as the commit it names until that
project re-runs verify on its next spin and updates it.

## Known and not yet counted

Workarounds these boards needed that have no row yet, because there's no
probe that could tell when their cause is fixed. A row needs one:

- **core: routing finished by hand.** route_auto left about 40 connections
  open; they were finished with a board-local maze router and rip-up,
  GND-island and via-tap scripts (`kicad/gen/miniroute.py` and the rest).
  A probe needs a dense routing benchmark.
- **core: placement by hand.** place_anneal placed nothing in 22 min on 278
  parts; hand anchors plus a board-local nearest-free-spot placer
  (`kicad/gen/satellites.py`) passed the place gate. Placement is not one of
  the five steps.
- **core: J1 and silk.** `kicad/gen/fix_j1.py` moves the USB-C in from the
  edge and trims its pads; `kicad/gen/silk_clean.py` clears what silk_place
  left.
- **lite-main: route_edit and the antenna.** route_edit neither checks
  clearance nor refills zones (`kicad/gen/refill.py`); no hwde script draws
  a copper keep-out (`kicad/gen/keepout.py`, the ESP32 antenna and the NPTH
  pegs); stitch_vias' hole-to-hole misses (`kicad/gen/fix_holes.py`).
- **lite-main: footprints by hand.** The stacked RCA's courtyard and pegs
  (`lib/aiee.pretty`) and the USB4105's trimmed pads.
- **both: EasyEDA's rate limit.** The CPL model cache in `parts/easyeda` was
  filled by retrying until it was complete (brief 261029-041444 makes dfm
  keep and reuse it).
