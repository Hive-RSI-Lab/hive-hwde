# sense_tap_off_pad

`check_kelvin` walked the sense line declared in constraints.json `power[].sense_pads`
from its sense pad and found it touching other copper on the rail before the pad it
must read: a pour, another pad, or a power track or via. The regulator (or monitor)
reads the voltage where it joins, not at the named pad, so the drop between the two
at full current goes uncorrected. `tap_mm` is how far from the named pad that join reaches.

- **Fix:** run the sense line from the sense pad to the named pad on a track that
  carries nothing else, and keep the rail's pour off the sense pad and that track
  (a keepout or a zone cutout round them, or move the part out of the pour). The
  power track and the sense track may both end on the named pad. `route_critical`
  does this itself for each `sense_pads` entry (#1043): re-run it on a board whose
  rail it routed before the entry was there.
- **A divider top in the rail's pour** (the case this check was written for) reads
  the pour under it: clear the pour from it, then route its own track to the pad.
- **Wrong pad named?** Fix `sense_pads` to the pad the rail must be held at (the
  output capacitor, or the load's own pin for remote sense).
- **Don't** widen the sense track or stitch it to the pour: that makes the join bigger,
  not the reading better.
