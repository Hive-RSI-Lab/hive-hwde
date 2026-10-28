# hive-hwde

The hwde module: KiCad hardware design for the Hive, in the Hive org like the other modules. It began as ihsan-sa/hwde's Claude Code skill and is its own now ([where it came from](docs/upstream.md)).

An agent designs a PCB from a written brief: research, architecture, parts, schematic, placement, routing, then the checks (ERC, DRC, the verify suite, JLCPCB DFM on the exported Gerbers), ending in a fab package. The person signs off between stages. Ordering and paying are always the person's.

- [`docs/README.md`](docs/README.md): how an agent uses it in the Hive; [`docs/playbook.md`](docs/playbook.md): the phases, gates and fix loop
- `agents/`: the role prompts the playbook spawns; `scripts/`: the checks and tools; `reference/`: their tables, recipes, checklists and knowledge records
- `bin/hwde`: runs the scripts in a Hive sandbox with the Hive's KiCad 10 and freerouting (`hwde check` first)
- The first board made with it: [boards/hwde-led-test/](boards/hwde-led-test/)

## Test and score

- **Test**, `bin/hwde check-all`: copies every board under `boards/` to a
  scratch dir and re-runs its gate steps there through `bin/hwde`: `erc`,
  `drc_routed`, `verify`, `fab` (gerbers, drill, BOM and CPL from
  `fab_export` and `bom_cpl`) and `dfm`. A board runs each gate its
  `state.json` records as passed, and `fab` once it has a package in `fab/`.
  Any step that fails fails the test. It also runs every probe in
  `tests/workarounds/` and fails when [docs/workarounds.md](docs/workarounds.md)
  doesn't match them: a cause that stands with no row, or a row whose cause
  is fixed. It needs `/hive/tools`'s KiCad and the Hive's uv; on a fresh
  HOME it builds the venv first (about 25 s in all for hwde-led-test).
- **Score**, `bin/hwde score`, higher is better: the share of those gate
  steps, over all boards, that ran with no workaround, as a percentage. A
  step counts as worked around when a row of `docs/workarounds.md` names it
  for that board, because its input came through a shim in `bin/hwde` or
  an edit made by hand in the board's files. A fix in the scripts that lets a
  board drop a workaround removes its row and raises the score. A new board
  adds its steps; any workarounds it needs add rows, with a probe each.
  hwde-led-test starts at 0/5: its ERC schematic was written through the
  symdir shim, its routed board has hand-widened courtyards and a
  hand-picked outline margin, verify reads a hand-made decoupling entry, and
  fab and dfm read a hand-added `refdes` per part.

MIT, like upstream; see NOTICE for what that doesn't cover.
