Frozen boards for `tests/test_mating.py`: the owner's two boards whose
connectors could not be mated, each before and after its fix, so the
mating check is tested on real footprints without reading the live boards
repo.

| file | boards repo commit | board |
|---|---|---|
| `lipo_boost_before` | `bd99e487` | PCB-0021-A lipo-boost, J4 USB-A mouth into the board |
| `lipo_boost_after` | `8eb0280b` (#19) | same, J4 turned to face the edge |
| `bldc_motor_driver_before` | `42e9af76` | PCB-0018-A bldc-motor-driver, J701/J702 mouths into the board |
| `bldc_motor_driver_after` | `91e76be2` (#22) | same, J701/J702 turned to face the edge |

Each is trimmed to the header, the Edge.Cuts outline and a few footprints:
lipo-boost keeps J2, J4 and L1 (the inductor in front of J4's old mouth);
bldc-motor-driver keeps J601, J701, J702, F701 and U301 (one part in front
of each old mouth). Tracks, zones and every other part are dropped, and 3D
model paths are rewritten to `${KIPRJMOD}/lib/aiee.3dshapes/<name>`. Do not
regenerate them; the tests pin what they flag.
