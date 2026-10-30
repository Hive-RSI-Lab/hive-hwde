# Designing a board with hwde in the Hive

hwde is ihsan-sa/hwde's KiCad design skill. It takes a board from a written
brief to checked KiCad files and a JLCPCB fab package. The skill itself is in
[`.claude/skills/hwde/`](../.claude/skills/hwde/SKILL.md), kept as upstream
has it ([what differs](upstream.md)). This page covers what's different when
an agent runs it inside the Hive.

## Before you start

- Run `hwde check` (`$HIVE_MODULES/hwde/bin/hwde check --quiet`). It must
  exit 0. On first use it builds the skill's Python venv in your HOME from
  `requirements.lock`, using the Hive's uv; that takes about 15 s.
- KiCad 10.0.6, freerouting 2.2.4 and KiCadRoutingTools 0.19.0 (for
  `route_critical` and route_auto's KRT finish) are the Hive's, read-only in
  `/hive/tools` (put there through a warden). Java 25 is the system's.
  `bin/hwde` finds them all, plus KiCad's own python with `pcbnew` and its
  ngspice. If `hwde check` can't find one, ask a warden; don't install KiCad
  yourself.
- In another project's test or score check (a fresh HOME, only `/work` and
  `/hive/tools` mounted, no `$HIVE_MODULES`), run hwde as
  `/hive/tools/hwde/bin/hwde`. That's a published copy: `hwde publish <dir>`
  builds it at `<dir>/hwde` with its own Python 3.14 and the lock's packages
  installed, so it starts with no venv to build and no download, and writes
  `<dir>/hwde.version`. A warden runs it into the Hive's tools folder after a
  change to the module that a check needs. A board's dfm gate still fetches
  LCSC models from EasyEDA (rate-limited, HTTP 403) into a fresh HOME's
  cache, unless the board has an `easyeda/` folder beside its `parts.json`
  (`cpl_verify.default_cache`); commit that folder so a check needs no fetch.
- Boards go in the project you work in, under `boards/<name>/`
  (`HWDE_BOARDS_ROOT`, default `$PWD/boards`). Commit the workspace on your
  branch like any other change. Gate `--commit` isn't needed.

## Running it

- `hwde <script> [args]` runs `.claude/skills/hwde/scripts/<script>.py` with
  the venv python and the `HWDE_*` pins set. The skill's prose says
  `.venv/bin/python scripts/<x>.py`; this is the same thing. With no X
  display it runs the script under its own Xvfb, with GLX off because H0's
  NVIDIA libraries crash Xvfb.
- `eval "$(hwde env)"` puts the same pins in your shell. Its `HWDE_PY` and
  `HWDE_SKILL` are what a board's own generator script (`kicad/gen/*.py`)
  runs with: `PYTHONPATH=$HWDE_SKILL/scripts:$HWDE_SKILL/scripts/lib $HWDE_PY kicad/gen/root.py`.
- Every board with a netlist gets `hwde sim_board --workspace boards/<name>
  --out boards/<name>/reports/sim_board.json`: ngspice over the board's own
  passives and discretes (KiCad 10's bundled libngspice, found by this
  wrapper), with the hand calcs a reviewer would do and a `known_unknowns`
  list. A review answers each known unknown from a datasheet or states the
  margin it rests on; `hwde eng_calc <calc> ...` gives the numbers a written
  margin cites.
- A board that runs firmware gets `hwde check_fw --board boards/<name>`,
  run at every schematic change, not first at bring-up. It runs hive-fw's
  `fw constraints` (with `fw pins` inside it) for each firmware target whose
  board is this one in fw's `targets.json` (or `--target`), reading the
  firmware beside the board in your checkout. It reports fw's own problems
  with fw's code (PIN-NET a firmware pin on the wrong net, STRAP-0/2/12 a
  strap pulled the wrong way at reset, BOARD-* the module's flash or
  PSRAM) and the ones only the board side can see: the ESP32's supply
  budgeted under the radio's peak current in `constraints.json`, an
  input-only GPIO 34-39 nothing drives or pulls, and an I2C line with no
  pull-up. Its `targets[].needs` are what
  a netlist can't settle (antenna keep-out, address straps); weigh each.
  Exit 0 pass, 1 problems, 2 when fw (one with `constraints`) isn't found:
  it looks at `--fw`, `$HWDE_FW`, then hive-fw in `$HIVE_MODULES`. A check
  sandbox sees neither, so it is an agent's check, not a per-PR one.
- Start from `hwde task_router --task "<the ask>" --workspace boards/<name>`
  and follow its recipe (`reference/recipes/<verb>.md`). The whole pipeline
  is `full-run`.
- The skill's subagents are the Agent tool: give each one its role prompt
  (`agents/<role>.md`), the workspace paths, and the `hwde` command line in
  place of the venv python. Record each spawn with `hwde state spawn`.
  Reviewers always get fresh context.

## Checkpoints are reports, not stops

hwde's checks are tools you run, not a stage machine: `set-phase` warns
instead of refusing, the fix-loop and cross-stage budgets count past zero
(`over: true`), `board_init` notes a fixed outline instead of refusing it, and
release (`attest.py`) needs the gates passed, not the person's sign-off. Going
past a default is your judgment; record why with `hwde state decision`.

