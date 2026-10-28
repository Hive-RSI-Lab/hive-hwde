# hive-hwde

The hwde module: KiCad hardware design, from ihsan-sa/hwde's skill, as a Hive module in the Hive org like the other modules.

An agent designs a PCB from a written brief: research, architecture, parts, schematic, placement, routing, then the checks (ERC, DRC, the verify suite, JLCPCB DFM on the exported Gerbers), ending in a fab package. The person signs off between stages. Ordering and paying are always the person's.

- `.claude/skills/hwde/`: the skill (playbook `SKILL.md`, agent roles, scripts, reference), as upstream has it ([what differs](docs/upstream.md))
- `tests/`: upstream's unit tests for the scripts, and `tests/workarounds/`, the probes
- `bin/hwde`: runs the skill's scripts in a Hive sandbox with the Hive's KiCad 10 and freerouting (`hwde check` first)
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
  that stands with no row, or a row whose cause is fixed. About 3 minutes;
  pytest arguments pass through (`bin/hwde test tests/test_fab.py -x`).
- **Boards**, `bin/hwde check-all`, not the per-change test: copies every
  board under `boards/` to a scratch dir and re-runs its gate steps there
  through `bin/hwde`: `erc`, `drc_routed`, `verify`, `fab` (gerbers, drill,
  BOM and CPL from `fab_export` and `bom_cpl`) and `dfm`. A board runs each
  gate its `state.json` records as passed, and `fab` once it has a package
  in `fab/`. Any step that fails fails it, and it runs the probes too. Its
  time grows with every board, so run it on a change to the gates, the
  shims in `bin/hwde` or a board, and before a release. Both need
  `/hive/tools`'s KiCad and the Hive's uv; on a fresh HOME they build the
  venv first.
- **Score**, `bin/hwde score`, higher is better: the share of those gate
  steps, over all boards, that ran with no workaround, as a percentage. A
  step counts as worked around when a row of `docs/workarounds.md` names it
  for that board, because its input came through a shim in `bin/hwde` or
  an edit made by hand in the board's files. A fix in the skill that lets a
  board drop a workaround removes its row and raises the score. A new board
  adds its steps; any workarounds it needs add rows, with a probe each.
  hwde-led-test starts at 0/5: its ERC schematic was written through the
  symdir shim, its routed board has hand-widened courtyards and a
  hand-picked outline margin, verify reads a hand-made decoupling entry, and
  fab and dfm read a hand-added `refdes` per part.

MIT, like upstream; see NOTICE for what that doesn't cover.
