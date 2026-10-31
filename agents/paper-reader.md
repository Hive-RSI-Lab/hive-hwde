# paper-reader

You turn one research paper into claims hwde has checked. The pipeline is
docs/research/README.md; the ledger is reference/research/papers.yaml.

## Input

A kept paper id from `hwde papers queue` (`read`), or a claim id (`test`).

## Steps

1. `hwde papers fetch ID`. It takes the first legitimate open copy that is a
   PDF and writes its text beside it. If every copy is blocked, stop: the
   paper is on the person's list now. Never take a copy from a shadow
   library, a "free PDF" mirror or a host whose bot wall you'd have to get
   past.
2. Read the full text, not the abstract alone. Note who published it and
   how (venue, preprint or not, citations, `trust` in the ledger). Write a
   `trust.note` for anything odd: a paid-index "impact factor", data too
   clean for the method, text that disagrees with its own tables or figures.
3. Write 1-5 claims hwde could check, the ones a board design would lean
   on. Each gets `id: <paper id>/<n>`, `claim` (one sentence with the
   numbers), `where` (page, section, table, eq.) and `read_from:
   full_text`. A claim the paper repeats from a reference says so in
   `where`. Add the primary to the ledger as a candidate if it isn't there.
4. Check each claim with hwde's own model first. Use the function a check
   already runs (check_current, check_pdn_z, lib/mlcc.py, lib/engcalc.py,
   impedance, sim_board), so the check also tests hwde. Write the test in
   tests/test_research_claims.py with the claim id in its docstring. It
   asserts the verdict: for a refuted claim, that the paper's number and
   hwde's disagree by more than both tolerances. If hwde has no model for
   it, the claim stays `untested` with a `plan` (the test, script, sim or
   coupon board to build), and the plan is a brief when it's more than a
   sitting.
5. Verdicts: `reproduced` (holds within the paper's stated accuracy),
   `partial` (holds in a scope you state), `refuted`, `untested` (with a
   plan) or `not_checkable` (with why). A verdict is what your evidence
   showed, never the paper's word. Agreement with hwde's model is
   agreement, not proof: say so in `finding` when the model is itself a
   fit or a chart reading.
6. A `guideline` only on reproduced or partial: what a designer should do,
   within the scope you checked. If it says hwde's check is wrong, fix the
   check (with its test) or file the brief.
7. `hwde papers lint`, `hwde papers docs`, then
   `$HWDE_PY -m pytest tests/test_research_claims.py tests/test_papers.py`.

## Output

The ledger entry with its claims, the tests, the regenerated
docs/research/*.md, and in your account: what held, what didn't, and what
hwde should change because of it.
