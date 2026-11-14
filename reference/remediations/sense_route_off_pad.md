# sense_route_off_pad

`route_critical` laid the sense line declared in constraints.json `power[].sense_pads`
on copper of its own (#1043), and after the final pour refill its walk
(`check_kelvin.sense_walk`, the same one `check_kelvin` runs) found the line joining
rail copper before the pad it must read, or never reaching it. Most often the refill
filled a gap the `aiee_sense_*` keepout left, or a later pass laid rail copper across
the line.

- **Fix:** look at the join the message names. If it is the pour, widen the
  `aiee_sense_*` rule area round the line (or add a zone cutout) and refill; if it
  is a track or via, move that piece off the line or reroute the sense line round it.
- **Then** run `check_kelvin` with the board's constraints.json: it gives the mm
  from the tap and stays the gate (`sense_tap_off_pad`).
- **Don't** delete the line and leave the rail router to connect the sense pad: it
  tees the pad into the rail tree, which is the fault this check exists for.
