# hive-hwde

The hwde module: KiCad hardware design, from ihsan-sa/hwde's skill, as a Hive module in the Hive org like the other modules.

An agent designs a PCB from a written brief: research, architecture, parts, schematic, placement, routing, then the checks (ERC, DRC, the verify suite, JLCPCB DFM on the exported Gerbers), ending in a fab package. The person signs off between stages. Ordering and paying are always the person's.

- `.claude/skills/hwde/`: the skill (playbook `SKILL.md`, agent roles, scripts, reference), as upstream has it ([what differs](docs/upstream.md))
- `tests/`: upstream's unit tests for the scripts, and `tests/workarounds/`, the probes
- `bin/hwde`: runs the skill's scripts in a Hive sandbox with the Hive's KiCad 10 and freerouting (`hwde check` first)
- `tests/`: the lookups the skill does for the Hive's shared tools folder (`$HWDE_PY -m pytest tests`)
- How an agent uses it in the Hive: [docs/README.md](docs/README.md)
- The first board made with it: [boards/hwde-led-test/](boards/hwde-led-test/)

## Test and score

- **Test**, `bin/hwde test`, run on every change: upstream's unit tests
  for the skill's scripts (in `tests/`, from ihsan-sa/hwde, about 2,400 of
  them) and the workaround probes. It leaves out tests marked `smoke` (a
  live KiCad run) or `net` (the internet), and points `HWDE_BOARDS_ROOT` at
  an empty dir, so tests that want a real board skip. Upstream tests that
  fail here for a cause a brief already owns are expected failures, listed
  with that brief in `tests/conftest.py`; one that starts passing shows as
  XPASS, and its line comes out. `tests/test_workarounds.py` runs every
  probe in `tests/workarounds/` and fails when
  [docs/workarounds.md](docs/workarounds.md) doesn't match them: a cause
  that stands with no row, or a row whose cause is fixed. It also leaves
  out the tests listed as `slow` in `tests/conftest.py` (`SLOW`: the live
  toolchain, whole fixture boards through bench or the gates, intake of a
  whole project); `bin/hwde test-slow` runs those, so run it too on a change
  to the checks, bench, intake or `check_env`. The test keeps its scratch
  files on `/dev/shm` when it has room and nothing in it fsyncs
  (`HWDE_NO_FSYNC`), because on a loaded disk the suite's file writes, not
  its CPU, set its time; it prints its time and its slowest 15 tests at
  the end. A few minutes; pytest arguments pass through
  (`bin/hwde test tests/test_fab.py -x`).
- **Boards**, `bin/hwde check-all`, not the per-change test: copies every
  board under `boards/` to a scratch dir and re-runs its gate steps there
  through `bin/hwde`: `erc`, `drc_routed`, `verify`, `fab` (gerbers, drill,
  BOM and CPL from `fab_export` and `bom_cpl`) and `dfm`. A board runs each
  gate its `state.json` records as passed, and `fab` once it has a package
  in `fab/`. Any step that fails fails it, and it runs the probes too. Its
  time grows with every board, so run it on a change to the gates, the
  shims in `bin/hwde` or a board, and before a release. Both need
  `/hive/tools`'s KiCad and the Hive's uv; on a fresh HOME they build the
  venv first. Inside a Hive turn both, and the scripts that run a whole
  board, the router or a sim (`route_auto`, `route_critical`,
  `place_anneal`, `gate`, `verify_all`, `bench`, `render`, `sim_run`,
  `fab_export`), run under `hive agent heavy`: they wait (at most 10
  minutes) while the machine is strained and other turns run one. A command
  already inside one (check-all's gates, a test that runs `bin/hwde`), or a
  Hive without that op yet, runs bare (`tests/test_heavy.py`).
- **Score**, `bin/hwde score`, higher is better: the share of those gate
  steps, over all boards, that ran with no workaround, as a percentage. A
  step counts as worked around when a row of `docs/workarounds.md` names it
  for that board, because its input came through a shim in `bin/hwde` or
  an edit made by hand in the board's files. A fix in the skill that lets a
  board drop a workaround removes its row and raises the score. A new board
  adds its steps; any workarounds it needs add rows, with a probe each.
  hwde-led-test starts at 0/5: its ERC schematic was written through the
  symdir shim, verify reads a hand-made decoupling entry, and fab and dfm
  read a hand-added `refdes` per part. Its routed board had hand-widened
  courtyards and a hand-picked outline margin until `check_courtyard`, fpfix
  rule E and silk in `board_edit --outline fit` took those two off.
  The boards built with hwde in other projects count too:
  [docs/boards.md](docs/boards.md) records each one (the hi-fi receiver's
  lite-main and core), with the steps it passed, and the rows of
  `docs/workarounds.md` name their board-local scripts and hand edits like
  any other board's. The score reads only this repo's files.

MIT, like upstream; see NOTICE for what that doesn't cover.
