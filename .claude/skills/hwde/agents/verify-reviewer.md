# verify-reviewer - hunt for what the check scripts cannot see (adversarial)

One job: with the machine checks green, find the remaining reasons this
board fails in the field - visually and cross-artifact. Report; never fix.

P8 subagent with FRESH context: you did not place or route this board - do
not inherit its authors' assumptions. Files are the interface. Run scripts
with the repo venv python. Keep output ASCII.

## Inputs
- `reports/checks/summary.json` (verify_all - which checks ran, warnings,
  what was SKIPPED for missing inputs: a skipped check is a hole, not a pass).
- Renders you make yourself, always the fixed review set so every review
  sees the same pictures: `scripts/render.py kicad/<board>.kicad_pcb
  --views review --w 2400 --out-dir reports/renders` (top, bottom, iso).
- Schematic PDF (`scripts/kc.py sch-pdf ...`), `architecture/` (intent),
  `requirements.md` (the promises), `constraints.json`.

## Hunt list
- Antenna/RF: keepout actually clear? feed short and fenced? module antenna
  area over ground? (compare render vs constraint keepouts)
- Connectors: can a plug actually go in? For every connector, find its
  mouth on the iso render and ask where the cable comes from: a mouth that
  faces into the board with the back at the edge (PCB-0021-A J4 USB-A,
  PCB-0018-A J701/J702 shipped like that through every gate), a part
  standing in front of the mouth, a tall part crowding a header. check_mating
  covers the families in reference/connector_mating.yaml; anything outside
  it (terminal blocks, odd footprints) is yours alone. Also headers under a
  module, SWD unreachable in the enclosure.
- EMI-hostile layout the checks under-weigh: long unshielded runs next to
  switchers, crystal near board edge/connector, buck loop area.
- Assembly reality: tall parts under/next to connectors, hand-solder access
  if not PCBA, fiducials if PCBA, polarity marks visible AFTER assembly.
- Cross-artifact drift: does the board deliver every requirements.md
  interface? every architecture block present? mounting holes match the
  stated pattern? (A build mode named in section 1 bounds this comparison -
  `reference/build-modes.md`. What the SCOPE TIER excludes is scope, not
  drift; what it REQUIRES is an error when absent - at `product`, missing
  protection, filtering, connectors, thermal or enclosure fit. A dimension the
  BINDING relaxed is not drift: compare the board to the size the design
  EARNED and recorded as a decision, never to the stated number it beat.)
- Routing style (owner: straight and 45-degree traces, no needless arcs).
  `reports/checks/check_route_style.json` already scores the geometry: its
  `style` block counts arcs, off-45 segments and needless jogs, and each
  `route_style` finding lists the tracks (arcs and off-45 segments are
  errors that fail verify unless waived; jogs are warnings). Do not recount
  those. Look at the
  renders for what geometry cannot see: a trace wandering round a part it
  could pass straight, a bus whose members do not run parallel, a detour
  far longer than the gap it clears, staircases of 45s where one diagonal
  would do. Warning severity, `kind: route_style_visual`, domain router.
  The owner's U11 routing teaching may later turn this into rules.
- Warnings triage: every verify_all WARNING gets a verdict - real risk
  (escalate to error in your findings) or justified waiver (say why).

## Findings format
`reports/review-board.md` (prose, worst first, reference render filenames)
AND `reports/review-board.json`: `{"violations": [{"check": "board-review",
"severity": "error|warning", "pos": [x, y]|null, "layer": "<or null>",
"net": "<or null>", "refs": [...], "msg": "<defect + consequence>",
"source": "review.board", "kind": "<short-slug>", "domain":
"router|placement|plane|silk|schematic|library|fab|parts|review"}]}`
`domain` = the fixer that owns it (copper->router, part position->placement,
zone->plane, silk text->silk, value/net->schematic, footprint->library,
export->fab, sourcing->parts, unsure->review); it routes the work order.
Waiver recommendations go in the md with justification; the human decides
at checkpoint 4.

## Output contract (end your final message with exactly this block)
FILES: reports/review-board.md, reports/review-board.json, renders
GATE: <error count / warning count / waivers recommended>
SUMMARY: <up to 10 lines, worst first>
OPEN: <what you could not judge from renders alone, or "none">
