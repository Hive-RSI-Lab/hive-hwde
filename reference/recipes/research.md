# research - fill a coverage gap: acquire, read visually, synthesize, second-read, queue

Coverage-gated design (v3 decision 5a). `knowledge.py --coverage` turns "I
know enough to design this" into a per-slot verdict; a `gap` slot is a
research task spec (`gaps[]`: slot, operating point, missing classes + min
levels, principle parents, related records). This verb turns ONE such spec
into page-cited draft records the coverage query can retrieve - workspace-
first, second-reader verified, queued for the owner's promotion ruling.
Research launches AUTOMATICALLY on every gap inside a full run (owner
ruling); the caps are what bound it, and a cap hit is a VISIBLE checkpoint.

## Roles

- **research.py** (`scripts/research.py`) - the mechanical spine: task file
  + source ledger, allowlisted fetch into quarantine, verdicts, validate,
  close (queue entry), promote, caps. `lib/researchlib.py` owns the logic.
- **researcher** (`agents/researcher.md`, fable/high) - the ONLY agent with
  web tools; acquires through `research.py fetch` only, reads cited pages
  VISUALLY, writes `research/records/<id>.yaml` (+ a draft checklist when the
  slot has none), runs validate. One instance per task; tasks are disjoint.
- **research-second-reader** (`agents/research-second-reader.md`,
  opus/high, FRESH context) - re-reads every cited page and tries to refute;
  `research.py verify --verdict verified|refuted` per record.
- **owner** - approves promoted records (maturity `approved`, an approval
  block) in the promote pass; rules on cap hits.

## The mechanics (bound in tasks.yaml; this is what they mean)

1. `knowledge.py --coverage --workspace <ws> --out log/coverage-<label>.json`
   - exit 1 = gaps. Workspace research records already present fold in, so
   a researched-but-unapproved class reads `provisional`, not `gap`.
2. `research.py open --workspace <ws> --gaps <report> --slot <id> | --all`
   - one task per gap under `research/tasks/<task>.json`; consumes
   `budgets.research.per_run` (state.py ledger), snapshots `depth_per_gap`,
   emits the BRIEF (paste it into the researcher spawn: gap, existing
   knowledge, tier policy, visual-read rule, templates, allowlist, caps,
   commands). `status: checkpoint` = the per-run cap is spent; the payload
   names the unopened slots - present them, never silently skip.
3. `research.py fetch --workspace <ws> --task <t> --url <https://...> --tier <T>`
   - https + `reference/knowledge/domains.yaml` allowlist, checked before
   any bytes and on every redirect hop; off-list = exit 2 refused (and
   ledgered). Files land in `research/sources/`, sha-pinned with tier
   (`vendor-layout > vendor-appnote > cross-vendor > forum`; vendor community
   hosts force `forum`). Depth cap = acquisitions per task -> exit 1
   checkpoint. `--expect pdf` refuses HTML shells (LCSC: use the wmsc form).
4. Records: `research/records/<id>.yaml`, schema v2 strict, `status: draft`,
   `maturity: draft`, `origin: research:<task>`, sources ONLY from the ledger
   with `page` + `note` (what was READ), `envelope` + `envelope_note` (what
   the rule scales with). No checklist for the slot -> the researcher writes
   `research/checklists/<token>.yaml` first (maturity draft).
5. `research.py validate --workspace <ws> --task <t>` - exit 1 names every
   contract breach (off-ledger citation, missing page/note, forum sole
   source, self-declared maturity, id clash with the library, record that
   does not key the slot or cover none of the missing classes).
6. `research.py verify --workspace <ws> --task <t> --record <id> --verdict verified --note "<page-level finding>"`
   - verified = status active + maturity verified + `verification` block;
   refuted = stays draft (the researcher may fix and a later pass re-reads).
7. `research.py close --workspace <ws> --task <t>` - validate clean + every
   record VERIFIED -> appends the workspace LEARNINGS.md entry and runs the
   queue compile (U6): the entry is `pending` for the promote pass. A ruling
   is NOT enough: refuted leaves the record draft, draft never injects, so
   close REFUSES (exit 1, naming them) while any record is still draft.
   Re-read and rule it, rewrite the refuted one and have it re-read, or
   `--accept-drafts` to ship the slot without that knowledge - which records
   a state decision naming every record you gave up on. A task with nothing
   usable: `--abandon --reason "<why>"` (recorded as a decision).
8. Re-run coverage. Verified records inject into P3/P6/P7 spawns through the
   normal `knowledge.py --select --workspace <ws>` path (drafts never do).
   The report's `draft_unverified` bucket is its own bucket, NOT folded into
   `provisional`: it names every unverified record even when a verified
   sibling covers the same class. `research.py status` counts verified vs
   draft per task and exits 1 while any record is draft behind a CLOSED task
   - the failure no per-task check can see.

## Promotion (the promote verb's pass, owner ruling)

`research.py promote --workspace <ws> --record <id>` copies a VERIFIED record
(+ its quarantined sources, citation paths rewritten) into the library and
re-lints it; then `learnings.py resolve --workspace <ws> --entry <queue id> --status promoted --kind knowledge_record --level L0 --reason "<why>" --targets reference/knowledge/records/<id>.yaml`
and the owner's ruling: `maturity: approved` + `approval: {by, date, note}`
(the note = what the rule scales with, U14's standard); re-render any
topology view. A draft checklist promotes the same way and is approved the
same way. Only `approved` (or bench-`proven`) closes a gap at the default
floor - research narrows the gap to `provisional`; the owner closes it.

## Caps and checkpoints (never silent)

`state.json budgets.research`: `per_run` (tasks opened per run, default 6)
and `depth_per_gap` (sources per task, default 4; failed/refused attempts
capped at 3x). Every hit is `status: checkpoint` (exit 1) with a
`state.py decision` + `research_checkpoint` event; the fix is the owner's:
raise the budget and re-run, or accept designing under the gap (a recorded
decision per slot). `research.py status --workspace <ws>` shows tasks,
verdicts and caps.

## Distributor data

`research.py parts --mpn <mpn> [--provider digikey|mouser|all]` - parametric
values + authoritative datasheet links (lib/distributors.py). Exits 2 naming
the exact missing env vars (HWDE_DIGIKEY_CLIENT_ID + HWDE_DIGIKEY_CLIENT_SECRET,
HWDE_MOUSER_API_KEY) until the owner registers keys.

## Bench hook (research quality)

`bench.py --freeze --stage P1 --fixture <id> --board <b> --from task=<ws>/research/tasks/<t>.json --from-dir research=<ws>/research --freeze-args '{"task": "<t>"}' --grade "<owner verdict>"`
then `--baseline`; the P1 scorer (researchlib.assess) penalizes off-ledger
citations, forum-only records, lint/citation problems, unruled/refuted
records. Owner-graded extraction fixtures accumulate from teaching sessions
(the learn verb, stage P1).

## Do not

- Do not fetch outside `research.py fetch`, and never work around an
  allowlist refusal - name the host in OPEN for the owner to add.
- Do not let the researcher verify its own records, and never edit a record
  to make it pass the second reader - refute it and say why.
- Do not raise maturity by hand: draft -> verified is the reader's verb,
  verified -> approved is the owner's, approved -> proven is bring-up's.
