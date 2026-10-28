# resume-phase - pick the run back up

`state.py resume --workspace <ws>` is the answer to "where were we", and it is
the ONLY answer: a killed session must resume from state.json alone.

## Read the freshness fields, not just the pass list

    gates_passed              ever passed
    gates_passed_fresh        passed AND every recorded input hash still matches
                              AND no stale mark
    gates_stale               an edit class marked them (derived artifacts too)
    gates_freshness_unknown   passed before v2 hashing, or inputs unresolvable -
                              honestly unverifiable, never assume fresh
    human_hold_pending        an edit whose ceremony has not been paid yet

Re-run exactly the stale and unknown gates. Never redo a gate that is passed and
fresh - its artifacts are committed, and re-running it costs a full cycle for no
information.

## Open issues

Issues in status `fixing` already have work orders on disk
(`log/workorders/wo-<id>.json`). Re-dispatch those rather than re-clustering the
findings from scratch - the cluster ids and the snapshots line up with them.

## Then

Log the seam (`state.py log --event resumed`) and re-enter the pipeline at the
phase state.json reports, which is the `full-run` recipe entered mid-sequence.
Everything after that point behaves exactly as it would in a fresh run: same
gates, same checkpoints, same fix loop.

## Do not

- Do not re-init a workspace to "clean it up". `state.py init` on an existing
  workspace is not a repair tool; the history IS the audit trail.
- Do not trust a phase number over a gate result. Phase says where the run got
  to; gates say what is actually true about the files on disk.
