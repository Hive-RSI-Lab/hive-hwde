# resize-board - change the board outline after P5

Until U17 the outline was written once (`board_init --outline`) and was final:
changing the size meant rebuilding the board, which threw placement and routing
away. `board_edit.py` edits Edge.Cuts in place, with the same contract as every
other writer - validate, stage, SWIG worker, independent re-parse, atomic swap,
byte-identical rollback.

    board_edit.py --pcb {pcb} --outline 40x30 | fit | keep --workspace {ws}

| form | means |
|---|---|
| `WxH` | resize to W x H mm, anchored at the current top-left (`--anchor center` keeps the centre) |
| `fit` | shrink (or grow) to the content bbox + `--margin`: courtyards, copper, keepout areas |
| `keep` | same bbox, new `--corner-radius` / `--cutout` |

The spec is ABSOLUTE, so re-running the same command is a no-op. The outline is
drawn by the same code `board_init` uses, so an edited board and an initialized
one are the same geometry.

## The shrink-to-fit flow (place first, size after)

This is the point of the script, and the flow a `canonical` learning run takes:

1. `board_init --outline` with a PROVISIONAL outline - generous, not the
   target. Guessing the final size here is what binds placement to a number
   nobody has earned yet.
2. Place (P6) against that room, gate `place`.
3. `board_edit --outline fit --margin M` - the board becomes exactly what the
   placement needs. M is a real clearance, not decoration: it must clear the
   fab profile's copper-to-edge floor (0.3 mm on the JLC profiles; the report
   carries the number it used), and connectors that mate off-board need their
   own room.
4. Re-run `planes_gen` if the board GREW - a zone's outline is a fixed polygon
   drawn when the plane was generated; it does not follow the edge outward, so
   the pour stops at the old boundary until it is re-poured (the report warns).
5. Route (P7). Zones refill as part of the edit, but a re-poured plane needs
   `stitch_vias` again.

Doing it the other way round - route, then resize - works, but every track that
ends up under the fab floor's edge clearance is a refusal you have to fix by
re-routing. Resize before the copper exists where you can.

## Refusals are the feature

`board_edit` never clips. It compares the board's issues BEFORE and AFTER the
proposed outline and refuses (exit 1, nothing applied) on anything the edit
CREATES or worsens:

- a footprint courtyard pushed outside (a part declared on an edge in
  `constraints.json` may overhang its own edge - that is what the declaration
  is for),
- copper or a drill closer to the new edge than the fab profile allows,
- a keepout rule area the new boundary would cut.

A part that ALREADY overhangs stays in `preexisting` and does not block - it is
the board's problem, not the resize's. `--report-only` runs the same analysis
and changes nothing: use it first, read `blocking_summary.refs`, move those
parts (`move`), then resize.

Zone FILLS are deliberately not checked: KiCad re-clips them to the edge at
refill, which `board_edit` runs unless you pass `--no-refill`.

## What it will not do

- **Non-rectangular outlines.** Edge.Cuts is rewritten wholesale, so a shape
  the script cannot restate (a notch it was not told about, a chamfer, an
  interior window, two boards in one file) refuses until you re-state the full
  shape with `--corner-radius` / `--cutout` and pass `--replace-shape`.
- **Interior windows.** A `--cutout` must touch an edge and become a notch;
  an interior window mis-parses as the board outline downstream (board_init
  refuses it for the same reason).
- **Moving parts.** It resizes the board around the placement; it never nudges
  a footprint to make a size fit. That is `move` / `place_anneal`.

## After the edit

`board_edit` records the `outline_change` edit class itself (into `--workspace`,
or the first parent of the board holding a state.json) - do NOT also run
`state.py edit`. The map stales `place`, `drc`, `drc_routed`, `verify`, `dfm`
and the gerber zip, at human_hold 2: an outline change is a fab-visible change,
so summarize it at the next checkpoint (new size, why, what moved).

DRC runs before and after and the edit rolls back if it got worse, so a green
report means the new outline is at least as clean as the old one - it does not
mean the gates are fresh again. Re-run them.
