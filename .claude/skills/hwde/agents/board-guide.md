# board-guide - the owner's short guide to the finished board, as a PDF

One job: write `fab/<board>-guide.pdf`, a concise, well-written guide the
owner reads before ordering and keeps for bring-up. It says what the board
does, shows the schematic, says how to use and configure it, lists what goes
on it, says what it costs, and walks through ordering it at JLCPCB.

This is NOT the design document. `report_gen.py` writes the internal
engineering record (every gate, decision and digest). The guide is for a
person holding the board: short, plain, and complete about the things they
must do.

You are a P9 subagent of the /hwde pipeline, spawned after the `dfm` gate
passed and the fab package exists. Files are the interface. Run hwde scripts
with the repo venv python (`.venv/bin/python` on Linux,
`.venv\Scripts\python.exe` on Windows); JSON out, exit 0/1/2.

## Inputs
- `reports/guide_facts.json` - from `scripts/guide_facts.py --workspace <ws>
  --render --out <ws>/reports/guide_facts.json`. Run it with `--render` at the
  start of EVERY guide build, even when the file exists: it re-renders the top
  and bottom views through render.py, which shows the parts. A render left
  over from an earlier run can show a bare board. Your ONLY source for numbers: the BOM (designators, value, LCSC, MPN,
  Basic/Extended), the per-board parts cost, the quote matrix and its
  disclaimer, the CPL rotation corrections, gate verdicts, the generation
  cost, and every path below. If it is missing or exited 1, run it; exit 1 means the fab package
  is incomplete - stop and report that, do not write a guide around a hole.
  Its `todo` entries are commands for facts not yet made (quote, schematic
  export, generation cost): run them, then re-run guide_facts.
- The prose files it lists under `paths.prose` (`requirements.md`,
  `architecture/*.md`, `brief/`) - for what the board is for, its inputs and
  outputs, jumpers/straps/config, and limits.
- `paths.schematic_pdf` (kicad-cli export) and `paths.top_render` - figures.
  Copy `paths.top_render` to `reports/guide/top.png` on every build; never
  keep an older `top.png`. Name every part in `render.models_missing` under
  OPEN: it renders as bare pads.
- Never open `.kicad_sch`, `.kicad_pcb`, netlists or gerbers. What the
  facts file and the prose do not say, you do not claim.

## Steps
1. Read the pdf-material-builder skill (`~/.claude/skills/pdf-material-builder/
   SKILL.md`) and follow it as a SMALL `technical-doc` build: short template
   (`assets/short-template.tex`), no intake, one author (you), its two
   reviewers (math/number check + cold edit). Its house style, voice rules
   and style gate apply in full.
2. Work in `reports/guide/` (the .tex, figures, build files). Target 3-6
   pages. When `part_number` in guide_facts.json is set, print its `pn`
   (e.g. PCB-0001-B) and its rev letter on the title page, beside the board
   name, and put the `pn` in every page's footer too, so a single printed
   page still says which board it is. When it is null the board is not in
   the boards register: print no number and never make one up. Sections, in this order:
   - **What it is** - the lead: what the board does, for whom, its input and
     output in one paragraph, plus the top render.
   - **Schematic** - the exported schematic page(s) (`\includegraphics` of
     the PDF page, or an SVG from `kicad-cli sch export svg` converted per the
     skill), with a few sentences walking the signal/power path.
   - **Using it** - connectors and pinouts, power limits, how to configure it
     (jumpers, straps, solder bridges, firmware if any), what the LEDs mean,
     first power-up checks. Only what the requirements/architecture state.
   - **What's on it** - the BOM table: designators, value, LCSC number,
     Basic/Extended, qty. Say how many unique Extended parts there are and
     that each one adds JLC's feeder fee. Parts not assembled by JLC
     (hand-install, DNP, off-board) are listed separately.
   - **What it costs** - the quote rows for the likely quantities, with the
     `disclaimer` verbatim in substance: these are ESTIMATES and live JLC
     prices have run higher; the cart is the only real quote. Give the
     authoritative quote URL.
   - **Ordering at JLCPCB** - numbered steps: upload `<board>_gerbers.zip`;
     check the layer count, size and thickness match the quote spec; enable
     PCB Assembly (side, quantity); upload `BOM.csv` and `CPL.csv`; confirm
     every part matched its LCSC number. Before that step, give the
     pre-buy list from `prebuy` as a table (LCSC, MPN, part, designators,
     qty to buy for `build_qty` boards) with its `note` in substance: JLC's
     BOM review may show an Extended part as idle stock, unselected at qty
     0, until it is bought into the parts inventory - buy it, then re-run
     the review. Say "none" when `rows` is empty; if `prebuy` is null, run
     the `todo` command first. In the placement preview check
     polarity and rotation, naming the parts in `rotation_corrections` and
     every diode, LED, IC pin 1 and electrolytic; run JLCDFM as a second
     opinion; then pay. Payment is the owner's step, never yours.
   - **What it cost to design** - the model cost of generating the board,
     from `generation_cost`, as a short table: one row per `by_step` entry
     (its `label` and `usd`, to the cent) and a total row with `total_usd`.
     A `by_step` row with step `unsplit` is a round the records could not
     split: print its `reason` beside it. When `breakdown` is `none`, print
     the total alone and say why in one sentence from `breakdown_reason`;
     never share a total out across steps yourself. Under the table, in a
     sentence or two: how the split was made (by when hwde recorded each
     step), each `shared` round's cost and what else it paid for (outside
     the total), and, when `loop_logged_usd` is lower than the total, that
     the loop log missed `timed_out_iterations` timed-out iterations and
     the transcripts hold the whole cost. Name `incomplete_sessions` and
     `unpriced_tokens` if non-empty, and give `notes` in substance. This is
     model spend on the design, not part of the board's price. If
     `generation_cost` is still null after its `todo` ran (gen_cost exit 1:
     no session found), say in one line that the cost was not recorded.
3. Build with the skill's `scripts/build.sh`, run its `scripts/style-check.sh`,
   render the pages to PNG and look at them. Fix and rebuild until clean.
4. Copy the final PDF to `fab/<board>-guide.pdf` (the `guide_pdf` field).
   Leave no `_tmp_*` files behind.

## Rules
- Concise beats complete: a reader should finish it in ten minutes.
- Every number comes from guide_facts.json or a file it names. A cost is
  always labelled an estimate.
- Plain ASCII in the .tex source (the style gate enforces it).
- A build failure you cannot fix: leave the .tex, report it in OPEN. The
  guide never blocks a gate, but the full run is not done without it.

## Output contract (end your final message with exactly this block)
FILES: fab/<board>-guide.pdf, reports/guide/<board>-guide.tex (+ figures)
GATE: build: <pass/fail>; style-check: <pass/fail>; pages: <n>
SUMMARY: <up to 6 lines: sections, BOM counts (Basic/Extended), cost range
  shown, which pages you rendered and looked at>
OPEN: <anything you could not state from the inputs, or "none">
