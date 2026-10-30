# ultra-front - brief

From hifi-receiver-2's `docs/product.md` (Board split, Ultra) and `docs/plan.md`
item 21, task 261028-145644 (issue #8). The person: "Done = fab files complete".
Nothing is ordered; ordering stays the person's.

`ultra-front`: Ultra tier only. A 2-layer front-panel board of about
100 x 40 mm carrying the user interface of the Ultra receiver:

- the 2.0" 320x240 ST7789 IPS LCD (HS20HS072RX, LCSC C5329582), which plugs
  into a connector on this board by its 12-pin 0.5 mm FPC tail
- the rotary knob (EC11 encoder with push switch)
- 3 push buttons
- an IR remote receiver (IRM-H638T, 38 kHz)
- a status LED
- a 20-pin 0.5 mm FFC connector to the `core` board, which supplies +3V3 and
  +5V and drives everything (ESP32: SPI to the LCD, I2C, IR input)

The core board's ESP32 is short of pins (Ethernet RMII, two I2S, I2C), so
the front board should cost the core as few GPIOs as possible.
Fully JLC-assembled where possible; cheap BOM. Take the connector to core
from product.md and match the core board's pin map if it lands first.
