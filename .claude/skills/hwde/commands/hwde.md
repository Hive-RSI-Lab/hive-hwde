# /hwde - AI PCB engineer

Usage:
- `/hwde <task>` - anything an EE would be asked: "review this board",
  "fix the verify findings", "move C12 off the connector", "swap R5 for a 10k",
  "add a 100nF on U1 pin 12", "re-route +3V3", "make a footprint for C5184243",
  "is this manufacturable", "order 5 boards", "where's my order" - and
  "design me a USB-C PD trigger board", which is the whole pipeline.
  Attach docs freely (requirements, datasheets, mechanical constraints) - they
  land in `brief/`.
- `/hwde --resume <workspace>` - continue a run from its `state.json`
  (e.g. `/hwde --resume ~/dev/boards/rf-amp-ctrl`). Workspaces live in the
  boards repo, not this one (`HWDE_BOARDS_ROOT`, default `~/dev/boards`).

On invocation:
1. Read `.claude/skills/hwde/SKILL.md` (the orchestrator playbook) and follow
   it exactly - it defines the front door, the phase machine, gates, the fix
   loop, state recording, and human checkpoints.
2. Route the task FIRST:
   `scripts/task_router.py --task "<the user's words>" [--workspace ~/dev/boards/<name>]`
   exit 0 -> follow `recipe.doc` + `recipe.steps`; exit 1 -> the payload says
   whether to classify (`--verb`), ask the user, or clear a blocked
   precondition. Never hand-assemble a recipe the table already has.
3. A task that needs a NEW workspace (a full run, or importing an outside
   project) derives a short kebab-case board name, creates
   `~/dev/boards/<name>/`, and runs `check_env.py` + `state.py init` as the
   recipe's first steps. A board's part number (`PCB-NNNN-R`) comes from
   `~/dev/boards/register.yaml`, read-only from here; a board not listed there
   carries no number. When the register already lists the new board as
   `dir: <PN>_<name>`, the workspace and KiCad project take that name (the
   router's `--workspace <name>` finds it); otherwise the board starts bare.

The orchestrator never opens design files; all design work happens in spawned
subagents using the role prompts in `.claude/skills/hwde/agents/`.
