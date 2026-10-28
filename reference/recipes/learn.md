# learn - teaching session: one stage, owner grading, durable artifact edits

Learning mode (v3 design decision 1). A learner agent owns ONE stage's
artifact set, spawns ISOLATED instances of the stage agent on frozen inputs,
the OWNER grades the renders, and the learner converts each critique into a
CLASSED artifact edit, then re-runs fresh. The frozen graded fixtures and the
scorer terms left behind are the RECORDING of the owner's scores - they guard
production runs where the owner is absent. `agents/learner.md` is the
contract; this file is the session mechanics.

Owner presence is a precondition, not a nicety (hold 3): the owner IS the
scorer. Without the owner there is nothing to learn from - do not run this
verb, and never let the learner self-grade.

## Roles

- **owner** - grades every cycle's render, rules on scorer terms, approves
  the freeze. Session close = owner satisfied.
- **learner agent** (one per session) - edits the stage's artifacts, spawns
  stage-agent instances, never touches other stages.
- **stage agent instances** - fresh context each cycle, frozen input, output
  contract back. Carry-over between cycles would hide whether the ARTIFACTS
  improved, so instances are never reused.

## Frozen inputs

The loop runs on bench fixtures (`bench.py --list` shows the stage's set).
When the session teaches on a NEW region (a workspace board, a known-bad
area), capture it first:

    bench.py --freeze --stage P6 --fixture <new_id> --board <b> \
      --from pcb=<path> --from constraints=<path> --grade "<owner verdict>"

Freeze copies the sources under the fixtures dir, LF-normalizes text before
sha-pinning (a CRLF-pinned sha drift-refuses on every fresh checkout), and
appends the manifest entry. KiCad artifacts bring their stem-matched
`.kicad_pro`/`.kicad_dru` siblings automatically - a board judged without
them scores under KiCad defaults. `known_answer` blocks (P8/P9 negative
fixtures) are hand-authored manifest YAML, never frozen. Then record the
grade as a baseline: `bench.py --stage <s> --fixture <id> --baseline`.

A run built under a LEARNING TARGET already names the stage to teach and the
deliverable to freeze: `state.py resume --workspace <ws>` reports `mode.stage`
(U18; null when the target studies no single stage). Take the session's
`--stage` from there rather than re-deciding it - the board was scoped and
sized to teach exactly that stage, and freezing a different one grades work the
run never optimized for.

## Pre-load (before cycle 1)

    learnings.py queue --workspace <ws> --status pending --stage <stage>
    knowledge.py --select --workspace <ws>
    bench.py --list

Pending queue entries are the session's candidate edits, and their kinds are
the SAME classed-edit vocabulary the learner uses (learnlib PROMOTE_KINDS:
script_check, cost_term, template, prompt_line, knowledge_record,
remediation, bench_item, root_learnings) - never invent a second taxonomy.

## The loop

1. Spawn an isolated stage-agent instance on the frozen input.
2. Render the output; score it against the fixture baseline when one exists
   (`bench.py --stage <s> --fixture <f> --artifact <candidate> --compare`).
3. The owner grades render + score.
4. Each critique becomes ONE classed artifact edit (kind, file, change,
   which critique it answers). An unclassifiable critique goes back to the
   owner as a question, not a guess.
5. Fresh re-run on the SAME frozen input; compare; repeat.

## Scorer divergence rule

When the owner's verdict and the bench composite disagree - the owner
rejects a high score, or approves a low one - the scorer is MISSING A TERM.
Fixing the scorer (a metric, a weight in benchlib WEIGHTS, a graded fixture)
IS session work at the same priority as the stage-artifact edit, because the
recorded scorer replays the owner's judgment on production runs. A new
penalty term needs its weight in the stage's table (unknown penalty keys
raise by design) and re-records every touched baseline in the SAME commit.

## Exit checklist (mechanical - run it, do not paraphrase it)

1. Freeze the owner-approved output as a graded fixture
   (`bench.py --freeze` as above, or nothing if only re-grading), then
   `bench.py --stage <s> --fixture <f> --baseline`.
2. Scorer terms committed; every baseline a scorer change touched
   re-recorded in the same commit as its cause.
3. New knowledge records indexed: `knowledge.py --validate` green; any
   topology view that has one re-rendered in the same commit; queue entries
   the session consumed ruled (`learnings.py resolve` - kind
   `knowledge_record` is how a queue entry becomes a record) and the queue
   linted: `learnings.py validate --workspace <ws>`.
4. Root lessons for the session's durable gotchas: one `learnings.d/`
   file each, its triage row as its last line (`learnings.d/README.md`;
   same session, the discipline test enforces).
5. Regression: `bench.py --compare` on the stage's OTHER frozen fixtures -
   exit 1 on any of them = fix before closing, never close red.
6. Commit artifact edits + manifest + baselines + LEARNINGS/triage together
   (scoped paths); report with the learner's output contract.

## Do not

- Do not grade for the owner, and do not average the owner's verdict with
  the composite - divergence is a missing term, not a tie to split.
- Do not edit another stage's contract, templates or cost terms; file what
  you found for that stage's own session instead.
- Do not freeze an unapproved output "to keep it" - the bench is the record
  of graded work only.
- Do not leave a new scorer term without a weight or a baseline: the next
  session inherits a bench that no longer replays anyone's judgment.
