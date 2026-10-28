# hive-hwde

The hwde module: KiCad hardware design, from ihsan-sa/hwde's skill, as a Hive module in the Hive org like the other modules.

An agent designs a PCB from a written brief: research, architecture, parts, schematic, placement, routing, then the checks (ERC, DRC, the verify suite, JLCPCB DFM on the exported Gerbers), ending in a fab package. The person signs off between stages. Ordering and paying are always the person's.

- `.claude/skills/hwde/`: the skill (playbook `SKILL.md`, agent roles, scripts, reference), as upstream has it ([what differs](docs/upstream.md))
- `bin/hwde`: runs the skill's scripts in a Hive sandbox with the Hive's KiCad 10 and freerouting (`hwde check` first)
- `tests/`: the lookups the skill does for the Hive's shared tools folder (`$HWDE_PY -m pytest tests`)
- How an agent uses it in the Hive: [docs/README.md](docs/README.md)
- The first board made with it: [boards/hwde-led-test/](boards/hwde-led-test/)

MIT, like upstream; see NOTICE for what that doesn't cover.
