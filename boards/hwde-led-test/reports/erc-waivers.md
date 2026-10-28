# Schematic review warnings carried, not fixed (P4)

- led-current-margin (R1, D1): accepted. The brief asks for "about 3-5 mA";
  at the listed Vf 3.1 V the current is 3.5-4.6 mA. Only an unconfirmed worst-case
  Vf of 3.4 V at 4.75 V input lands at 2.8 mA, which still lights the LED.
- bom-fields-missing (J1, C1, R1, D1): checked again at P9, where bom_cpl
  writes the JLC BOM; if the LCSC column is empty there, it is fixed then.
