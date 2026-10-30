# ultra-front - requirements

## 1. Function
Front-panel user-interface board for the Ultra tier of the hifi-receiver-2
streaming amplifier: carries a 2.0" ST7789 SPI LCD (plugged in by its FPC
tail), an EC11 rotary encoder with push switch, 3 tactile buttons, a 38 kHz
IR receiver and an RGB status LED, and connects to the `core` board over a
20-pin 0.5 mm FFC. No MCU on this board: the core's ESP32 drives the LCD over
SPI and reads buttons/encoder/LED through an I2C GPIO expander on this board.
No declared mode (normal design).

## 2. Interfaces
- J1: 20-pin 0.5 mm FFC connector to the core board. Pinout (pin N on core =
  pin N here): 1 GND, 2 +3V3, 3 +5V, 4 GND, 5 LCD_SCK, 6 GND, 7 LCD_MOSI,
  8 LCD_CS, 9 LCD_DC, 10 LCD_BL_PWM, 11 GND, 12 I2C_SCL, 13 I2C_SDA,
  14 FRONT_INT_N, 15 IR_OUT, 16 ENC_A, 17 ENC_B, 18 +5V, 19 +3V3, 20 GND.
  Rails mirror-symmetric (1/20, 2/19, 3/18) so a reversed cable cannot short
  a rail. Signals 3.3 V CMOS; SPI at up to 15 MHz (the LCD's 66 ns minimum write
  clock period) over < 150 mm of FFC, terminated at the core (source) end.
- J2: 12-pin 0.5 mm FPC connector for the LCD tail (LCD pins 1 GND, 2 CS,
  3 RS/DC, 4 SCL, 5 SDA, 6 RST, 7 NC, 8 IOVCC, 9 VCC, 10 LED-A, 11 LED-K,
  12 GND). Tail contacts face the display side: dual-contact connector.
- I2C: one GPIO expander at 7-bit address 0x27 (clear of CH224Q 0x22/0x23
  and ES9018K2M 0x48/0x49 on the core's bus). I2C pull-ups live on the core.
- User: encoder (A/B/push), 3 buttons, RGB status LED, IR receiver window.

## 3. Power
- +3V3 from core (3.2-3.4 V): expander, LCD logic, IR receiver, pull-ups.
  About 30 mA.
- +5V from core (4.75-5.25 V): LCD backlight (4 white LEDs in parallel,
  Vf 2.8-3.2 V, run at about 60 mA through a series resistor and a low-side
  N-MOSFET, PWM-dimmable from the core; about 50 mA as built) and the status LED anode. About 75 mA.
- No regulators on this board. No battery, no mains, nothing above 5.25 V.

## 4. Environment
Indoor consumer audio product, 0-40 C ambient inside a 3D-printed enclosure.
User-touched front: buttons and knob (metal encoder shaft and frame tied to
GND).

## 5. Size & mounting
- Outline about 100 x 40 mm (product.md); SOFT. 2.0" LCD module is
  51.8 x 36.2 mm and must sit on the top side with its 20.7 mm tail reaching
  J2; the knob, buttons, IR and LED sit beside it.
- Mounting: 4 x M2.5 holes for screwing to the front panel.
- Height: the LCD and switches face the user (top side); keep the LCD area
  free of parts.
- Stackup: 2 layers, 1.6 mm FR-4, HASL, 1 oz.

## 6. Quantity & budget
10 to 100 units. Cheapest BOM consistent with the function; product.md
budgets about $9 per unit for the front board's parts at 100 (LCD $2.43 of
it, bought separately and plugged in at final assembly).

## 7. Assembly
JLCPCB PCBA, top side only. SMD everywhere except the EC11 encoder (THT,
JLC hand/wave soldered). The LCD module is not JLC-placed: it is plugged into
J2 and taped down at final assembly. The FFC cable to core is an off-board
item.

## 8. Compliance/safety flags
None apply: no mains, no battery, no voltage above 30 V, no current above 3 A,
no RF transmit (IR receive only).

## 9. Open questions
none (answered from product.md; pinout proposed to the core board's run)
