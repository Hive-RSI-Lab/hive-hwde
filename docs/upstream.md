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

`requirements.lock`, `LICENSE` and `NOTICE` came from upstream's repo root.
Its tests, plans, docker setup and design notes stayed there.

Everything else that's specific to the Hive is in `bin/` and `docs/`:

- `bin/hwde` wraps the Hive's KiCad AppImage (kicad-cli, KiCad's python with
  pcbnew, ngspice, the 3D model loaders the render preflight looks for).
  It also builds the venv and runs scripts under Xvfb with GLX off.
- `bin/flatten-symdirs.py` exists because KiCad 10 ships its stock symbols
  as one-file-per-symbol `.kicad_symdir` folders, and kicad-sch-api 0.5.6
  can't read those. It writes flat `power` and `Device` libraries into HOME,
  and `bin/hwde` points `KICAD_SYMBOL_DIR` at them.
