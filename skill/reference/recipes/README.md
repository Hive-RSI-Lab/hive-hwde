# recipes - one file per task verb

The skill is an EE picking up a project in whatever state it is in. `tasks.yaml`
holds the MECHANICAL half of each task (steps, arguments, edit class); these
files hold the half a command line cannot carry: what the step means, what it
is blind to, and when to stop and ask.

## How a recipe reaches you

    scripts/task_router.py --task "<the user's words>" --workspace ~/dev/boards/<b>

exit 0 -> `recipe.steps` bound to this workspace's real paths, plus `gates`
and `human_hold` read from `reference/invalidation.yaml` (never restated here).
exit 1 -> `status` says why: `ambiguous` (pick from `candidates`, re-run with
`--verb`), `unknown` (classify yourself or ask), `needs_args` (each `needs`
entry carries the question to ask), `blocked` (a precondition failed - the
detail says which). Read `recipe.doc` - this file - before executing the steps.

## The step vocabulary

| kind | means |
|---|---|
| script | run it with the repo venv python; exit 0 pass / 1 violations / 2 error |
| gate | `gate.py --gate <g> <input> --workspace <ws>` (--workspace records the result in state.json); on pass `--commit`, on fail the fix loop |
| agent | spawn that role prompt from `agents/` at the given tier |
| human | a hold: 0 silent, 1 log a decision, 2 summarize at a checkpoint, 3 approve before fab |
| recipe | another verb, run inline (this is how `full-run` is assembled) |
| note | judgment the scripts cannot check - apply it |

A step with `optional: true` runs only when its `when` condition holds. Steps
render `<name>` for slots you fill at run time (op-list paths, the edit class a
fix turns out to belong to); `needs` lists the ones that BLOCK the plan.

## The rule these files exist to obey

Knowledge that a script can check does not go in a prompt (design notes 6). So:
a fact that fires on a FINDING belongs in `reference/remediations/<check_id>.md`
(the fixer gets it automatically through the work order); a fact about how to
carry out a TASK belongs here; a fact that can be measured belongs in a check.
If a recipe note starts sounding like a threshold, it has outgrown this file.
