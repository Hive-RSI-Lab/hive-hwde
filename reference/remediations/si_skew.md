# si_skew

The nets of a source-synchronous group (RMII TX or RX with the reference
clock, or a group in constraints si.groups) spread in flight time by more
than 10% of the group's unit interval.

- Emitted by: scripts/check_si.py   Gate: verify (warning)
- Fields: net (the slowest), group, spread_ps, budget_ps.

## Fix
Lengthen the fast members (a meander on the shortest), or reroute the slow
one shorter. The report's `nets` gives each member's delay_ps.
