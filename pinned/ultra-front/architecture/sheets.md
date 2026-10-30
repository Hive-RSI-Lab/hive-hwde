# Sheet plan

Single sheet: `ultra-front` (root, no hierarchy), generator `kicad/gen/root.py`.

| Blocks | Refdes |
|---|---|
| B1 core link | J1, C2, C3 |
| B2 LCD | J2, C4, R8 |
| B3 backlight | Q1, R11, R12, R13, R17, R18 |
| B4 expander | U1, C1, R7, R19 |
| B5 encoder | SW4, R1, R2, R3, C6, C7 |
| B6 buttons | SW1, SW2, SW3, R4, R5, R6 |
| B7 IR | U2, R9, R10, C5 |
| B8 status LED | D1, R14, R15, R16 |
| mounting | H1-H4 (M2.5), board-only, added by board_init |

Net names (root-local labels export as /NAME): /LCD_SCK, /LCD_MOSI, /LCD_CS,
/LCD_DC, /LCD_BL_PWM, /LCD_RST, /BL_GATE, /BL_A, /BL_K, /EXP_UNUSED, /I2C_SCL,
/I2C_SDA, /FRONT_INT_N, /IR_OUT, /IR_VCC, /ENC_A, /ENC_B, /ENC_SW, /BTN1-3,
/LED_R, /LED_G, /LED_B, /LEDR_K, /LEDG_K, /LEDB_K; rails +3V3, +5V, GND.
