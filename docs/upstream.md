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
- A shared tools folder (`HWDE_TOOLS_DIR`, the Hive's `/hive/tools`) is
  searched the way upstream searches its own `tools/`, so the skill finds
  the Hive's tools itself and `bin/hwde` needs no wrappers:
  - `scripts/lib/env.py`: `kicad_installs` takes KiCad AppImage AppDirs at
    `<tools>/kicad-*/` (`bin/kicad-cli` runs as is); `find_kicad_python`
    takes the AppDir's `bin/python3.<n>`; `find_ngspice_dll` its
    `lib/libngspice.so`; `find_krt` also looks under
    `<tools>/kicad-routing-tools/KiCadRoutingTools-*/plugins` and picks the
    newest by version, not by string.
  - KiCad 10 ships its stock symbols as one-file-per-symbol `.kicad_symdir`
    folders, which kicad-sch-api 0.5.6 can't read. `env.py` flattens a
    library into `~/.cache/hwde/symbols/<key>/` the first time it's needed
    (`stock_symbol_lib`, again when the symdir is newer):
    `export_kicad_symbol_dir` does `power` and `Device` and points
    `KICAD_SYMBOL_DIR` there; `register_stock_libs` adds any other.
    `schlib.py` calls it before each placement and `--pins`, and
    `schem_refdes.py` before its round trip (whose save drops symbols the
    cache can't resolve).
  - `scripts/kc.py`: a comment only. The AppDir's 3D loaders match
    `render_env`'s `usr/lib/*/kicad/plugins/3d` through the AppImage's own
    link once kicad-cli is the AppDir's, not a wrapper.
  - `scripts/check_env.py` checks the stock `power`/`Device` symbols
    (fail), the 3D loaders (warn) and KiCadRoutingTools (warn).
  - `scripts/route_critical.py`: the missing-KRT message names the shared
    layout.

  `tests/test_env_lookups.py` covers these lookups (`$HWDE_PY -m pytest
  tests`). They're meant to go upstream.

`requirements.lock`, `LICENSE` and `NOTICE` are upstream's, from the repo
root, and so is `docs/failure-modes.yaml` (`bench --scorecard-corpus` maps
findings to failure modes through it). `tests/` is upstream's `tests/`
except `check.sh` and the tests of what stays upstream: evals
(`test_e2e_electrical`, `test_eval_sandbox`, `test_eval_writeset`), the
showcase (`test_showcase_gallery`), `LEARNINGS.md` and its triage
(`test_learnings`, `test_remediations`), the docker and CI pins
(`test_kicad_pin`) and `docs/check-scorecard.jsonl`
(`test_check_scorecard`). `tests/conftest.py` adds `KNOWN_FAILING`, and
`tests/test_workarounds.py`, `tests/test_env_lookups.py` and `tests/workarounds/` are the module's own,
as is `pytest.ini` (upstream's pytest settings from its `pyproject.toml`).
The plans, docker setup and design notes stay upstream.

Everything else specific to the Hive is in `bin/` and `docs/`: `bin/hwde`
sets `HWDE_TOOLS_DIR` to the Hive's tools folder, builds the venv, and runs
scripts under Xvfb with GLX off.

To update, copy upstream's skill folder over this one, drop the source PDFs
again, re-apply the changes above that upstream hasn't taken, and bump
the commit above. Copy
upstream's `tests/` over this one the same way, leaving out the files above
and keeping `KNOWN_FAILING`, `test_workarounds.py` and `test_env_lookups.py`.
