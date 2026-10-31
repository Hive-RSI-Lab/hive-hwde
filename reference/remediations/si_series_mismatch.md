# si_series_mismatch

A long clock has a series resistor, but the resistor plus a ~20 ohm CMOS
driver misses the line's Z0 by more than 0.35 in reflection coefficient, so
the source still reflects (too small) or the edge slows and the first step
lands mid-threshold (too large).

- Emitted by: scripts/check_si.py   Gate: verify (warning)
- Fields: net, refs (the resistor), z0 in the message.

## Fix
Change the resistor's value to ~Z0 - R_driver (the message gives it). If the
driver's output impedance is known (IBIS model or datasheet), set
si.nets[net].driver_ohm and re-run before changing parts.
