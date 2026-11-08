# rating_diode_forward

A diode is wired so it conducts all the time: its anode sits on a stated rail and
its cathode on ground, or a TVS or Zener clamp has its anode above its cathode. A
unidirectional clamp should block its rail (cathode to the higher net) until the
rail passes its breakdown; fitted backwards it clamps the rail at one diode drop,
gets hot, and every ratings check still passes because the reverse bias is
negative (#645).

- Emitted by: scripts/check_ratings.py (kind="rating_diode_forward")   Gate: verify
  (P8, via verify_all.py); runs from P4 on.
- Fixer domain: schematic (cluster_violations.py).
- Fields: net (the anode's), refs [the diode], pin (the anode pad), net_v (the
  forward volts), limit_v (0.5 V), rating (how the cathode was found). Error.

## Is it real?
- Read the `[...]` at the end of the message. "pad 1 taken as the cathode" means
  the footprint's pads carry no K/A names; check the footprint's pad 1 really is
  the cathode (most diode and LED footprints, but not every vendor's).
- A clamp named a TVS only by its MPN family: check the part is not bidirectional
  (an SMBJ...CA is). A bidirectional part should carry a `Polarity` attribute.
- A rail-to-ground diode meant to conduct (a shunt reference, a deliberate bleed)
  has a series resistor; with none, it is a short.

## Fix ladder (cheapest first)
1. Rotate the diode 180 degrees or swap its symbol pins so the cathode meets the
   higher net.
2. A wrong footprint pin map (pad 1 the anode): fix the symbol-to-footprint mapping.
3. A clamp across two rails: re-check which rail it protects and tie its anode
   to ground.

## Do not
- Do not mark the part bidirectional in parts.json to silence it.

## Verify
```
bin/hwde check_ratings --pcb <ws>/kicad/<board>.kicad_pcb --constraints <ws>/kicad/constraints.json
```

## Sources
- scripts/check_ratings.py docstring; hive-hwde issue #645.
