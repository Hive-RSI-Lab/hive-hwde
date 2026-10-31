# Designing a board with hwde in the Hive

hwde is the Hive's KiCad design module. It takes a board from a written
brief to checked KiCad files and a JLCPCB fab package. The way through is
[playbook.md](playbook.md); the agent roles are in [`agents/`](../agents/),
the scripts in [`scripts/`](../scripts/) and their tables, recipes and
knowledge in [`reference/`](../reference/). It began as ihsan-sa/hwde's Claude
Code skill ([where it came from](upstream.md)), but it isn't a skill: nothing
here is loaded by `/hwde`, and nothing needs to stay as upstream has it. This
page covers how an agent runs it inside the Hive.

## Before you start

- Run `hwde check` (`$HIVE_MODULES/hive-hwde/bin/hwde check --quiet`). It must
  exit 0. On first use it builds the module's Python venv in your HOME from
  `requirements.lock`, using the Hive's uv; that takes about 15 s.
- KiCad 10.0.6, freerouting 2.2.4 and KiCadRoutingTools 0.19.0 (for
  `route_critical` and route_auto's KRT finish) are the Hive's, read-only
  in `/hive/tools` (put there through a warden). Java 25 is the system's.
  `bin/hwde` points the skill at that folder
  (`HWDE_TOOLS_DIR`) and the skill's `env.py` finds them all, plus KiCad's
  own python with `pcbnew`, its ngspice, its 3D model loaders and its stock
  symbols (KiCad 10's `.kicad_symdir` libraries, flattened into
  `~/.cache/hwde/symbols` on first use). If `hwde check` can't find one, ask
  a warden; don't install KiCad yourself.
- In another project's test or score check (a fresh HOME, only `/work` and
  `/hive/tools` mounted, no `$HIVE_MODULES`), run hwde as
  `/hive/tools/hwde/bin/hwde`. That's a published copy: `hwde publish <dir>`
  builds it at `<dir>/hwde` with its own Python 3.14 and the lock's packages
  installed, so it starts with no venv to build and no download, and writes
  `<dir>/hwde.version`. The copy runs the installed module's code
  (`/hive/modules/hive-hwde`, which follows main at every merge) with its own
  Python whenever it can see the module and both have the same
  `requirements.lock`, so code changes reach checks with no new publish; a
  warden re-publishes only when the lock changes. Agents see the module now;
  checks see it once the Hive mounts modules in checks (hive task
  261029-140256); until then a check runs the copy's own code. A board's dfm gate still fetches
  LCSC models from EasyEDA (rate-limited, HTTP 403) into a fresh HOME's
  cache, unless the board has an `easyeda/` folder beside its `parts.json`
  (`cpl_verify.default_cache`); commit that folder so a check needs no fetch.
- Boards go in the project you work in, under `boards/<name>/`
  (`HWDE_BOARDS_ROOT`, default `$PWD/boards`). Commit the workspace on your
  branch like any other change. Gate `--commit` isn't needed.

## Running it

- `hwde <script> [args]` runs `scripts/<script>.py` with the venv python
  and the `HWDE_*` pins set. Where the playbook, a recipe or a role prompt
  says `scripts/<x>.py`, run `hwde <x>`. With no X
  display it runs the script under its own Xvfb, with GLX off because H0's
  NVIDIA libraries crash Xvfb.
- `eval "$(hwde env)"` puts the same pins in your shell. Its `HWDE_PY` and
  `HWDE_MODULE` (the module's root) are what a board's own generator script
  (`kicad/gen/*.py`) runs with:
  `PYTHONPATH=$HWDE_MODULE/scripts:$HWDE_MODULE/scripts/lib $HWDE_PY kicad/gen/root.py`.
  `HWDE_SKILL`, its old name, is still set to the same path for boards that
  use it.
- Part datasheets: `hwde datasheet_extract --fetch boards/<name>` puts
  each parts.json line's PDF in `parts/datasheets/<lcsc>.pdf` (not
  committed; `index.json` beside them keeps URL and sha256). LCSC's
  wmsc.lcsc.com answers with a 140-byte script redirect, not the PDF;
  the fetch follows it, so don't curl datasheets yourself.
- Every board with a netlist gets `hwde sim_board --workspace boards/<name>
  --out boards/<name>/reports/sim_board.json`: ngspice over the board's own
  passives and discretes (KiCad 10's bundled libngspice, found by this
  wrapper), with the hand calcs a reviewer would do and a `known_unknowns`
  list. A review answers each known unknown from a datasheet or states the
  margin it rests on; `hwde eng_calc <calc> ...` gives the numbers a written
  margin cites.
