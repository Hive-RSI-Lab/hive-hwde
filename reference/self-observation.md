# Self-observation: the board watches itself

The person, 10-28 22:05: "whatever can be done for minimal increased cost
and complexity to have the board monitor irself and its waveforms is best".
A board the Hive brings up unattended (remote-bringup.md) can only report
what it is wired to measure. A scope on the person's desk is for later;
whatever the board can read about itself for cents, it should.

This is the default for a board with an MCU, judged per board: add what
makes a failure visible, skip what no failure mode needs. A board with no
MCU gets test points and a bench plan instead.

## Start from the failure modes, not a parts list

For each thing the first-spin review (first-spin.md) says could go wrong
or sits on a thin margin, ask: what reading would show it at bring-up, and
can the board take that reading itself? Write the answer as a bring-up item
(name, what is read, the limit) before choosing parts. Examples from
hifi-receiver-2:

| Risk | Reading | On the board |
|---|---|---|
| PVDD switch turns on early or not at all | PVDD voltage per state | divider into an ADC1 pin (GPIO39) |
| PD contract not reached | VBUS, PD power-good | divider into GPIO34; CH224 PG on GPIO4 |
| amplifier gain / clipping / THD / noise wrong | the speaker outputs | 100k/10k divider + 1 uF from each output leg into the codec's spare ADC inputs, ~$0.04 per board |
| amp overheats at 1/8 power | thermal warning, fault | OTW/FAULT pins on GPIO or the expander |
| board resets or brown-outs | reset reason, heap, uptime | firmware, no parts |
| Wi-Fi weak inside the box | RSSI | firmware, no parts |

## The cheap measures, roughly by cost

At JLC, a basic 0402 part carries no extended-part fee and a joint costs
about $0.0017: a divider is under a cent. Extended parts add a one-off fee
per order (about $3), so reuse a part already on the BOM where one fits.

- **Rail sense**: a divider (and a small cap at the ADC pin) from each rail
  that matters into a spare ADC pin. Pick ADC pins that work with the radio
  on (ESP32: ADC1; ADC2 is taken by Wi-Fi). Size the divider so the worst
  case stays under the ADC's range with margin and its current is a
  rounding error.
- **Status pins to GPIO**: every PG, FAULT, OTW, CLIP, INT, ALERT an IC
  offers goes to a GPIO or an I/O expander input, not just a pull-up. If
  pins are short, an I/O expander (TCA9539: no internal pull-ups, resets
  with the MCU) is cheaper than a lost fault.
- **Output loopback**: what the board produces, fed back into an input it
  already has: an audio output into a spare codec ADC, a PWM or DAC output
  into an ADC pin through an RC, a motor or LED current through a sense
  resistor. With test signals the board makes itself, this measures gain,
  level, frequency response, distortion, noise and DC offset with no bench.
- **Current sense**: a shunt and an INA-class monitor on the main input
  where power matters (governors, chargers); a 0R footprint where it might.
- **Temperature**: the hot part's own sensor or warning pin, else an NTC
  divider or a small I2C sensor beside it; the MCU's internal sensor only
  for the box air.
- **Clocks and links**: firmware can count a clock against another
  (crystal vs RTC), run a loopback on a UART or I2S, read a PHY's link
  registers, run iperf.
- **Console and recovery**: a USB-UART bridge with auto-program (or the
  MCU's own USB), so a blank board flashes and provisions from one script;
  a test pad for EN and BOOT anyway.
- **Test points** on every rail and every signal a fault would be read on,
  for the readings the board cannot take (efficiency, ripple, power into a
  real load, a thermocouple at 1/8 power). The bench takes those once.

## What firmware must offer

The board's measurements are only as good as the API that reads them. The
firmware (hive-fw module) carries a small test API, the same over the
console, the LAN and the relay (remote-bringup.md):

- `info` (board, firmware version, unit id), `state` (every reading above,
  with units), `selftest` (items with value, limit and pass, the same
  shape as a bring-up result item), and a `capture` for any loopback tap.
- The self-test items that don't need a load run at every boot; a new
  firmware image must pass them before it marks itself good.
- Test signals stay quiet by default (a board may sit at home with its
  speakers or motors connected); loud runs are asked for explicitly.

## The report

`scripts/check_observability.py --workspace <ws>` reads the exported
netlist and lists each rail as `self` (an MCU, monitor or ADC pin reads
it, directly or through a divider), `test_point` or `blind`; each status
output as `watched`, `test_point` or `unwatched`; and whether the board has
a console. It is heuristic (pin names; every path's pin still needs the
datasheet's word that it has an ADC behind it) and a report, not a gate:
run it at P4 when the schematic exports and again at P8, and weigh each
`rail_blind` and `status_unwatched` against the failure it would hide.
