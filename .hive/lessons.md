# Lessons

What was tried and why it didn't stay. Read before working here.

- 2026-10-31: "hwde: RF layout checks: stubs, electrical length/phase, band isolation, shield cans, reference cutouts" (PR #396, ff7a6801) was reverted: the tests went red after it merged: =========================== short test summary info ============================ | FAILED tests/test_fab.py::test_pick_rules_unknown_raises - Failed: DID NOT RA... | FAILED tests/test_gate_strict.py::test_verify_all_nonstrict_skips_are_visible | FAILED tests/test_gate_strict.py::test_verify_all_strict_missing_inputs_fail | FAILED tests/test_gate_strict.py::test_verify_all_strict_with_declared_na_passes | FAILED tests/test_sourcer_records.py::test_dropped_attributes_are_named - ass... | 5 failed, 3140 passed, 31 skipped, 268 deselected, 12 xfailed in 164.90s (0:02:44) | hwde test: 166s (scratc…
