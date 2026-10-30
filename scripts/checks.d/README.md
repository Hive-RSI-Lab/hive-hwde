# checks.d - drop-in check registration

A new verify check joins `verify_all.py`, the `verify` gate and the scorecard
by adding one file here, so no check row edits `verify_all.py` (TEST-12,
docs/failure-modes.md section 4). The script itself lives one level up, as
`scripts/<name>.py`, and follows the SPEC.md section 6 contract with a
`run(argv) -> (payload, out)` function, as the built-in checks do.

```yaml
# scripts/checks.d/check_foo.yaml
name: check_foo                       # must equal the file stem
needs: [constraints]                  # skipped (strict: a coverage failure) without these
args: [--constraints, "{constraints}"]
optional:                             # appended only when that input exists
  parts: [--parts, "{parts}"]
```

- Inputs are `constraints`, `decoupling` and `parts`; `--pcb` and `--out` are
  always passed. A placeholder in `args` must name an input in `needs`, and
  one under `optional.<key>` only `<key>`.
- `verify_all.load_checks` refuses the whole suite on a bad fragment: an
  unknown key or input, a name that differs from the file stem or is already
  registered, or a missing script.
- Fragments load in file-name order, after the built-in checks.

The check's planted fault registers the same way, as
`tests/golden/manifest.d/<name>.yaml` holding one `mutants:` mapping in the
shape `tests/golden/manifest.yaml` uses (board, script, check, defect, modes,
expect). A mutant name already in the manifest is an error. The mutation
script and its committed output go in `tests/golden/mutations/` and
`tests/golden/mutants/<mutant>/` as before. Do not re-record the scorecard
from a check row; the scorecard-record row does that once.
