# silk_overlap

Two silkscreen items collide ("Silkscreen clearance") - here nearly always a refdes label on a
neighbour's label or body outline. Warning severity: never fails P6 `drc`, always fails `drc_routed`.

- Emitted by: kicad-cli DRC, normalized by scripts/kc.py (kc.py:55)   Gate: drc_routed (gates.yaml:60-67; `drc` is error-only)
- Fixer domain: silk (cluster_violations.py:86)   Scripts you may use: place_edit.py, render.py (fix_dispatch.py:88-101)
- Fields on the violation: `pos` [x,y] mm, `refs` (1 or 2 refdes), `severity` "warning", `source` "drc", `layer` "F.Silkscreen"|null, `net` null, `items[]`={`msg`,`pos`,`uuid`}. The classifier is `items[].msg`: "Reference field of <REF>" vs "Segment/Arc/Circle/Polygon of <REF> on F.Silkscreen". Across the committed boards: 530 Reference-field items, 600 outline-graphic items; 399 violations with 2 refs, 166 with 1.

## Is it real?
- Real geometry, wrong phase. At board_init/P5 self-check every 2-ref silk_overlap is reported
  transient (board_init.py:225-233) - parts still sit on the shelf. Fix silk only once placement final.
- 1-ref cases split two ways. Own outline vs own Reference field IS a real, move_text-fixable finding
  (LEARNINGS 1821). Own `fp_text user` marks vs own body outline is a LIBRARY defect: the pd-trigger
  DIP switch ships 8 out of the box, all hidden under the part once assembled (LEARNINGS 712b) ->
  librarian edit (approval + lib/EDITS.md), never board text; gate.py has no waiver filter.
- The number is trustworthy (DRC measures the inked stroke polygon); a pre-flight built on
  `GetTextBox()` is not - 1.6965 mm vs the 1.162 mm DRC tests at size 1.0 / thickness 0.15, over-
  constraining 0.27 mm per side so solvable labels look infeasible (LEARNINGS 1821).
- Bad input that fakes a mass outbreak: lib_pull parks EVERY refdes at a blanket (0,-4.0) mm
  regardless of part size (LEARNINGS 1958) - the source of the lumina-carrier/usb-buck bulk. If
  lib_refdes_norm.py never ran on that lib, this cluster is a symptom, not the disease.

## Fix ladder (cheapest first)
1. Classify from `items[].msg`: label-vs-label, label-vs-outline (2 refs), own-outline-vs-own-label
   (1 ref), or fp_text-user-vs-own-outline (library -> step 5). Fix nothing before this.
2. Build the obstacle set from KiCad's own model, not a hand transform: bundled python +
   `TransformShapeToPolygon` / `TransformTextToPolySet`; read-only probe to copy is
   the boards repo's lumina-carrier/work/p8/silk/probe_geom script. A text field's stored `at` POSITION is local but
   its ANGLE is ABSOLUTE - adding the footprint angle mis-rotates the obstacle (LEARNINGS 1750).
3. Move labels with place_edit `move_text` (`{"op":"move_text","ref":R,"field":"reference","x":..,
   "y":..,"deg":..}`, x/y in ABSOLUTE board mm, place_swig.py:19-23). What took lumina-carrier from 95
   mis-attributed refdes to 3 on its first clean DRC run (LEARNINGS 687, 1821): offer BOTH text angles on ALL FOUR sides, score
   `(min(clearance,0.30), -distance_to_own_silk+pads)`, most CROWDED parts first (descending
   neighbour count within 4 mm). Targets must clear the part's OWN silk, not just pads:
   `min(pad_top, silk_top) - margin - inked_h/2` (LEARNINGS 1986). Apply in batches, re-run DRC each.
4. Only if no label position exists (channel under ~4 mm): relieve it structurally - move the
   LOOSEST-constrained part via place_edit place/move within placement legality. On a routed board
   rip the affected nets FIRST, then move, then re-route (LEARNINGS 1750), or you trade silk warnings
   for track_dangling/via_dangling ones.
5. Escalate: "library defect - <FOOTPRINT> intra-footprint silk collision, needs a librarian
   edit" for the 1-ref fp_text class; "requires_pipeline_rewind: run lib_refdes_norm.py --lib
   <dir>.pretty before board_init" if the whole board carries (0,-4.0) offsets (LEARNINGS 1958).

## Do not
- Do not treat place_edit / place_metrics legality as silk legality: they have NO silk model; a
  courtyard-legal, copper-clean move landed 39 silk warnings (LEARNINGS 1750).
- Do not prove the fix with check_silk.py: it covers silk-over-pad and legibility only, never overlap
  (check_silk.py:1-11). Only `kc.py drc` decides this check (LEARNINGS 628, 712c).
- Do not raw-edit the .kicad_pcb to nudge a label, and do not fix intra-footprint silk on the board:
  place_edit owns text writes, and a library defect returns with the next board off that library.

## Verify
```
.venv\Scripts\python.exe .claude\skills\hwde\scripts\place_edit.py --pcb boards\<ws>\kicad\<board>.kicad_pcb --ops work\silk\ops.json --out-report work\silk\apply.json
.venv\Scripts\python.exe .claude\skills\hwde\scripts\kc.py drc boards\<ws>\kicad\<board>.kicad_pcb --out work\silk\drc.json
.venv\Scripts\python.exe .claude\skills\hwde\scripts\gate.py --gate drc_routed boards\<ws>\kicad\<board>.kicad_pcb --out work\silk\gate.json
.venv\Scripts\python.exe .claude\skills\hwde\scripts\render.py boards\<ws>\kicad\<board>.kicad_pcb --views top --out-dir work\silk
```
Read the render back: a DRC-clean label nearer a neighbour than its own part is still wrong, and
nothing in the pipeline catches that (LEARNINGS 1958).

## Sources
- LEARNINGS 2026-07-28 [easyeda2kicad][drc] silk-on-pad CORRECTION + fix recipe (628); Reference
  parked 4 mm off-origin [placement][drc][silk] (687); DIP switch ships 8 silk_overlap (712)
- LEARNINGS 2026-07-30 [place_edit][placement][silk] moving a footprint on a ROUTED board (1750)
- LEARNINGS 2026-07-29 [silk][place_edit][kicad] GetTextBox 1.70 vs inked 1.16 mm (1821);
  [parts][silk] blanket (0,-4.0) refdes offsets (1958); refdes must clear OWN silk (1986)
- .claude/skills/hwde/scripts/board_init.py:225-233, lib/place_swig.py:19-23, check_silk.py:1-11
- boards repo: lumina-carrier/work/p8/silk/probe_geom script, lines 1-40
