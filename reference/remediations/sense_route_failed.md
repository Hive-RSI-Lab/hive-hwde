# sense_route_failed

`route_critical` could not lay a sense line from `power[].sense_pads` (#1043) from
its sense pad to its tap pad without touching other copper: every other piece of the
rail (tracks, vias, pours, other pads) is a wall for it, and no path got through. The
sense pad is left unrouted, so `check_kelvin` reports `sense_tap_missing` too.

- **Fix:** make room. Move the divider (or the sense pin's part) nearer the tap pad,
  or onto the side of the tap away from the rail track, then route again.
- **Or** route it by hand: a thin track from the sense pad to the tap pad that
  touches nothing else; `route_critical` leaves a sense pad that already has copper
  alone.
- **Don't** fix it by naming a pad nearer the sense pad in `sense_pads`: the tap is
  the pad the rail must be held at, not the one easiest to reach.
