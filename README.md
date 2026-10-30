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
- **Score**, `bin/hwde score`, higher is better: what this checkout's
  gates find on boards held fixed. The hi-fi receiver's lite-main and core,
  pinned in `pinned/` at one hifi-receiver-2 commit, and hwde-led-test go
  through erc, drc_routed, verify and dfm with `bin/hwde`'s shims off. Each
  unwaived finding, each warning and each board-local script that stands
  in for hwde is a charge, and the score is minus the charges. A fix in
  hwde that clears a finding on those boards raises it with nothing written
  by hand. A pinned board changes only through `bin/hwde pin`. The whole
  definition is [docs/score.md](docs/score.md). About ten minutes or more,
  under `hive agent heavy`. A looser check finds less, so the score also
  runs hwde's planted faults (`bin/strength.py`: the gate criteria, every
  verify check's mutant in `tests/golden/`, dfm at JLC's floors), and each
  one a check lets through charges 100.

MIT, like upstream; see NOTICE for what that doesn't cover.
