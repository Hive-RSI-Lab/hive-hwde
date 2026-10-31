# si_impedance_step

A transmission-line net has two parts, each longer than its critical length,
whose Z0 differ by more than 0.10 in reflection coefficient: a neck-down, a
width change, or a layer change onto a different dielectric.

- Emitted by: scripts/check_si.py   Gate: verify (warning)
- Fields: net, gamma, layer (the higher-impedance part), message names both.

## Fix
Route the whole net at one width per layer chosen for the same Z0
(impedance.solve_width gives the width for a Z0 on each layer), or keep the
odd part shorter than the critical length.
