# Checklist: mcu (any microcontroller)

- BOOT/strapping pins: every boot-select pin strapped per intended boot
  source (never floating), value per datasheet; check against reset-default
  pin functions.
- NRST/reset: cap per datasheet; internal pull-up presence checked, external
  pull only when required; no accidental permanent reset.
- Crystal: load caps = 2*(CL - Cstray) with Cstray ~3-10pF stated; gain
  margin per AN (drive level, ESR max); crystal grounded per land pattern;
  no digital toggling under the crystal region.
- EVERY power pin decoupled per DS (list them); VDDA/VREF pairs exact; VBAT
  strapped when unused (to VDD unless DS says otherwise).
- Debug port pins un-conflicted (SWD/JTAG default pins not repurposed
  without a stated waiver); connector pinout matches the debugger family.
  STM32 debug connector = STDC14 (2x7 1.27 mm) unless the brief opts out
  to TC2030 or a 0.1 in header.
- 5V-tolerance: any pin fed >VDD verified FT in the datasheet pin table.
- Unused inputs: NC-flagged or defined level; no floating inputs.
