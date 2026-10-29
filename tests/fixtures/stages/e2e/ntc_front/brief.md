# ntc front (e2e bench brief)

A thermistor interface. A 10 k NTC (B = 3950) plugs into a 2-pin JST-PH
connector and is read by a 3.3 V ADC on a host through a 0.1 inch header that
carries 3.3 V, ground and the signal. Bias it so 25 C sits at mid-scale, and
low-pass the signal at 1-5 Hz for a slow ADC.
