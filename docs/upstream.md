# What differs from ihsan-sa/hwde

`.claude/skills/hwde/` is ihsan-sa/hwde's `.claude/skills/hwde/` at
`5d7bbe52483ca0bc6bf28a56e4c1e0d24b5ca4b7`, at the same path, because its
scripts locate each other and the repo root by that depth. It differs in
these ways:

- `reference/knowledge/sources/*.pdf` (36 MB of manufacturers' datasheets and
  application notes) is left out. NOTICE says they're redistributed under
  their owners' terms, not MIT.
- `scripts/lib/knowledgelib.py`: `SOURCES_DIR` reads `HWDE_KNOWLEDGE_SOURCES`
  when it's set, so the records can still find those PDFs elsewhere.
- Fixes the Hive made while building boards, each worth sending upstream:
  `scripts/check_courtyard.py` (+ `checks.d/check_courtyard.yaml`), a verify
  check that fails a courtyard that misses its own pads; `scripts/lib/fpfix.py`
  rule E, which `lib_pull` runs on every pull, and its `--pcb` mode for placed
  copies; `scripts/board_edit.py`, whose `--outline fit` counts silk and whose
  edits refuse to clip it; `scripts/check_silk.py`, whose silk items carry
  their footprint's reference.
- A live stock-and-match check on every BOM row (the person, 10-28: "didn't
  the hive pull the JLC and LCSC?"): `scripts/stock_check.py` and
  `scripts/lib/stocklib.py` read JLC's and LCSC's own record for each code
  (what JLC can still sell, whether it sells the code at all and what its BOM
  review would swap in, whether the model is the BOM's part), and `bom_cpl.py`
  fails a row JLC would not place as written (`dfm_check`'s scratch run and
  `--offline` skip it and say so).
- A fact ledger per board ("know what we dont know", the person 10-28):
  `scripts/lib/ledgerlib.py` and `scripts/ledger.py` keep `ledger.json`,
  every fact the design relies on as verified (source, date), failed or
  unknown; `stock_check`, `bom_cpl`, `check_ratings` (rating, and a new pinout
  comparison against the extraction) and `cpl_verify` (footprint) write it,
  and `lib/releaselib.py` refuses an attestation, and drops order-ready,
  while any fact is not verified now.
- The Hive's rules are judgment, not refusals (the person, 10-28): `state.py`
  `set-phase` warns instead of refusing an unrecorded gate, `budget
  --consume` and `cross-spawn` count past zero (`over: true`), `research.py
  open --slot` opens past the per-run cap, `board_init` notes a fixed outline
  under a geometry-as-output mode instead of refusing it, and the release
  attestation no longer needs an approved H4 (a rejected one still vetoes).
  `SKILL.md`, `full-run.md`, `fix-finding.md`, `build-modes.md` and
  `tasks.yaml` say so: H1-H4 are reports, and only error waivers, cost/feature
  trades and the order go to the person.
- `agents/board-guide.md` finds pdf-material-builder through
  `HWDE_PDF_MATERIAL_BUILDER` before `~/.claude/skills/`, and says how to
  build the guide on a machine without it.
- `scripts/report_gen.py` (`STAGE_REPORTS`) and `scripts/guide_facts.py`
  read a stage's report under the names runs have used as well as the
  recipes' one, and `agents/dfm.md`, `recipes/dfm-check.md` and
  `recipes/full-run.md` give each report's name (dfm.md's command lines also
  had `--out` for `--out-dir` and `--parts-json` for `--parts`).

`requirements.lock`, `LICENSE` and `NOTICE` are upstream's, from the repo
root, and so is `docs/failure-modes.yaml` (`bench --scorecard-corpus` maps
findings to failure modes through it). `tests/` is upstream's `tests/`
except `check.sh` and the tests of what stays upstream: evals
(`test_e2e_electrical`, `test_eval_sandbox`, `test_eval_writeset`), the
showcase (`test_showcase_gallery`), `LEARNINGS.md` and its triage
(`test_learnings`, `test_remediations`), the docker and CI pins
(`test_kicad_pin`) and `docs/check-scorecard.jsonl`
(`test_check_scorecard`). `tests/conftest.py` adds `KNOWN_FAILING`, and
`tests/test_workarounds.py` and `tests/workarounds/` are the module's own,
as is `pytest.ini` (upstream's pytest settings from its `pyproject.toml`).
The plans, docker setup and design notes stay upstream.

Everything specific to the Hive is in `bin/` and `docs/`:

- `bin/hwde` wraps the Hive's KiCad AppImage (kicad-cli, KiCad's python with
  pcbnew, ngspice, the 3D model loaders the render preflight looks for).
  It also builds the venv and runs scripts under Xvfb with GLX off.
- `bin/flatten-symdirs.py` exists because KiCad 10 ships its stock symbols
  as one-file-per-symbol `.kicad_symdir` folders, and kicad-sch-api 0.5.6
  can't read those. It writes flat `power` and `Device` libraries into HOME,
  and `bin/hwde` points `KICAD_SYMBOL_DIR` at them.
- `bin/house-style-fallback/housestyle.sty` defines the few house-style
  commands report_gen writes, on stock LaTeX, for machines without the
  pdf-material-builder skill.

To update, copy upstream's skill folder over this one, drop the source PDFs
again, re-apply the `SOURCES_DIR` line, and bump the commit above. Copy
upstream's `tests/` over this one the same way, leaving out the files above
and keeping `KNOWN_FAILING` and `test_workarounds.py`.
