# sense_tap_missing

`check_kelvin` walked the sense line declared in constraints.json `power[].sense_pads`
from its sense pad and never reached the pad it must read: the line is not routed,
or it reaches that pad only through other copper.

- **Fix:** route the sense line from the sense pad to the named pad on a track of its
  own that touches nothing else on the way.
- **Wrong pad named?** Fix `sense_pads` to the pad the line really ends on.
