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
  their footprint's reference. From the first product board
  (hifi-receiver-2 lite-main): `fpfix` rule F numbers an exposed pad's
  unnumbered thermal vias and drops their paste, and `fpfix.body_flags`
  reads each part's 3D model for a peg with no hole, a body outside the
  courtyard or a model off its footprint (`lib_pull` lists them in
  `autofix.flags`); `lib_pull --budget-s` stops before a 10-minute call
  runs out, and a re-run skips parts already whole in the library;
  `schlib.Sheet` registers the project's own symbol libraries from its
  `sym-lib-table`; the ERC gate reports `pin_types` and warns when every pin
  is passive; `board_init --origin` keeps a re-run's outline where the board
  had it; the schematic role prompts give `kc.py`'s positional input.

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

To update, copy upstream's skill folder over this one, drop the source PDFs
again, re-apply the `SOURCES_DIR` line, and bump the commit above. Copy
upstream's `tests/` over this one the same way, leaving out the files above
and keeping `KNOWN_FAILING` and `test_workarounds.py`.
