# hwde

AI PCB engineer for KiCad + JLCPCB. Takes a task in any project state - review a
board, fix findings, move/swap/add/remove a part, re-route a net, make a footprint,
DFM-check, order, track, resume - with the full brief -> architecture -> schematic ->
placed & routed -> verified -> DFM-checked -> order-ready JLCPCB pipeline as one of
those tasks.

Entry point: [SKILL.md](SKILL.md). Drop this repo into a project as `.claude/skills/hwde/`.

Runtime environment setup (KiCad 10.0.3 pin, Python venv pins, Freerouting jar +
Temurin JRE) lives in the parent build repo and is resolved via `scripts/lib/env.py`.
