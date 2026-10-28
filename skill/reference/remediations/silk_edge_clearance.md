# silk_edge_clearance

Silkscreen ink overlaps an Edge.Cuts item: KiCad's "Silkscreen clipped by board edge". The fab prints it and
the router mills through it, leaving a half-glyph or a smeared outline.

- Emitted by: kicad-cli DRC, normalized by scripts/kc.py (kc.py:90-119)   Gate: drc_routed (P7, error+warning,
  gates.yaml:60-68). Severity is ALWAYS warning (159/159 across the committed boards), so the P6 `drc` gate
  (error only) never fails on it and board_init exempts it outright.
- Fixer domain: silk (cluster_violations.py:87)   Scripts you may use: place_edit.py, render.py
- Fields on the violation: check, severity, msg ("Silkscreen clipped by board edge"), pos, layer (always
  Edge.Cuts), net (always null), refs (the silk OWNER only; the edge carries no ref), items[0] = edge item {msg
  "Segment|Rectangle|Arc on Edge.Cuts", pos, uuid}, items[1] = offender {msg "Segment|Circle of <REF> on
  F.Silkscreen" or "Reference field of <REF>" - the refdes form names NO layer, pos, uuid}. Top-level
  `pos`/`layer` are items[0]'s - the EDGE, not the silk: measured pos [119.58, 60.13] for silk at [114.13,
  88.13] (lumina-carrier/work/p6/drc_r1.json). items[1] tells you what to move; always exactly 2 items.

## Is it real?
- Yes at P7: min_silk_clearance is 0.0 in all four boards/*/kicad/*.kicad_pro:152, so this is actual clipping,
  not a margin miss; min_copper_edge_clearance (:145, 0.3-0.5 mm) is copper-only (LEARNINGS 1821).
- Blind the other way: at a 0 mm rule silk 0.01 mm inside the edge passes and still smears at the mill. The P8
  solver demanded EDGE_MARGIN = 0.10 mm (the p8/silk/solve script in the boards repo, line 28); zero means "not clipped", not "clean".
- Bad input, not a board defect: at board_init parts sit on a temporary shelf, so hits there are noise and are
  classed transient (board_init.py:228-232). pd-trigger's 14 init hits were an undersized outline; the fix was
  45x25 -> 48x30 (log/P5-digest.md:2).
- One EDGE item carries several violations sharing items[0].uuid (4 of the 5 in drc_r1.json), one per clipped
  silk item; `net` is null and clustering keys on (net, kind) then splits on EDGE-pos proximity
  (cluster_violations.py:146,105-122), so one cluster can span a whole edge. items[1].uuid is the work list.

## Fix ladder (cheapest first)
1. Classify each items[1].msg: "Reference field of <REF>" -> step 2; "Segment/Circle of <REF>" -> step 3.
2. Refdes: place_edit.py --pcb <board> --ops ops.json with {"op":"move_text","ref":"R1","field":"reference",
   "x":X,"y":Y} (+ optional "deg"; place_edit.py:56,98) - absolute board coords, atomic, self-verified. Size
   the target off the INKED box (size_h + thickness), not GetTextBox: 1.162 vs 1.6965 mm at size 1.0 /
   thickness 0.15 - GetTextBox over-constrains ~0.27 mm per side and makes solvable targets look impossible
   (1821). Keep >= 0.10 mm off the outline and clear of the part's OWN silk outline (1986).
3. Footprint-internal silk over the edge: nudge the FOOTPRINT inboard with place_edit move/place. usb-buck's J1
   poked 0.05 mm past the edge because its silk reaches |local y| 4.10 + width/2 (687). UNROUTED board only:
   on a routed one the nets must be ripped first (route_edit remove by uuid) and the move orphans pad stubs and
   stitching vias into track_dangling / via_dangling (1750) - route_edit is not yours, escalate (fixer.md:38).
4. Neither possible: narrowing the silk stroke buys half the narrowing per side, coordinates byte-identical
   (0.25 -> 0.20 on C0603 = 0.025 mm/edge; JLC floor 0.15 mm) - a LIBRARY edit (lib/EDITS.md), not yours (628).
5. Escalate with the ref list and measured overhang (outline too small / part must leave that edge). Outline
   changes belong to board_init: radius is clamped to the mounting-hole inset (927), a hand-added inner
   Edge.Cuts gr_rect silently BECOMES the outline (964), and an arc-cornered outline can parse as POLYGON EMPTY
   with no error (908).

## Do not
- Do not read top-level `pos`/`layer` as the silk's location/layer - they are the edge item's - and do not move
  or trim the Edge.Cuts item; no allowed script owns the outline.
- Do not hand-sum a text angle: a footprint text field's stored angle is ABSOLUTE, its position local (1750).
- Do not declare it fixed on check_silk: it has no edge rule (kinds silk_over_pad / silk_illegible / silk_thin,
  check_silk.py:303-321). Do not stop at 0 clipped either; leave >= 0.10 mm or the next nudge re-opens it.

## Verify
```
.venv\Scripts\python.exe .claude\skills\hwde\scripts\place_edit.py --pcb boards\<name>\kicad\<name>.kicad_pcb --ops work\silk_ops.json
.venv\Scripts\python.exe .claude\skills\hwde\scripts\kc.py drc boards\<name>\kicad\<name>.kicad_pcb --parity --all-track-errors --out work\drc_after.json
.venv\Scripts\python.exe .claude\skills\hwde\scripts\gate.py --gate drc_routed boards\<name>\kicad\<name>.kicad_pcb
.venv\Scripts\python.exe .claude\skills\hwde\scripts\render.py boards\<name>\kicad\<name>.kicad_pcb --views top --out-dir work\silk_render
```

## Sources
- LEARNINGS 2026-07-29 [silk][place_edit][kicad] inked box vs GetTextBox (1821); [parts][silk] refdes must
  clear own silk (1986); 2026-07-30 [place_edit][placement][silk] moving a part on a routed board (1750)
- LEARNINGS 2026-07-28 [placement][drc][silk] Reference 4 mm off-origin + J1 mouth (687); [easyeda2kicad][drc]
  narrow the stroke (628); [layout] radius vs hole inset (927); [geom][layout] inner gr_rect becomes the
  outline (964), arc endpoints -> POLYGON EMPTY (908)
- kc.py:90-119; board_init.py:225-233; check_silk.py:303-321; place_edit.py:54-56,98; gates.yaml:52-68;
  cluster_violations.py:87,105-122,146; fix_dispatch.py:88-102; lumina-carrier/work/p6/drc_r1.json +
  work/p8/silk/solve script lines 11-16,28 (boards repo); pd-trigger/log/P5-digest.md:2; agents/fixer.md:38-41
