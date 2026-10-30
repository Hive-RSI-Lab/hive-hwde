# What differs from ihsan-sa/hwde

`.claude/skills/hwde/` is ihsan-sa/hwde's `.claude/skills/hwde/` at
`5d7bbe52483ca0bc6bf28a56e4c1e0d24b5ca4b7`, at the same path, because its
scripts locate each other and the repo root by that depth. It differs in
these ways:

- `reference/knowledge/sources/*.pdf` (36 MB of manufacturers' datasheets and
  application notes) is left out. NOTICE says they're redistributed under
  their owners' terms, not MIT.
- `scripts/lib/knowledgelib.py`: `SOURCES_DIR` reads `HWDE_KNOWLEDGE_SOURCES`
  when it's set, so the records can still find those PDFs elsewhere:
  `docs/knowledge-sources.txt` lists them (git blob sha, size, name) and
  `bin/sources.py` fetches them from upstream into HOME.
- `scripts/parts_search.py --kind/--category` (with
  `reference/part_kinds.yaml` and `category_search` in
  `scripts/lib/partslib.py`): finding a part by what it is, by browsing its
  JLC category, since the keyword search only matches part numbers. Tested
  offline by `tests/test_parts_kind.py`.
- Fixes from building boards here (issue #5, hwde-led-test), each tested in
  `tests/test_sourcer_records.py`:
  - `scripts/bom_cpl.py` reads a footprint's LCSC number from `LCSC Part`
    (what lib_pull's easyeda2kicad writes) as well as `LCSC`, and gives a
    refdes-less parts.json line the board refs with its LCSC number.
  - `scripts/check_ratings.py` reads LCSC's resistor rating names
    ("Voltage-Supply(Max)", "Working Voltage") and says which parts.json
    lines carry no attributes.
  - `scripts/check_pdn.py` counts a cap the board puts straight across a rail
    and ground when decoupling.json records none for that rail.
  - `agents/part-sourcer.md` copies the parts_search row whole (attributes
    included) and writes no refdes.
- Fixes the Hive made while building boards, each worth sending upstream:
  `scripts/check_courtyard.py` (+ `checks.d/check_courtyard.yaml`), a verify
  check that fails a courtyard that misses its own pads; `scripts/lib/fpfix.py`
  rule E, which `lib_pull` runs on every pull, and its `--pcb` mode for placed
  copies; `scripts/board_edit.py`, whose `--outline fit` counts silk and whose
  edits refuse to clip it; `scripts/check_silk.py`, whose silk items carry
  their footprint's reference. From routing lite-main (hifi-receiver-2), a
  dense 2-layer power board: `stitch_vias` and `planes_gen` read KiCad's
  hole_to_hole as drill edge to drill edge; `route_auto` carries the hole
  rules into the DSN (`routelib.dsn_hole_floor`, `ses_restore_vias`,
  `dsn_npth_keepouts`) and keeps the best of `--tries` shuffled-order routes
  (`routelib.dsn_shuffle`); `rules_gen` sizes plane_fed taps, writes
  neck-down rules; `planes_gen` merges touching same-net pours; `scripts/keepout_gen.py`
  (new, with route_swig's `set_rule_areas`) draws copper keep-outs;
  `route_critical` rips only the nets whose copper broke DRC; `lib/geom.py`
  lists NPTH holes (`npth`).
- Two verify checks from hifi-receiver-2's first-spin review (issue #130):
  `scripts/check_straps.py` (+ `reference/reset_states.yaml`), an MCU's
  strapping pins and IO expander pins at power-on and on an MCU-only reset,
  and `scripts/check_antenna.py` (+ `reference/rf_modules.yaml`), copper
  under a module's PCB antenna and its place at the board edge, each with its
  `checks.d` fragment.
- Power-path checks from hifi-receiver-2's first-spin power review, which
  did each by hand: `scripts/check_power_path.py` (+
  `checks.d/check_power_path.yaml`, mutant `power-path-under-contract`), the
  current each pad-track-via chain carries as a max-flow over the copper;
  `scripts/sim_inrush.py` and `scripts/lib/pdsource.py`, a board's load
  switches against a USB PD source; `scripts/soa_check.py` and
  `scripts/lib/soa.py` with `reference/fet_power.yaml`, a FET's peak
  junction temperature through a pulse; `scripts/mlcc_dcbias.py` and
  `scripts/lib/mlcc.py` with `reference/mlcc_dcbias.yaml`, ceramics at DC
  bias (sim_board's DC-bias warning reports the derated value).
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

`requirements.lock`, `LICENSE` and `NOTICE` are upstream's, from the repo
root, and so is `docs/failure-modes.yaml` (`bench --scorecard-corpus` maps
findings to failure modes through it). `tests/` is upstream's `tests/`
except `check.sh` and the tests of what stays upstream: evals
(`test_e2e_electrical`, `test_eval_sandbox`, `test_eval_writeset`), the
showcase (`test_showcase_gallery`), `LEARNINGS.md` and its triage
(`test_learnings`, `test_remediations`), the docker and CI pins
(`test_kicad_pin`) and `docs/check-scorecard.jsonl`
(`test_check_scorecard`). `tests/conftest.py` adds `KNOWN_FAILING`, and
`tests/test_workarounds.py`, `tests/workarounds/` and
`tests/test_parts_kind.py` are the module's own, as is `pytest.ini` (upstream's pytest settings from its `pyproject.toml`).
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
again, re-apply the `SOURCES_DIR` line and the fixes above (or check upstream
has them), and bump the commit above and in `docs/knowledge-sources.txt`
(its lines are the GitHub contents API's `sha`, `size` and `name` for the
sources folder at that commit). Copy upstream's `tests/` over this one
the same way, leaving out the files above and keeping `KNOWN_FAILING`,
`test_workarounds.py` and `test_sourcer_records.py`.
