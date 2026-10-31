# si_unterminated_clock

An edge-sensitive net (a clock: oscillator output, MCU serial clock) is longer
than its critical length - delay > t_rise / 6 on the board's own stackup - with
no series resistor in it. Each edge reflects off the far end and rings back
through the receiver's threshold: a double clock.

- Emitted by: scripts/check_si.py   Gate: verify (warning)
- Fields: net, length_mm (longest pad-to-pad path), critical_mm, t_rise_ns,
  si_class, z0_ohm, refs (a 0-ohm link it found and did not count).

## Is it real?
- The class's edge is the fastest its drivers make (oscillator 1 ns, MCU pin
  2 ns). A slower driver (a part whose datasheet gives its edge) is
  `constraints.json` si.nets[net].t_rise_ns, not a waiver.
- A net driven through a buffer the check can't see as a resistor (a ferrite,
  a level shifter) reads as unterminated; say so in a waiver.

## Fix ladder
1. Put a resistor in series at the DRIVER pin, R ~ Z0 - R_driver (the message
   gives it; 22-33 ohm for a 50-75 ohm line). It goes in the schematic, then
   board_update; place it within a few mm of the driver.
2. Shorten the net below critical_mm (place the loads nearer the driver).
3. Several loads in a row: a series R still helps the last; loads strung
   along a long trunk want short stubs (Johnson, "Series Termination").
