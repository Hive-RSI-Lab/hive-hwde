# Where each pinout and value came from

Fetched 2026-10-28. LCSC's own datasheet links (wmsc.lcsc.com) returned a
141-byte stub through the proxy for every part tried, so the sources below
are the vendors' or mirrors.

| Part | Source | Used for |
|---|---|---|
| TPA3118D2 | TI SLOS708G, https://www.ti.com/lit/ds/symlink/tpa3118d2.pdf | DAP pin table (sec. 5), gain table 1 (26 dB master = 20k to GND, 100k to GVDD), stereo BTL typical application: 220 nF bootstraps, 10 uH + 680 nF..1 uF output filter, 1 nF/100 nF/220 uF PVCC per side, 1 uF GVDD, PLIMIT to GVDD = no limit |
| CH224Q | WCH CH224 datasheet V2.0 (English), https://done.land/assets/files/ch224aq_datasheet.pdf, and the LCSC Chinese copy | table 4-1 pins (VHV 1, CFG2/SCL 2, CFG3/SDA 3, DP 4, DM 5, CC2 6, CC1 7, VBUS 8, CFG1 9, PG 10, EP GND), table 5-1 Rset (120k = 20 V), I2C address 0x22, VBUS shorted to VHV with 1 uF |
| ES8388 | Everest ES8388 rev 5.0, https://www.armdesigner.com/download/ES8388_datasheet.pdf | 28-pin QFN pin table; decoupling values from Espressif's ESP32-LyraT v4 schematic, https://dl.espressif.com/dl/schematics/esp32-lyrat-v4-schematic.pdf |
| TPS54302 | TI SLVSDG6C, https://www.ti.com/lit/ds/symlink/tps54302.pdf | pin table (GND 1, SW 2, VIN 3, FB 4, EN 5 float = on, BOOT 6), table 7-2 (5 V: 10 uH, 44 uF, 100k/13.3k, 75 pF) |
| XDJK-0271-1650 | footprint geometry cross-checked against the open M2-IO-Emulator-Board footprint notes (Lian Xin drawing A-AV2-7A1) | stacked dual RCA: pad 2 = sleeve, pad 1 = top jack (white, L), pad 3 = bottom jack (red, R); 2 locating pegs the EasyEDA footprint omits |
| CH340C, SY8089A1AAC, LP5907, AO4485, MMBT3904, IRM-H638T, TS-1187A, SMBJ24A, LED, RVT 220 uF | the EasyEDA symbol/footprint pin names (lib/aiee.kicad_sym), checked against the well-known pinouts | |
| ESP32-WROVER-E | EasyEDA symbol pin names (IO numbers); strapping and PSRAM pins per Espressif's WROVER-E datasheet | |
