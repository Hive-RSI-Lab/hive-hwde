# What differs from ihsan-sa/hwde

`.claude/skills/hwde/` is ihsan-sa/hwde's `.claude/skills/hwde/` at
`5d7bbe52483ca0bc6bf28a56e4c1e0d24b5ca4b7`, at the same path, because its
scripts locate each other and the repo root by that depth. It differs in these
ways:

- `reference/knowledge/sources/*.pdf` (36 MB of manufacturers' datasheets and
  application notes) is left out. NOTICE says they're redistributed under
  their owners' terms, not MIT.
- `scripts/lib/knowledgelib.py`: `SOURCES_DIR` reads `HWDE_KNOWLEDGE_SOURCES`
  when it's set, so the records can still find those PDFs elsewhere.
- `agents/board-guide.md` finds pdf-material-builder through
  `HWDE_PDF_MATERIAL_BUILDER` before `~/.claude/skills/`, and says how to
  build the guide on a machine without it.

`requirements.lock`, `LICENSE` and `NOTICE` are upstream's, from the repo
root. The tests, plans, docker setup and design notes stay upstream.

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
again, re-apply the `SOURCES_DIR` line, and bump the commit above.