H1 (architecture), H2 (schematic), H3 and H4 (verified board) are reports you
write and keep going. Bring the person only what is theirs: waiving an
error-severity finding on a board that will be built, a cost or feature trade
the brief didn't settle, and the order itself (H5, money). That goes as a short
packet to the project's lead: the files to read on your branch, a digest of 10
lines or fewer, and the exact question with your recommended answer; the lead
puts it to the person. Don't use a warden request for it, and don't wait on
it: carry on with your recommendation, say so in the packet, and rework if the
answer is no. Record answers with `hwde state human` (a rejected H4 blocks
release until reworked).

## Know what the board doesn't know

Every board keeps `ledger.json`: each fact its design relies on (per BOM code:
JLC stock, the part JLC will really place, whether JLC still sells it; per
part: pin ratings, pinout, footprint), verified with its source and date,
failed, or unknown. The checks fill it in as they run, and one that can't
tell writes unknown. `bin/hwde ledger report --workspace <ws>` lists the gaps.
`bin/hwde ledger set ... --source "..."` records a fact you checked yourself.
A board isn't ready to order (attest refuses, order-ready drops) while any
fact is failed or unknown. Stock is good for 72 hours, so run
`bin/hwde stock_check --workspace <ws>` right before the order packet; the
person's flow is "I order it, it arrives, I plug it in", with no surprise at
JLC's BOM review.

## Every board gets its own docs

A board's `README.md`, at the root of its workspace, says what the board is,
the design decisions and why they were made, how to rebuild and re-check it,
and how to test a built one. It has to make sense to someone who has never
heard of hwde. Write it when the run starts and keep it current at every
stage: a decision recorded in `state.json` belongs in the README the same day.
A run isn't finished until its README matches the board.
[`boards/hwde-led-test/README.md`](../boards/hwde-led-test/README.md) shows
the shape.

## Be critical of the tools

The skill improves the way the rest of the Hive does: by changing it, not by
working around it. When a script, check, prompt or recipe gets in the way or
gets something wrong (a false finding, a missing case, an agent role that left
out a field, a path that only works upstream), note it with the file it lives
in. Fix it in this module if it's small and yours to make. Otherwise file it
as its own change: `hive agent brief --project hive-hwde --title ...
--done ...`. Board-local workarounds are the failure mode. List what you hit
in your account.

A fix to a script goes in with a test under `tests/` (run them with
`eval "$(bin/hwde env)"; $HWDE_PY -m pytest -q tests`), and a line in
[upstream.md](upstream.md) so an update from upstream keeps it.

A workaround you can't avoid gets a row in [workarounds.md](workarounds.md)
naming the gate steps it touched, and a probe in `tests/workarounds/` that
exits 0 while its cause stands. That is what the project's score counts
(`bin/hwde score`), and the test (`bin/hwde test`) fails while a row and its
probe disagree. A shim in `bin/hwde` checks its row (`wa <id>`), so it runs only
while it's listed. When you fix a cause, its probe starts exiting 10: take
the workaround out, delete the row, keep the probe as a guard, and the score
goes up. A board you build in another project (a product's `boards/`) counts
the same way: when it passes, record it in [boards.md](boards.md) with the
commit and the steps, and give each of its board-local workarounds a row
here, or add the board to the row of the same cause.

Record each run's wall time per stage (the `phase` events in
`state.json` history) and the gate attempts and fix loops per gate. That way
a later board shows whether the module got faster or cleaner.

## Finishing a dense board's routing

`route_auto` ends with `route_finish`, so a dense board needs no copper
scripts of its own. After the Freerouting ladder (and KRT, when it is
found), `route_finish` rips what DRC names in a short or clearance error,
then routes what is still unconnected. It is a grid maze router that works
at the board's own rules: it joins pads, stubs and cut-off pour pieces (with
a via into the plane), and frees a walled-in pad by ripping the fewest
signal nets in the way. It never rips pour nets or nets with a width rule.
Run it alone on any routed board (`hwde route_finish --pcb ... --dry-run`
first). Its `failed` connections have no path at the rules, which makes
them a placement or topology finding.

On hifi-receiver-2's core (278 parts, 4 layers), with every signal track
ripped, one Freerouting rung left 54 unconnected and 16 DRC errors.
`route_auto` with `route_finish` then took it to a handful unconnected with
no DRC errors. What remained was a 0.4 mm-pitch QFN pocket walled in by a
VBUS_IN track and a 77 mm long haul.

## What it never does

It never orders or pays. P10 (quote, order, payment) is the person's: stop
after the `dfm` gate with the package in `fab/` (gerber zip, BOM.csv,
CPL.csv, prebuy.csv) and say so. JLCPCB's API keys never reach a sandbox, and
nothing in the Hive should ask for them.

## What doesn't work here yet

- `report_gen` (the design-doc PDF) and the board guide need the
  pdf-material-builder house style, which isn't here. They fall back to
  `--tex-only`, and the skill says that must never gate a run.
- DigiKey/Mouser price lookups need API keys and stay off.
- The knowledge records cite manufacturer PDFs that the module doesn't carry
  (see NOTICE). `bin/hwde` points `HWDE_KNOWLEDGE_SOURCES` at the hwde
  project's clone (`$HIVE_PROJECTS/hwde`) when there is one.

## The first board

[`boards/hwde-led-test/`](../boards/hwde-led-test/) is the first board hwde
made in the Hive: a 2-layer, 13.4 x 10.5 mm board with a 5 V header, a bulk
cap and a green LED with its resistor. It went through every stage up to P9,
and ERC, place, drc_routed, verify and dfm all pass. Read its `log/P*-digest.md`
for what each stage did and `LEARNINGS.md` for what it taught.
