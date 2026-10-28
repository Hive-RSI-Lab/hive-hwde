# Design documents - filing and the P10 documents

`report_gen.py` assembles state.json + digests + reports + renders
into the design doc; without pdflatex it degrades to .tex-only (check_env
warns). Only the FINAL design doc is filed in the document register: run that
one with `--file`, and give the revision a note with `--note "<text>"`
(passed to `cc-docs file --note`). When the library already holds this board's document of
that kind (found read-only in cc-docs' register: its source under the board's
`reports/<kind>/`, or its last revision describing the part number), the
filing reuses that document's project and exact title, so a rebuild from any
worktree is its next revision and never a new number. Else it files into the
project `Boards` (002), or `DOC_PROJECT=<project>`; a per-board
`Boards/<PN> <name>` is refused by cc-docs while 002 is a project named
`Boards` outside any group. A new title leads with the part number
(`PCB-0022-B nfc-card design doc`), so a new board revision never files over
the last one's documents. Never export DOC_PROJECT for the
session, or every run files. An unchanged rebuild
files nothing. Every filed document also carries the board's fab set as
supporting files, each name led by the workspace directory's name:
`<ws>_gerbers.zip`, `<ws>_BOM.csv`, `<ws>_CPL.csv` and the distributor BOMs
when present; a fab set that changed under an unchanged PDF still goes up. At P10 close (or when the owner asks) every board also gets two
more documents, each filed as its own: `--kind highlight` (a few pages: what
it is, a picture, BOM, the decisions that changed it, checks) and `--kind full
--render-history` (the design doc plus a render of each routing snapshot,
every decision and why, the run's history and a diagram-maker figure of how it
went). Once the board's boards PR has squash-merged, pass `--history-ref <its
track branch>` so the history reads the run's own commits.

Waivers the owner accepted are shown in the documents with no extra work:
the design and full docs list every entry of `reports/verify-waivers.json`
under Verification, and the highlight lists them under Checks and Cost,
each with what fires, where, the reason and its `approved` line. So a
waiver accepted on condition it is "flagged in the documentation" needs
only its waiver entry, not a hand-written review note or a decision.
