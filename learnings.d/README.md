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

The old skill's archive sits at the repo root: `LEARNINGS.md` (entries
1-425, closed 2026-10-06) and its register `design/ladder-triage.md`. Both are
frozen - remediation refs and register rows cite their line numbers - so a new
lesson never goes there. `triage` counts their rows with the lesson files, and
promotion refuses a lesson the archive already carries.
