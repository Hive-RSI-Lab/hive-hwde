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
- KiCad 10.0.6 and freerouting 2.2.4 are the Hive's, read-only in
  `/hive/tools` (put there through a warden). Java 25 is the system's.
  `bin/hwde` finds them all, plus KiCad's own python with `pcbnew` and its
  ngspice. If `hwde check` can't find one, ask a warden; don't install KiCad
  yourself.
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

## What it never does

It never orders or pays. P10 (quote, order, payment) is the person's: stop
after the `dfm` gate with the package in `fab/` (gerber zip, BOM.csv,
CPL.csv, prebuy.csv) and say so. JLCPCB's API keys never reach a sandbox, and
nothing in the Hive should ask for them.

## What doesn't work here yet

- `route_critical` and KRT's finishing pass need KiCadRoutingTools 0.19.0.
  It's now in `/hive/tools/kicad-routing-tools/KiCadRoutingTools-0.19.0`,
  but nothing points the skill at it yet (brief 261028-141924). A board with
  diff pairs, high-current power or RF needs it.
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
