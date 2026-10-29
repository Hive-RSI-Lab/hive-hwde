Frozen hwde workspace for `tests/test_mcp_server.py`: a P0 `state.json` for
board `bb-adc`. The test copies it to a temp dir and puts the
`tests/fixtures/bb_adc` board and sidecars in its `kicad/`, so no board is
stored twice. Do not regenerate it; the tests pin its contents.
