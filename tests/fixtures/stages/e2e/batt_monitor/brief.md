# batt monitor (e2e bench brief)

A 4S lithium-ion pack monitor front end. The pack (12.0 to 16.8 V) connects
on a screw terminal. The board scales it for a 3.3 V ADC on a host MCU and
hands the scaled voltage over a 0.1 inch header, which also carries ground.
Put an anti-alias low-pass in front of the ADC with its corner near 10 Hz.
Across component tolerances the scaled signal must not exceed 3.3 V at a full
pack and should use at least 90% of the ADC range. The pack must not drain
more than 100 uA through the board. It goes on every pack, so keep it cheap.
