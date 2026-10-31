# si_crosstalk

A fast net (the `aggressor`) runs beside another signal net (`net`, the
victim) on the same layer close enough and long enough that its estimated
near- or far-end noise passes the victim's budget: 5% of the aggressor's
swing for a digital victim, 1% for an analog one or a crystal.

- Emitted by: scripts/check_si.py   Gate: verify (warning)
- Fields: aggressor, net (victim), victim_class, next_pct, fext_pct,
  noise_mv, coupled_mm, min_gap_mm, budget_pct, pos (closest point), layer.
- Model: each 0.25 mm of the aggressor, the nearest near-parallel victim
  track's edge gap is solved as a coupled pair on the stackup
  (lib/impedance.coupling); NEXT = min(max kb, sum(kb dx) / (v t_r / 2)),
  FEXT = |sum(kf dx)| / (v t_r) (Bogatin ch. 10).

## Is it real?
- The victim class comes from the name (analog: audio, ADC/DAC, VREF,
  sense) or constraints si.victims. A digital net named like an analog one
  takes `"victims": {"/NET": "digital"}`.
- The swing is 3.3 V unless si.nets[aggressor].swing_v says otherwise.

## Fix ladder
1. Spread them: at 3x the height to the plane the coupling falls to a few
   percent; route_edit the victim or aggressor apart over the coupled run.
2. Shorten the parallel run (below v t_r / 2 the NEXT is proportional to
   length).
3. Put a ground trace or pour stitched to the plane between them.
4. Move one to another layer with a plane between.
