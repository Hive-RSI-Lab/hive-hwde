# PCB-0023-A gan-rf-inverter — brief

The brief hwde was given for PCB-0023-A (boards brief/task.md), with only the repo bookkeeping taken out: register lines, sibling boards' rows, PR and library filing.

PWM LINK (shared by all three; don't change it without saying so first in the PR): 50-ohm coax on SMA. The source is back-terminated in 50 ohm and delivers >=2.5 V high into a 50-ohm load, edges <=1.5 ns. The receiver terminates in 50 ohm (0-ohm/DNP option to go high-Z), reads high at >=2.0 V, survives 0-6 V at the SMA, and works from a Siglent SDG6032X (the owner's reference source, up to 10 Vpp into 50 ohm) as well as from PCB-0025.

THIS ROW is the inverter, unchanged from the brief below, with two additions: its PWM inputs meet the link above, and ngspice sims of the class-DE stage (ZVS waveforms, loss at 100 and 200 W, the ringing margin behind the GaN voltage choice) go in its docs.

THE OWNER'S BRIEF, verbatim (#ai-ee 1791233794.135289, 2026-10-05):
"RF Inverter: I want you to design an RF Inverter board capable of 100-200W, GaN (EPC 2307 or 2304 or something, with LMG1020 or 1025). Keep it big, easy to cool, 13.56MHz, make the gan stage small and follow routing guidelines closely. It doesnt need much. Just PWM in via SMAs, 5V supply or whatever for low voltage side, VBUS input, and then you can just put in generic large footprints for inductors and caps for the reactive part. This is just meant as a prototype which i can use to play around with. The GaN power stage and gate drive loops etc are the most important part. Just use regular SMA -- do rough power calculations for stuff, it's not too important for everything to be exact. prioritize good layout and low cost. JLC assembly. You can also use very small components for the power stage and we can fab many if they break. Get the HS drive iso right too-- remember it needs to be the same drive for top and bottom so there is no delay. This is for driving ICP plasma."
His answers: (1) VBUS "probably like 80V". (2) "Two SMAs with self-set dead time." (3) "just put in generic footprints for the reactive stage. I want to experiment with ZVS inductors, making it robust as possible/load independent, pushing into 50 OHM and reactive loads, etc, and also in the next stage, experiment with outphasing etc." (4) "Half-bridge class DE actually".

ENGINEERING NOTES (from the ai-ee seat; these are design guidance, not the owner's words):
- Put both PWM channels through the same isolator part so the delays match, with matched trace lengths, and give the isolator high CMTI (above 100 V/ns), because GaN edges run 50-100 V/ns. Supply the high-side 5 V from an isolated DC-DC with low barrier capacitance, not a bootstrap.
- Lay out the power loop to EPC's optimal-layout guidance: decoupling right at the FETs, the return on the layer directly beneath, likely 4 layers. Keep the gate loops minimal and use Kelvin source connections.
- 80 V on a 100 V part is tight even with ZVS. Check the ringing margin, and pick the 200 V part if the 100 V one doesn't hold. Record the choice and why.
- Make the reactive stage configurable: shunt ZVS L to the midpoint, series LC, shunt C and an L-match, selectable with 0-ohm or jumper options. Use large generic L/C footprints. PWM in and RF out stay on SMAs, so that two boards can be combined later for outphasing.
- Plan for heatsinking the GaN: top-side cooling, or room for a clip or a heatsink. Make the board big, keep the power stage small and tight.
- Do rough power calculations (loss, dissipation, currents) and keep them in the design docs.

The silkscreen carries "PCB-0023 REV A". Run from intake to a DRC/ERC-clean fab package with renders. Do NOT order.
