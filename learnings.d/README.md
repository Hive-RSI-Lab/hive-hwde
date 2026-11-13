# learnings.d - the skill's root lessons

A board lesson that is about the skill, not the board, is promoted here by
`learnings.py resolve ... --kind root_learnings` (see
`reference/recipes/promote.md`). Each lesson is its own file,
`learnings.d/<date>-<slug>.md`: the entry verbatim, a provenance line, and its
triage row as the last line:

    Triage: now L0 | target L2 | owner scripts/gate.py | status open | note ...

Two PRs that each add a lesson add two different files, so they never
conflict. Nothing here is counted by hand: `python3 scripts/learnings.py
triage` computes the ladder counts from these files.

The old hwde archive (root `LEARNINGS.md`, entries 1-425, and its register
`design/ladder-triage.md`) is not carried in this module. A copy of both is
kept with the board it came from, in the rf-de-20m
workspace under `archive/ai-ee/`. A checkout that does carry them at the repo root gets
their rows counted too.
