# si_crosstalk_sim

check_si simulated one of its worst crosstalk estimates (scripts/sim_si.py:
the routed pair as coupled lines with its real drivers and loads) and the
noise at the victim's receiver passes the budget: 5% of the aggressor's swing
for a digital victim, 1% for an analog one or a crystal.

- Emitted by: scripts/check_si.py, scripts/sim_si.py   Gate: verify (warning)
- Fields: aggressor, net (victim), noise_mv, estimate_mv, budget_mv, margin_mv,
  over_margin, coupled_mm, pos, layer. `crosstalk_sim` in the report has each
  end's peak, the sections, and which orientation was worst.
- Model: T lines and coupled sections (2x2 L, C on the stackup); driver
  R_drv + series R behind a linear ramp t_r long; receiver R_in || C_in. No IBIS
  (#486), no loss above DC, no third conductor in the cross-section.

## Is it real?
- `driver_assumed` true means nobody said which end drives, so the worst of
  every orientation was kept. Lines driven from opposite ends make an
  interdigital resonator, the worst case by far. Declare
  `si.nets[net].driver` ("U1" or "U1.3"): co-directional buses often pass.
- Lossless ringing sets the peak on unterminated lines, and a ramp's corners
  excite it more than a real edge does. Past the budget and inside `margin_mv`
  (`over_margin` false) is a look, not a failure. `si.nets[victim].noise_margin_v`
  sets the margin, `c_in_pf` and `driver_ohm` the ends.
- `sim_over_estimate` above 1 is normal for a receiver: the estimate takes matched
  ends, and an open far end doubles what arrives.

## Fix ladder
1. Series-terminate the aggressor (R + R_drv ~ Z0): it launches half the swing,
   slows nothing much, and stops the ringing.
2. Spread them (3x the height to the plane) or shorten the parallel run.
3. Add a ground guard stitched to the plane, or move one to another layer with a
   plane between. On a 2-layer board the plane is 1.5 mm off: nothing else helps
   as much as giving these nets a nearby return.
