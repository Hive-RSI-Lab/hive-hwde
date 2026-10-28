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

## Sign-offs: H1, H2, H4

The skill stops for the person at H1 (architecture), H2 (schematic) and H4
(verified board). In the Hive, each sign-off is a short packet to the
project's lead: the files to read on your branch, a digest of 10 lines or
fewer, and the exact question with a recommended answer. The lead puts it to
the person and sends back their answer. Don't use a warden request for a
sign-off. If you go on while a sign-off is open, say so in the packet, and
rework if the answer is no. Record answers with `hwde state human`.

## What it never does

It never orders or pays. P10 (quote, order, payment) is the person's: stop
after the `dfm` gate with the package in `fab/` (gerber zip, BOM.csv,
CPL.csv, prebuy.csv) and say so. JLCPCB's API keys never reach a sandbox, and
nothing in the Hive should ask for them.

## What doesn't work here yet

- `route_critical` and KRT's finishing pass need KiCadRoutingTools 0.19.0,
  which isn't in `/hive/tools`. A board with diff pairs, high-current power
  or RF needs it; ask a warden to add it the way KiCad was added.
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