- Every board with a power input also gets its power paths checked:
  `hwde sim_inrush --workspace boards/<name> --out boards/<name>/reports/sim_inrush.json`
  (load switches against a USB PD source: inrush, a switch that conducts
  with firmware off, turn-on SOA as a peak junction temperature, hard
  reset), `hwde check_power_path` (in the verify suite: the current each
  pad-track-via chain carries, against the budget, `power_paths` and the
  PD contract) and `hwde mlcc_dcbias --workspace boards/<name>` (ceramics
  at their DC bias). Put the PD contract in constraints.json as
  `"pd_source": {"contract_v": .., "contract_a": ..}` and the real current
  of each high-current run in `power_paths`; `hwde soa_check` gives a FET's
  Tj for a written margin. These are what hifi-receiver-2's first-spin
  power review did by hand (its docs/review-2/power.md).
- A board with analog or audio stages gets its hand numbers from
  `hwde analog_calc <calc>`: stage noise (`noise`), line levels and
  bridging (`level`, `bridge`, `damping`), coupling corners (`hpf`),
  slew (`fpbw`), op-amp phase margin into a cap load and the Riso that
  fixes it (`riso`), class-D LC filters and output power (`lc`,
  `classd`), and a SAR ADC's charge-bucket settling (`sar`).
  `hwde analog_verify` re-proves each one against ngspice.
  netlist_audit errors on an op-amp/comparator/in-amp input with no DC
  path for its bias current (`opamp_input_no_dc_path`), and warns on one
  held only by a connector and on a reverse-biased electrolytic
  (`polarized_cap_reverse`). Which rules of thumb held up, which are
  folklore, and the ngspice traps (`x^3` drops the sign; THD needs a
  max timestep) are in
  [reference/ingest/analog-audio.md](../reference/ingest/analog-audio.md).
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
- The playbook's roles run as helper agents: `hive agent spawn --role worker
  --brief @<file>`, or your harness's own subagent tool for a short
  read-and-report role ([playbook.md](playbook.md), "Agent spawn template").
  Give each one its role prompt (`agents/<role>.md`), the workspace paths, and
  the `hwde` command line in place of the venv python. Record each spawn with
  `hwde state spawn`. Reviewers always get fresh context.

## Documents: the design doc and the board guide

`hwde report_gen --workspace boards/<name>` builds the board's design doc
(`--kind highlight` and `--kind full` the shorter and longer ones), and the
`board-guide` agent writes the owner's guide. Both are set in the
pdf-material-builder skill's house style. `bin/hwde` finds that skill in
`~/.claude/skills/`, then `/opt/pdf-material-builder` (where this machine has
it), then `$HIVE_PROJECTS/pdf-material-builder`, and `hwde env` prints it as
`HWDE_PDF_MATERIAL_BUILDER`. Where none is installed, `HWDE_HOUSE_STYLE` points
at the module's plainer stand-in (`bin/house-style-fallback/`), so report_gen
still builds the PDF rather than stopping at `--tex-only`, and the guide is
built with lualatex directly and skips the skill's style check.

report_gen reads each stage's report from the name the recipes give it
(`reports/verify_all.json`, `reports/fab_export.json`, `reports/bom_cpl.json`,
`reports/gate-dfm.json`). It also takes the names earlier runs used:
verify_all's own `reports/checks/summary.json`, `verify-summary.json`,
`fab-export.json`, `bom-cpl.json` and `dfm-gate.json`.

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

## Checklists count without the person's sign-off

A coverage checklist (`reference/knowledge/checklists/<id>.yaml`, or one a
research task drafts under `<ws>/research/checklists/`) says which knowledge
classes a block needs before hwde claims it knows enough to design one.
Upstream, a checklist counted only once the person approved it. Here a
checklist also counts once an independent agent has ruled it:

    bin/hwde checklist_verify packet --checklist <id> --packet <dir> --key <file>
    # spawn agents/checklist-verifier.md (fresh context, not the checklist's
    # author) on <dir> only; it writes a verdicts JSON
    bin/hwde checklist_verify rule --checklist <id> --packet <dir> --key <file> \
        --verdicts <its JSON> --write
    bin/hwde checklist_verify status        # which checklists count, and why not

The packet hides the checklist among at least three planted mutants of it: a
required class dropped or swapped, an unrelated class added, a number in a
note changed. The ruling counts only when the verifier names every planted
fault for the reason it was planted and calls the real checklist sound. Each
required class must also rest on an active record that holds the class and
applies to the checklist's own blocks, or on a cited source file that exists.
`rule` checks that itself rather than taking the verifier's word. A verifier
that misses one planted fault proves nothing, so its whole ruling is refused.
`--write` sets the checklist to `verified` with a `verification` block pinned
to a digest of what it decides (applies, requires, sources). Coverage then
counts it as it counts an approved one, at any floor up to `approved`. An
edit to what it decides breaks the digest, and the checklist reads draft
again until it is ruled again. The person's `approval` still works and still
ranks above this.

Planted faults don't catch a verifier that diffs the items and votes on the
majority. The basis check does, because it is mechanical.

## Works first time, watches itself, brought up by the Hive

The person, 10-28: they give the spec, order the board, plug it in, run one
script and leave it; the Hive brings it up, updates its firmware, tests it
and tells them when it is ready. "This should be the flow for all board
designs." Take it as far as each board needs, and record how far:

- Design the board to see itself (`reference/self-observation.md`): rail
  dividers into spare ADC pins, status pins to GPIO, output loopback taps, a
  USB console. `bin/hwde check_observability --workspace <ws>` lists each
  rail as self / test_point / blind and each PG/FAULT/INT as watched or not.
- Before the order, a first-spin review (`reference/first-spin.md`): one
  `domain-reviewer` per domain, every margin a number, contingencies on the
  board, every blocker and major answered. `bin/hwde first_spin --workspace
  <ws>` says what is still open and merges the tools the reviewers wanted;
  file those to hive-hwde.
- Doubt the board (`reference/doubt.md`, `agents/doubter.md`): `bin/hwde
  doubt ask|add|fact|run|dispose|status|promote --workspace <ws>`. The
  agent forms its own questions about how this board could fail, writes a
  check for each under `<ws>/doubts/checks/`, and closes every unknown with
  a cited fact; general checks are promoted into `scripts/doubts/`, which
  every board is asked.
- Boards in hand: the `bring-up` verb and `reference/remote-bringup.md`.
  `bin/hwde state bringup --workspace <ws> --results bringup/results.json`
  closes it with the measurements (a project bench's session.json converts);
  a bare `bringup_passed` is refused, and `bring-up-passed` cites the file.

hifi-receiver-2 is the first instance (its `docs/review-2/`, `docs/remote.md`
and `bench/`).

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

The module improves the way the rest of the Hive does: by changing it, not by
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
exits 0 while its cause stands. The test (`bin/hwde test`) fails while a row
and its probe disagree. A shim in `bin/hwde` checks its row (`wa <id>`), so
it runs only while it's listed. When you fix a cause, its probe starts exiting
10: take the workaround out, delete the row, and keep the probe as a guard.

The score (`bin/hwde score`, [score.md](score.md)) doesn't read that table:
it re-runs the gates on the boards pinned in `pinned/`, with every shim off,
and counts what they find plus each board-local stand-in script. A fix in
hwde that clears a finding on those boards raises it at once. When a product
board you build in another project drops a stand-in script or changes in
another way worth measuring, re-pin it: `bin/hwde pin <board> --from <clone>
--commit <sha>`, classify any new script in `pinned/<board>.json`, run
`bin/hwde pin --digests`, and name the commit in the commit message.

Record each run's wall time per stage (the `phase` events in
`state.json` history) and the gate attempts and fix loops per gate. That way
a later board shows whether the module got faster or cleaner.

## Straps, reset states and the antenna

Two verify checks do what the first-spin review of hifi-receiver-2's boards
did by hand (its `docs/review-2/digital.md`, DIGITAL-1/2 and 18/19), on
every board:

- `check_straps` resolves each strapping pin of an MCU (ESP32, -S3, -C3)
  from the board's netlist at power-on and on an MCU-only reset: external
  pulls, the internal pulls of attached parts, series resistors, an
  oscillator's output while its enable reads high or floating, and an IO
  expander whose outputs hold through the MCU's reset. It fails a strap that
  can read the wrong level, a mid-band level or a clock, and an expander pin
  whose pull fights an external one. What each part does at reset is in
  `reference/reset_states.yaml`; a pin it doesn't know is a warning naming
  the pin. Add the part (with its datasheet) rather than ignoring it.
- `check_antenna` finds a module's PCB antenna end from its footprint (the
  body beyond the pad field), and fails copper of any kind under it on any
  layer. It warns when the end is more than 1 mm inside the board edge, or
  copper starts less than 15 mm beside it. The modules are in
  `reference/rf_modules.yaml`; a board can name its own in constraints.json
  (`"antennas": {"U7": "pcb"}`).

On hifi-receiver-2's main at 66891e8, `check_straps` fails core's GPIO0
(the 50 MHz oscillator's OE at 1.65 V from the TCA9555, and held through an
ESP32 reset) and six of its expander pins, and `check_antenna` fails core's
U7 (GND or +3V3 fill under the antenna on all four layers). lite-main
passes both, with warnings: 2.2 mm cleared beside the antenna, and ES8388
MCLK on GPIO0 with no known reset state.

## ESD at the connectors, and what the ingest runs found

`bin/hwde check_esd --pcb <board> [--constraints c.json]` (run by hand for
now; it joins the verify suite once its findings on the pinned boards are
judged) checks that every line leaving the board through a connector reaches
a TVS / ESD clamp first, with no via or branch before it, the clamp near the
pin and a ground via at its ground pad. It also prints an L·dI/dt estimate
of the overshoot for each line. `constraints.json` `esd.internal`,
`esd.exempt_nets`, `esd.clamps` and `esd.level_kv` tune it. The rules behind
it, and which EMC, ESD and thermal rules of thumb are verified, disputed or
folklore, are in [`reference/ingest/emc-thermal.md`](../reference/ingest/emc-thermal.md),
with the scripts that reproduce them.

## 3D models on their pads

`check_model_fit` (verify suite) reads every footprint's 3D model, VRML or
STEP, with no CAD kernel (`scripts/lib/modelgeom.py`). It places the model
the way KiCad's 3D viewer does, by the board's own `(model (offset) (scale)
(rotate))`, front or back, and lays the model's contact features against
the footprint's pads. Leads are flat geometry at the seating plane that
isn't the body's underside. Pins, shell legs and pegs are geometry below the
board, scored at each hole's centre. The fit is a pad-coverage score,
worked out at once for every slide within ±5 mm on a 0.05 mm grid by
cross-correlating rasters, at the model's own turn and at +90/180/270°.
A slide or turn that covers clearly more pads, and meets no fewer, is a
`model_offset` or `model_rotation` error. The finding gives the
`(offset)`/`(rotate)` to write in the footprint library. `model_scale`
(not 1 1 1), `model_floats` (lowest point above the board) and
`model_in_board` (geometry inside the board's material, within the outline,
where the footprint has no hole) are errors too. `model_unmatched` (no
placement meets half the pads: a wrong model) is a warning. A missing or
hidden model is skipped.

`lib_pull` runs the same fit on every footprint it pulls
(`check_model_fit.fit_footprint`) and writes the fitted `(offset)`,
`(rotate)` and `(scale)` into the library's `(model ...)`, so a model
EasyEDA misplaced is fixed before any board takes the footprint (the
pull's `model_fit` report; `--no-model-fit` skips it). A model no
placement fits is left as it was and listed in `model_fit.left`.

The hit: EasyEDA's TYPE-C-31-M-12 model, as `lib_pull` fetches it for
lite-main J1, sits 2.2 mm off its footprint. Its shell legs and pegs miss
every hole, so renders, the STEP and an enclosure fitted to it put the
connector in the wrong place. The check reports `model_offset` with a fit of
`(offset (xyz 0 -2.2 0)) (rotate (xyz 0 0 180))`. `fpfix.body_flags`
still flags a pulled library footprint at pull time (THT pins only). This
check runs on the board, every time.

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

- `route_critical` and KRT's finishing pass need KiCadRoutingTools 0.19.0.
  It's now in `/hive/tools/kicad-routing-tools/KiCadRoutingTools-0.19.0`,
  but nothing points the skill at it yet (brief 261028-141924). A board with
  diff pairs, high-current power or RF needs it.
- DigiKey/Mouser price lookups need API keys and stay off.
- The knowledge records cite manufacturer PDFs that the module doesn't carry
  (see NOTICE). `docs/knowledge-sources.txt` pins them at upstream's commit;
  `bin/hwde` fetches them into `$HOME/.cache/hwde/knowledge-sources` the first
  time `knowledge`, `research`, `check-all` or `hwde sources` runs (37 MB,
  each checked against its git blob sha) and points `HWDE_KNOWLEDGE_SOURCES`
  there. A record `research promote` adds with a new PDF puts that PDF in the
  same cache, so it isn't in the module or upstream's list.
- Knowledge records the Hive adds start at `verified`, which means a
  fresh-context second reader has checked them against the cited pages.
  Coverage still counts only `approved` records, which is the person's
  sign-off, so their slots read `provisional`, not `gap`.

## The first board

[`boards/hwde-led-test/`](../boards/hwde-led-test/) is the first board hwde
made in the Hive: a 2-layer, 13.4 x 10.5 mm board with a 5 V header, a bulk
cap and a green LED with its resistor. It went through every stage up to P9,
and ERC, place, drc_routed, verify and dfm all pass. Read its `log/P*-digest.md`
for what each stage did and `LEARNINGS.md` for what it taught.

## Ingestion notes

What each EE ingestion run found, with every rule marked verified, disputed
or folklore, and its ranked gap list: [dfm-assembly](../reference/ingest/dfm-assembly.md).
`hwde check_paste --pcb <board> [--stencil-mm 0.10]` (stencil area ratio,
exposed-pad paste) came from it; run it in the DFM/assembly review. It is
not in the verify suite yet (task 261031-013753-d3238414d5).
