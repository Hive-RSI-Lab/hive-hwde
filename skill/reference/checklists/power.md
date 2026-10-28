# Checklist: power (any board with a regulator or >1 rail)

- Output cap TYPE/ESR vs the regulator's stability requirement (AMS1117-class
  wants tantalum/controlled-ESR; many LDOs NEED ceramic; bucks per datasheet).
- Input cap present at every regulator input pin, close (loop area).
- Dropout/headroom at WORST-case low line + full load, minus any series
  protection drop; check the datasheet MAX dropout column, not typ.
- Abs-max input vs the applied source (incl. reversed/transient cases).
- Every consumer's per-pin decoupling vs ITS datasheet (count per power pin,
  bulk placement, analog-rail pairs) - not just "some caps".
- Feedback divider values/placement per topology; enable/PG pins strapped.
- Buck: hot-loop parts on one layer; SW-node copper minimal; bootstrap cap.
- Rail sequencing needs; inrush vs source limits (USB: cite the 10uF VBUS
  soft-start expectation).
- Power-path stubs declared width-only in constraints must carry "pdn": false.
