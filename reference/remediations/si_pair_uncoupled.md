# si_pair_uncoupled

A differential pair longer than its critical length is not routed as a
coupled pair (no measurable edge gap between the halves), so its
differential impedance is not held at all.

- Emitted by: scripts/check_si.py   Gate: verify (warning)

## Fix
Route the pair together (route_critical's diff-pair pass), at the stackup
profile's width and gap for the target; or shorten it below the critical
length, where coupling stops mattering (USB FS under ~100 mm).
