# From research to checks

hwde follows current EE research and turns what holds up into tests,
scripts, simulations and design guidelines. The person asked for it on
10-30: "find all the cutting edge research and work, and turn it into tests
and scripts and simulations, and then also design guidelines and learnings".
They also said: "Dont blindly trust everything u find out there". So a paper
is a lead to check, never a fact. A claim becomes a guideline only once
hwde's own model, simulation or measurement has reproduced it.

`hwde papers` (scripts/papers.py) runs the pipeline. The ledger is
[`reference/research/papers.yaml`](../../reference/research/papers.yaml) and
the topics are [`reference/research/watch.yaml`](../../reference/research/watch.yaml).

## The loop

1. **Scan.** `hwde papers due` lists the topics whose interval has passed.
   `hwde papers scan --due --add` searches OpenAlex for each one's queries.
   OpenAlex covers journals, conferences and arXiv, and needs no key. Works
   the ledger hasn't seen go in as `candidate`, with their access and trust
   signals. A topic is one question hwde answers from folklore today, plus
   the tools its answer would sharpen.
2. **Triage.** `hwde papers triage ID --keep|--drop --why ".."`. Keep what a
   named hwde tool could check or use, and say which. Drop what is off-topic
   or chip-level. A dropped paper stays in the ledger so no scan brings it back.
3. **Get a legitimate copy.** `hwde papers fetch ID` tries each open copy
   OpenAlex listed, in turn, and keeps the first real PDF in the cache
   (`~/.cache/hwde-papers`, outside the repo, because the copies are the
   publishers' and authors'). It writes the PDF's text alongside.
   `hwde papers access ID --write` looks again through OpenAlex, then
   Unpaywall (set `HWDE_RESEARCH_MAILTO`; Unpaywall wants a real address),
   then Semantic Scholar. A host whose bot wall turns the agent away is
   recorded as `blocked`, and the agent doesn't try to get past it. Shadow
   libraries (Sci-Hub, LibGen, Z-Library, Anna's Archive, mirrors) are
   refused wherever a URL comes in, and `lint` fails on any in the ledger.
4. **The person's list.** A kept paper with no open copy, or with only
   blocked ones, goes on [paywalled.md](paywalled.md). The person may have
   institutional access or a browser. With their copy, `access.state:
   person_supplied` puts the paper back in the reading queue. The copy is
   never committed.
5. **Read and claim.** The reader ([agents/paper-reader.md](../../agents/paper-reader.md))
   writes the paper's checkable claims into the ledger: the claim, where it
   was read (page, section, table, equation), and `read_from: full_text` or
   `abstract`. A claim the paper repeats from another source says so, and
   the primary goes on the queue.
6. **Check.** Each claim gets hwde's own verdict:
   - `reproduced`, `partial` or `refuted` needs `evidence` and a
     `finding`. The evidence is a test (`tests/<file>.py::<test>`, whose
     source names the claim id) or a script or sim file that names it.
     `tests/test_research_claims.py` holds the tests: each computes what
     hwde's own model says (check_current's IPC-2152 table, check_pdn_z's
     branch model, lib/mlcc.py's bias curve...) and asserts the verdict. A
     test that starts failing means hwde's model moved or the claim was
     misread, and the ledger entry needs another look.
   - `untested` needs a `plan`: the test, script, simulation or coupon board
     that would check it.
   - `not_checkable` needs a `why`.
7. **Guideline.** A reproduced or partial claim may carry a `guideline`
   (what a designer should do, within the scope hwde checked).
   [guidelines.md](guidelines.md) is built from them, and
   [claims.md](claims.md) lists every claim with its verdict.
   `hwde papers docs` rewrites both, and the test fails when they're stale.
   A guideline that changes how hwde should design goes on as a check, a
   recipe or a playbook rule, filed as its own brief when it's bigger than
   the claim.

`hwde papers queue` prints the work in order: triage, read, test, the
paywalled list and the blocked list. `hwde papers lint` holds every promise
above, and `bin/hwde test` runs it.

## Trust

Each paper carries OpenAlex's signals: retracted, peer-reviewed venue,
source type and citations. They say how hard to look, not what to believe.
A retracted paper's claims can't become guidelines. A paper with odd signs
(a paid-index "impact factor", data too clean for the method, text that
disagrees with its own tables) gets a `trust.note`. The 2026 trace-fuse
paper in the ledger is the worked example. Its "IPC-2152" ratings are 1.5
to 2x what hwde's IPC-2152 table allows. Its stated overload exponents
(-1.7 and -2.3) disagree with its own Table 1 (3.1 to 4.0). Both are
refuted by tests, and its fuse-temperature claim waits on a coupon board.

## Keeping it standing

The topics carry `every_days` and `last_scanned`, so `hwde papers due` says
when a scan is owed. A sweep or lead that finds topics due runs `scan --due
--add` and the triage, then files a brief for any reading or checking
bigger than a sitting. The ingestion runs (`docs/ingest/`, the EE map) and
design reviews add topics whenever they find hwde answering a question
from folklore.
