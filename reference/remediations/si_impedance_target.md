# si_impedance_target

A transmission-line net or pair misses its impedance target: a declared
si.nets[net].z0_ohm (default tolerance 20%), or a pair class's standard
differential impedance (USB 90, 100BASE-TX 100, tolerance 15%). Twice the
tolerance is an error.

- Emitted by: scripts/check_si.py   Gate: verify
- Fields: net (pair: pair), z0_ohm or zdiff_ohm, target_ohm, miss_pct.

## Fix
rules_gen / route_critical draw the width (and gap) a stackup profile gives
for the target; reroute the net at it. A pair the router drew uncoupled
reads as si_pair_uncoupled instead.
