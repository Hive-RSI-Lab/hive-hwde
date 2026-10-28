# promote - move captured learnings to their level on the ladder

Every run captures learnings in its own workspace. This verb is the second
half: it turns that pile into artifacts. Nothing here touches copper.

## The two files

- `~/dev/boards/<b>/LEARNINGS.md` - the run's own file, appended as the run goes.
  Format: a `## YYYY-MM-DD [P7][tag][tag] the claim in one line` heading (stage
  tag first), then the body with the numbers it was measured on. `## ` headings
  before the first dated entry are preamble; after it they must parse.
  `learnings.py init --workspace <ws>` writes the skeleton.
- `~/dev/boards/<b>/learnings/queue.yaml` - the machine-readable queue, compiled from
  that file. Per entry: `{entry, line, title, tags, stage, proposed_level,
  targets, status}` plus a `resolution` once ruled. Ids are stable
  (`<date>-<title slug>`), so a ruling survives every later re-compile.

## Run close (every recipe ends with it)

    learnings.py compile --workspace ~/dev/boards/<b>

Idempotent: new entries land `pending`, rulings are preserved, line/title/tags
are refreshed. Exit 1 means a heading did not parse or a queue id lost its
entry - fix the file, do not hand-edit the queue.

## Three operator modes, one queue

- **owner at run close** - rule on this run's entries while the evidence is
  fresh.
- **general agent sweeping** - `learnings.py sweep` lists every workspace with
  its pending count; work the backlog board by board.
- **stage learner (U7) pre-session** - `learnings.py queue --workspace <ws>
  --status pending --stage P6` pulls only the entries that stage owns; they are
  the session's candidate artifact edits.

## Ruling on an entry

A promotion WRITES SOMEWHERE. Pick the level, make the edit, then record it:

| kind | what you actually edit |
|---|---|
| `script_check` | a check/threshold in `scripts/` - the L2/L3 destination |
| `cost_term` | a placement/routing cost term or bench scorer |
| `template` | a placement template or generator, so it cannot be built wrong |
| `prompt_line` | a line in the owning `agents/<role>.md` |
| `knowledge_record` | `reference/knowledge/records/<id>.yaml` (U4) |
| `remediation` | `reference/remediations/<check_id>.md` (T4) |
| `bench_item` | a bench fixture or a `--baseline` case |
| `root_learnings` | a new lesson file `learnings.d/<id>.md`, its triage row as its last line |

    learnings.py resolve --workspace ~/dev/boards/<b> --entry <id> \
      --status promoted --kind script_check --level L2 \
      --reason "<why it climbs>" --targets scripts/check_current.py

`root_learnings` is the only kind the tool performs for you - it appends the
entry verbatim (with a provenance line) and writes the triage row, because the
suite checks those two files against each other:

    learnings.py resolve --workspace ~/dev/boards/<b> --entry <id> \
      --status promoted --kind root_learnings --level L2 \
      --now-level L0 --target-level L2 --triage-status open \
      --note "<what would own it, and how>" \
      --reason "<why it is skill-level, not board-level>" \
      --owner scripts/gate.py

A whole pass at once: `--batch <file>.yaml` with a `rulings:` list of the same
fields (`triage: {now, target, owner, status, note}` for root promotions).
In a batch, `artifacts:` is the resolution's written-to list - the CLI's
`--targets` maps to exactly that - while an optional `targets:` key refreshes
the ENTRY's candidate list instead; they are different fields.
Write every `reason:` and `note:` as a `>-` block scalar - a multi-line PLAIN
scalar breaks the moment the prose contains ": " or a line starting with "- ".
Keep the batch beside the queue - it is the record of who ruled what.
`~/dev/boards/rf-de-20m/learnings/rulings-2026-08-14.yaml` is the worked example: 66
entries, 42 promoted, 24 declined.

Declining is a first-class outcome and needs its own kind + reason:
`board_local` (true of this board, not of the skill), `duplicate`,
`superseded`, `not_actionable`.

## After the edits

- Knowledge records: `knowledge.py --validate`, and re-render any topology view
  that has one (`knowledge.py --render-topology buck --out
  reference/topologies/buck.md`) - the view is byte-pinned to the records.
  A promoted record carries the U13 coverage fields: `level` (principle/
  topology/family/part/instance), `envelope` (what the rule scales with,
  unit-suffixed dims; none at principle level), `maturity` - `draft` unless
  the OWNER rules it `approved` in this pass (then add `approval: {by, date}`);
  `proven` is never hand-set (bring-up evidence via `knowledge.py --prove`).
- `learnings.py validate --workspace ~/dev/boards/<b>` - every promoted entry names
  artifacts that exist, every ruling carries a kind and a reason.
- `learnings.py triage` - recompute the register header's counts from its own
  table rather than editing them.
- Commit the artifact edits, the queue and the LEARNINGS/triage rows TOGETHER.
  An uncommitted LEARNINGS append gets reverted by the next scoped gate commit.

## Do not

- Do not promote a board's engineering record (its operating point, its part
  survey, its own thermal budget). If nothing outside that board can use it,
  the ruling is `board_local` and the workspace keeps it.
- Do not mark an entry promoted with nowhere to point: `validate` fails a
  promotion with no artifacts, and that is the whole point of the queue.
- Do not edit `queue.yaml` by hand to clear a backlog. Pending is an honest
  state; a fabricated resolution is not.
