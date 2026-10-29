# Where it came from

hwde began as ihsan-sa/hwde's Claude Code skill, `.claude/skills/hwde/` at
`5d7bbe52483ca0bc6bf28a56e4c1e0d24b5ca4b7`. That skill is frozen: this module
doesn't follow it, copy it over itself, or keep its layout. Change, slim or
rename anything here when it earns it.

What moved where:

- `SKILL.md` (the orchestrator playbook) is [playbook.md](playbook.md), with
  the skill's frontmatter gone.
- `scripts/`, `agents/` and `reference/` are at the module root. The scripts
  find the module root one level above `scripts/` (`env.repo_root()`, and
  each lib's `SKILL`/`REPO`, which are now the same folder).
- Upstream's `/hwde` command, the skill's README, the empty `templates/` and
  check_env's vendored-copy check (whether a copy was behind an ai-ee
  checkout) are gone.
- `reference/knowledge/sources/*.pdf` (36 MB of manufacturers' datasheets
  and application notes) was never brought over. NOTICE says they're
  redistributed under their owners' terms, not MIT.
  `scripts/lib/knowledgelib.py`'s `SOURCES_DIR` reads `HWDE_KNOWLEDGE_SOURCES`,
  and `bin/hwde` points it at the hwde project's clone when there is one.
- Fixes the Hive made while building boards, worth offering upstream:
  `scripts/check_courtyard.py` (+ `checks.d/check_courtyard.yaml`), a verify
  check that fails a courtyard that misses its own pads; `scripts/lib/fpfix.py`
  rule E, which `lib_pull` runs on every pull, and its `--pcb` mode for placed
  copies; `scripts/board_edit.py`, whose `--outline fit` counts silk and whose
  edits refuse to clip it; `scripts/check_silk.py`, whose silk items carry
  their footprint's reference.

`requirements.lock`, `LICENSE` and `NOTICE` came from upstream's repo root,
and so did `docs/failure-modes.yaml` (`bench --scorecard-corpus` maps
findings to failure modes through it). `tests/` began as upstream's `tests/`,
without `check.sh` and the tests of what stayed upstream: evals
(`test_e2e_electrical`, `test_eval_sandbox`, `test_eval_writeset`), the
showcase (`test_showcase_gallery`), `LEARNINGS.md` and its triage
(`test_learnings`, `test_remediations`), the docker and CI pins
(`test_kicad_pin`), `docs/check-scorecard.jsonl` (`test_check_scorecard`)
and the vendored-copy check (`test_skill_copy_current`). `tests/conftest.py`
adds `KNOWN_FAILING`; `tests/test_workarounds.py`, `tests/workarounds/` and
`pytest.ini` (upstream's pytest settings from its `pyproject.toml`) are the
module's own. The plans, docker setup and design notes stayed upstream.

Everything else that's specific to the Hive is in `bin/` and `docs/`:

- `bin/hwde` wraps the Hive's KiCad AppImage (kicad-cli, KiCad's python with
  pcbnew, ngspice, the 3D model loaders the render preflight looks for).
  It also builds the venv and runs scripts under Xvfb with GLX off.
- `bin/flatten-symdirs.py` exists because KiCad 10 ships its stock symbols
  as one-file-per-symbol `.kicad_symdir` folders, and kicad-sch-api 0.5.6
  can't read those. It writes flat `power` and `Device` libraries into HOME,
  and `bin/hwde` points `KICAD_SYMBOL_DIR` at them.
