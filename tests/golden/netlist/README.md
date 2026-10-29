# Golden netlist (hwde -> fwe contract)

`usbbuck4.net` is the kicadsexpr netlist hwde's `kc.export_netlist` writes for
`tests/golden/usbbuck4/usbbuck4.kicad_sch`, exported with kicad-cli 10.0.6
(`env.KICAD_PIN`). The one edit: the top-level `(source ...)` path is made
repo-relative so no machine path is committed.

`usbbuck4.parsed.json` is what hwde's reader, `lib/simlib.parse_netlist`, makes
of it: `{"components": {ref: {value, footprint}}, "nets": {name: [{ref, pin,
pintype, pinfunction}]}}`, dict keys sorted, node lists in export order.

fwe keeps a copy of this reader (`fwelib/netlist.py`) and copies both files to
test it against. `tests/test_golden_netlist.py` holds hwde to them: the reader
must still give the JSON, and a fresh export from the schematic must still read
the same. When that second test fails because KiCad or the schematic changed,
regenerate both files on purpose and tell fwe to copy them again:

    kicad-cli sch export netlist --format kicadsexpr \
      -o tests/golden/netlist/usbbuck4.net tests/golden/usbbuck4/usbbuck4.kicad_sch
    # make the (source ...) path repo-relative, then rewrite the JSON:
    python -c "import json,sys; sys.path[:0]=['.claude/skills/hwde/scripts/lib']; \
      import simlib; print(json.dumps(simlib.parse_netlist( \
      'tests/golden/netlist/usbbuck4.net'), indent=1, sort_keys=True))" \
      > tests/golden/netlist/usbbuck4.parsed.json
