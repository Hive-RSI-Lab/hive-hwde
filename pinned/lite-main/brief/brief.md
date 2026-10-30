# lite-main: brief

Task 261028-145601-969b9c9f7c (hifi-receiver-2 issue #4): build the Lite tier board
with hive-hwde, from schematic through routed layout to a JLC fab package. Nothing is
ordered.

The product is defined in `docs/product.md` (Lite column) and `docs/bom-estimate.csv`:

- USB-C PD sink at 20 V (CH224 family); a 65-100 W laptop charger powers it.
  On a 5 V-only port (a laptop) it boots with the amplifier unpowered, for flashing.
- TPA3118D2 stereo class-D amp, BTL, 4 ohm speakers (Vienna Acoustics), 43 W/ch peak at 20 V.
- ESP32-WROVER-E-N8R8 + ES8388 codec, wired as the ESP32-A1S audio kit so
  squeezelite-esp32 supports it unchanged.
- RCA line in (Pioneer FLX4) through the ES8388's analog bypass; RCA sub outs (2 subs,
  full range, the ES8388's second output pair).
- 4 mm banana sockets for the two speakers.
- CH340C on the USB-C data pins for console and flashing (auto-program).
- 3 buttons (vol-, vol+, play/source), IR receiver, a status LED.
- One 2-layer board, about 100 x 80 mm, connectors on the rear edge, buttons/IR/LED at
  the front, antenna at a board edge. JLC assembly, LCSC parts only.

The person's standing instruction: take care of everything yourself; where hwde asks the
person to sign off (H1, H2, H4), go on with best judgement and record the choice.
Ordering or paying stays with the person.
