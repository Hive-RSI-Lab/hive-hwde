# kelvin_error

`check_kelvin` solved the DC potential in the shunt net's copper and found a sense tap
above the shunt's pad: the amplifier reads that copper as shunt, so the reading is
high by the figure given and drifts with copper temperature (+0.39 %/K).

- **Fix:** route the tap from the shunt's own pad, on a track that carries nothing else,
  on the side of the pad away from the current. Both taps. A via into a plane near the
  pad is not the pad: the plane sits past the pad's via drop.
- **If the shunt's pads can't take a tap on their far side:** a four-terminal part
  has its own sense pads and ends this finding.
- **Wrong "from" part?** Declare the current in constraints.json `power_paths` with the
  shunt at one end. With no list, the check takes the part that gives the smallest error.
- **Don't** raise a firmware threshold to cover it: the error moves with etch width and
  temperature, and an over-current trip set on it fires at the wrong current.
