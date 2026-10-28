# Topology reference: buck (GENERATED VIEW - do not hand-edit)

Source of truth: reference/knowledge/records/ (U4). Regenerate with
`scripts/knowledge.py --render-topology buck --out reference/topologies/buck.md`; a test pins this file to the
records, so hand-edits fail the suite - edit the record, re-render.

HOW TO USE (research-reference-design agents): read this FIRST, then
research only the part-specific delta (exact external-component
table, errata, the family's FB flavor). Cite deltas against the
record ids. Retrieval into P6/P7 spawn prompts is automatic
(knowledge.py --select) once constraints.json declares the block.
Each heading carries the record's level/maturity tag (U13 coverage
contract): draft = unreviewed; only approved/proven satisfy coverage.

## buck-bst-fb-output-caps [feedback, decoupling] (family/approved)

BST: integrated-FET parts need the 100 nF BST-SW cap - it is required, not optional (DS41326 s13); confirm value/rating in the family datasheet. FB TRAP (cost a near-miss once): FIXED-output family members tie FB straight to the output sense point - do NOT copy the ADJUSTABLE variant's divider from the shared datasheet figure (AP63203 vs AP63200/1, DS41326 fig 20/21). Adjustable parts: divider AT the FB pin, short FB trace routed away from SW and L, sense point AFTER the output caps. OUTPUT CAPS: ceramic, inside the datasheet's stated C/ESR window - internal compensation assumes it (DS41326 s12); count DC-bias derating (a "22 uF" X5R at bias is ~12-15 uF). When the part has EXTERNAL compensation, the vendor's table values are quoted for a specific C_OUT - a different bank means re-deriving the network, not copying the row (sbuck D-item: 5x 22 uF vs the table's 2x 22 uF moved the crossover by 2.5x).

Rule: bst_cap_nf=100 fb_divider_at=FB pin, sense point AFTER the output caps

Envelope: integration_kind={"in": ["integrated-fet"]}

Sources: boards/usb-buck/parts/C5248536.pdf DS41326 s12-13, fig 20/21; boards/sbuck-5v3a/architecture/blocks.md s3 (compensation re-derive)

## buck-cin-co-ground-separation [power-loop, emi] (topology/approved)

Two ROHM measurements the generic hot-loop rule does not carry: (1) shortening the CBYPASS wiring EVEN BY 1 mm is worth it - the HF bypass supplies the steepest edge of the switch current, and its loop inductance sets the VIN-pin voltage noise directly; (2) even with CBYPASS tight to the IC, several hundred MHz rides on the INPUT capacitor's ground, so the grounds of C_IN and C_OUT must sit 1-2 cm APART - placed close together the input HF noise couples straight into the output through C_OUT. C_IN placed on the bottom layer through vias is explicitly unsuitable (via inductance); input cap and (for async) the free-wheel diode go on the SAME surface layer as the IC terminals. This refines, not replaces, the shortest-loop rule in buck-input-hot-loop.

Rule: bypass_same_surface_as_ic=True cin_co_gnd_separation_cm=1 to 2

Envelope: switching_kind={"in": ["hard"]}

Sources: reference/knowledge/sources/rohm-buck-pcb-layout-an.pdf p.3 s3, fig 3-a..3-d; reference/knowledge/sources/rohm-buck-pcb-layout-an.pdf p.7 s6 (output cap)

## buck-cmode-inductor-window [selection] (family/approved)

"Take the next standard value up" is only half true on a current-mode part: the inductor is bounded on BOTH sides by the modulator. Floor: the datasheet prints L >= 0.28 * Vout / fsw - 3.5 uH at 5 V and 400 kHz - and below it the loop is not stable. Ceiling: the current-mode sensor needs ripple to work on, so the minimum inductor ripple must stay above about 10% of the DEVICE's maximum rated current under nominal conditions; too much inductance starves the sensed ramp. Between them the target is 20-40% ripple, best 30%, and the reference current is the DEVICE maximum rather than the load - explicitly so when the load is much smaller than what the part can deliver. On a 3 A part running a 2 A rail that means sizing ripple against 3 A, which is why the vendor's 400 kHz / 5 V table row is 8 uH rather than the larger value a 2 A-referenced calculation gives. Saturation: Isat must beat the HIGH-SIDE current limit (3.85 A minimum, 5.05 A maximum here), not the load and not the low-side limit alone, so a 2 A rail still needs an inductor that survives about 5 A; ferrite saturates hard while powdered iron saturates softly and allows some relaxation at the cost of core loss above about 1 MHz. The same table row is the fastest sanity check on the rest of the bank.

Rule: isat=must exceed the HIGH-SIDE current limit, not the load (3.85 A min / 5.05 A max on this part) l_ceiling=ripple must stay above about 10% of the device maximum rated current l_floor=L >= 0.28 * Vout / fsw (3.5 uH at 5 V and 400 kHz) ripple_band_pct=20 to 40 ripple_reference=the DEVICE maximum rated current, not the load current ripple_target_pct=30 table_row_400k_5v=L 8 uH, C_OUT 4 x 22 uF, R_top 100 k, R_bot 24.9 k, C_IN 10 uF + 220 nF, C_BOOT 100 nF, C_VCC 1 uF, C_FF open

Envelope: control_kind={"in": ["cmode"]}

Sources: reference/knowledge/sources/lmr33630.pdf p.21 9.2.2.4 Inductor Selection, Equation 4; reference/knowledge/sources/lmr33630.pdf p.22 9.2.2.4 (continued), Equation 5; reference/knowledge/sources/lmr33630.pdf p.7 7.5 Electrical Characteristics, CURRENT LIMITS; reference/knowledge/sources/lmr33630.pdf p.20 Table 9-2 Typical External Component Values

## buck-cmode-internal-comp-cout-window [feedback] (family/approved)

A peak-current-mode part with FIXED internal compensation does not just have a minimum output capacitance - it has a WINDOW, and the ceiling is the half designers forget. Floor: the load-transient equation yields both a minimum C_OUT and a maximum ESR for a stated dVout on a stated dIout; the vendor's own 5 V / 400 kHz example needs 52 uF and ESR below 0.11 ohm to hold 250 mV on a 2 A step. Then derate before comparing - 20% tolerance plus 10% DC-bias derating turns that 52 uF into 72 uF of nameplate, met with 4 x 22 uF 16 V 1210 parts, and a 5 V output wants 16 V-class ceramics (10 V class is for 3.3 V and below). Ceiling: total output capacitance must stay under about 10x the design value or 1000 uF, whichever is smaller, because the internal compensation and the fixed soft start were designed against a bank of the intended size; a much larger bank degrades start-up behaviour as well as loop stability, and going past it buys a full-load start-up study and a Bode plot. Two adjacent levers, not substitutes for bulk: a 1-100 nF small-case ceramic on the output kills the HF spikes that inductor and board parasitics leave on the rail, and a feedforward capacitor across the top divider resistor is the phase-margin lever when that resistor is large (recommended 100 kohm, 1 Mohm maximum and then C_FF is mandatory).

Rule: above_ceiling=owe a full-load start-up study and a Bode plot, not a guess ceiling=total C_OUT under about 10x the design value, or 1000 uF, whichever is smaller derating=add tolerance and DC bias before comparing - 52 uF becomes 72 uF of nameplate, met by 4 x 22 uF floor=load-step equation gives a minimum C_OUT and a maximum ESR for a stated dVout/dIout hf_adder_nf=1 to 100 voltage_class=16 V or more for a 5 V output; 10 V class only at 3.3 V and below worked_floor=5 V / 400 kHz, 250 mV on a 2 A step -> 52 uF and ESR under 0.11 ohm

Envelope: control_kind={"in": ["cmode"]}

Sources: reference/knowledge/sources/lmr33630.pdf p.22 9.2.2.5 Output Capacitor Selection, Equations 6-7; reference/knowledge/sources/lmr33630.pdf p.23 9.2.2.5 (continued); reference/knowledge/sources/lmr33630.pdf p.21 9.2.2.3 Setting the Output Voltage; reference/knowledge/sources/lmr33630.pdf p.24 9.2.2.9 C_FF Selection, Equation 9

## buck-constraints-emission [constraints-emission] (principle/approved)

What a buck block must emit into constraints.json for the pipeline: (1) power entries with current_a from the RAIL BUDGET (consumer sum + ~30% headroom, rounded to a design ceiling) and dt_c from ambient; (2) a thermal entry when regulator dissipation > ~0.5 W - for async parts add the Schottky's Vf * I * (1-D), the diode often out-heats the IC; (3) layout_notes for P6/P7: hot-loop grouping, SW containment/separation, FB routing - these become placement groups and route_critical facts; (4) a blocks entry ({topology: buck}) so knowledge retrieval keys on it.

Rule: thermal_entry_above_w=0.5

Sources: boards/usb-buck/research/power.md s7-9 (emitted constraints); .claude/skills/hwde/reference/constraints_schema.md power / thermal / blocks

## buck-constraints-emission-layout-groups [constraints-emission] (principle/approved)

What a buck block must hand downstream beyond the generic power, thermal and blocks entries, because nothing later can infer it. (1) The thermal entry needs a COPPER-AREA target next to the watts: effective RthetaJA is set by copper area, layer count and the number of vias under the pad, the datasheet's tabulated figure is explicitly not a design number, and the vendor's area curve is drawn for a four-layer 2/1/1/2 oz stack - so a board with a different stack must state its own assumption or the temperature rise is fiction. (2) Layout notes must produce THREE placement groups, not one: the hot loop (IC plus C_IN plus the HF bypass hard against the VIN and PGND pins), the gate-drive capacitors (C_BOOT across BOOT-SW, C_VCC across VCC-GND, each at its own pins), and the FB divider at the FB pin with its sense connection taken at the output bank. A placer told only about the hot loop will happily scatter the other two. (3) Emit copper CONTINUITY as a route fact rather than a hope: no route may cross the ground pour under the hot loop, the SW node or the inductor, because a cut there was measured to cost the entire benefit of the plane. (4) The exposed-pad via array is a land-pattern fact that must travel with the footprint, since it is simultaneously the thermal path and the ground reference.

Rule: footprint_fact=the exposed-pad via array is part of the land pattern, not a DFM afterthought placement_groups=three, not one - hot loop (IC + C_IN + HF bypass at VIN/PGND), gate-drive caps (C_BOOT at BOOT-SW, C_VCC at VCC-GND), FB divider at the FB pin route_facts=plane continuity under the hot loop, the SW node and the inductor; FB sense taken at the output bank and kept off the SW corridor thermal_entry=emit a copper-area target with the watts - RthetaJA is set by area, layer count and via count, and the tabulated value is not a design number thermal_entry_states_stack=say which layer count and copper weight the assumed RthetaJA came from

Sources: reference/knowledge/sources/lmr33630.pdf p.7 7.4 Thermal Information; reference/knowledge/sources/lmr33630.pdf p.25 9.2.2.11 Maximum Ambient Temperature, Figure 9-3; reference/knowledge/sources/lmr33630.pdf p.33 10.1 Layout Guidelines, rules 1-4, 6, 9; reference/knowledge/sources/snva721a.pdf p.6 2.4 Ground Shielding, Figures 11 and 14; reference/knowledge/sources/snva721a.pdf p.5 2.3 Minimize Area of Gate Driver Loops, Figure 8

## buck-dc-input-hot-plug-overshoot [inrush] (topology/approved)

A bench supply or DC adapter imposes no bulk-capacitance budget the way USB, PD or PoE do - that is buck-upstream-inrush-limit's case. What bounds a dc-input board is the hot-plug transient: lead resistance and inductance in series with a low-ESR ceramic C_IN form an under-damped series LC that a live plug-in steps. With no series resistance the printed model reduces to Vi*(1-cos wt), a 2x peak; TI's plots at Ri = 0.21 ohm, Li = 9.3 uH peak at ~30 V (C_IN 20 uF) and ~27 V (40 uF) on an ~18.3 V source - 1.5-1.6x Vi. More capacitance damps the peak without removing it, more lead inductance raises it and rings longer, and only series resistance suppresses the ring (raising Ri drops the peak ~36 -> ~30 V; ring duration runs the other way on that plot - see the p3 note). The criterion R_lead + R_cap > 2*sqrt(L_lead/C_in) wants 1.9 ohm at 10 uF C_IN, 0.96 ohm at 40 uF, for that 9.3 uH cable; real cable is 0.15-0.21 ohm and the pin capacitor cannot carry much series R - short by 5-10x, so every plotted trace rings. Scaled onto this board's 30 V input that is 45-49 V at VIN against a 38 V abs max - a destroy-the-part event. Fixes, in the part's own words: shorten the leads and add a 20-100 uF aluminium or tantalum across the ceramics - its ESR damps the resonance and holds VIN up through load steps; an RC snubber sized by the criterion is cheaper. A TVS is allowed, but never a snap-back type: when it fires it collapses below Vout and the output caps discharge back through it.

Rule: binding_limit=hot-plug LC overshoot at the VIN pin, NOT an upstream bulk-capacitance budget damping_criterion=R_lead + R_cap > 2*sqrt(L_lead/C_in) - for TI's 9.3 uH cable that is 1.9 ohm at 10 uF C_IN, 1.4 ohm at 20 uF, 0.96 ohm at 40 uF fixes=shorten the leads; 20-100 uF aluminium or tantalum across the ceramics; or an RC snubber (2 ohm 0.5 W + 2.2 uF class) headroom_at_this_op=30 V nominal against a 38 V abs-max VIN pin = 1.27x; scaling TI's 1.5-1.6x ring onto 30 V gives 45-49 V, over the abs max plotted_peaks=about 30 V (C_IN 20 uF) and 27 V (40 uF) from an ~18.3 V source at Ri = 0.21 ohm, Li = 9.3 uH - 1.5-1.6x Vi tvs=allowed, but never a snap-back (thyristor) type on a regulator input undamped_peak=the printed model with zero series R reduces to Vi*(1-cos wt) - a 2x peak

Envelope: source_kind={"in": ["dc-input"]}

Sources: reference/knowledge/sources/lmr33630.pdf p.32 Power Supply Recommendations; reference/knowledge/sources/lmr33630.pdf p.6 7.1 Absolute Maximum Ratings / 7.3 Recommended Operating Conditions; reference/knowledge/sources/sluaal8.pdf p.2 2 Root Cause, Figure 2-1, Equations 1-3; reference/knowledge/sources/sluaal8.pdf p.3 Figures 2-2, 2-3, 2-4; reference/knowledge/sources/sluaal8.pdf p.4 3 RC Snubber, Equation 4, Figure 3-1

## buck-en-softstart-sequencing [sequencing] (principle/approved)

Check the EN pin's OWN behavior before adding parts: many parts auto-start (AP63203: internal 1.5 uA pull-up - tie to VIN or float; adding a divider is wasted parts). When EN is used as a UVLO divider, mind the two traps the sbuck run hit: a narrow hysteresis gap can force a divider into the mA-burn regime via the part's own equations, and hysteresis smaller than the input CABLE DROP at full load motorboats (start -> sag -> stop -> recover). Soft-start time sets the output-cap inrush as seen by the source. Single-rail boards rarely need sequencing; multi-rail: state the order requirement or "none" explicitly in power.md. Three facts from a precision-EN part (LMR33630) invert the auto-start case: EN may be "Do not float" with no internal pull-up (0.2 nA leakage), so tie it to VIN or divide it; a fixed internal soft-start (2.9-6 ms, no SS pin) makes output inrush a derived number, about C_OUT * Vout / t_SS on top of the load; and a UVLO divider's hysteresis is a fixed FRACTION of turn-on, Voff = Von * (1 - Ven_hys/Ven_h), so the window shrinks with Vin.

Sources: boards/usb-buck/research/power.md s6 (EN/soft-start); boards/sbuck-5v3a/architecture/blocks.md s4 (UVLO divider pathology); reference/knowledge/sources/lmr33630.pdf p.5 Table 6-1 Pin Functions, EN row: Do not float; reference/knowledge/sources/lmr33630.pdf p.7 7.5 ENABLE block: precision thresholds, 0.2 nA leakage; reference/knowledge/sources/lmr33630.pdf p.8 7.5 SOFT START: t_SS 2.9/4/6 ms, no SS pin; reference/knowledge/sources/lmr33630.pdf p.13 8.3.2 Enable and Start-up; reference/knowledge/sources/lmr33630.pdf p.14 Figures 8-3 and 8-4: precision enable, start-up capture; reference/knowledge/sources/lmr33630.pdf p.24 9.2.2.10 External UVLO, Equation 10

## buck-ep-agnd-thermal-via-array [thermal-via, return-path] (topology/approved)

On the 8-pin HSOIC the exposed pad is not just a heatsink. The pin table names it AGND - the ground reference for the internal references and logic - and states that ALL electrical parameters are measured with respect to it, so the via array under the pad is simultaneously the thermal path and the analog ground connection. A partly wetted pad therefore degrades both the effective thermal resistance and the reference the feedback comparator works against, which is why the datasheet says the integrity of that solder connection has a direct bearing on effective RthetaJA. The vendor number is a minimum 4 x 3 array of 10-mil (0.25 mm) vias evenly distributed under the pad; the library's buck-thermal-via-and-via-current holds ROHM's 0.3 mm at about 1.2 mm pitch, and both agree on the reason - small enough to fill with solder instead of wicking it out of the joint. Where the array LANDS is the two-layer catch: with no inner plane it has to reach the bottom-layer ground pour, so that pour must be a real heatsink area (2 oz preferred, 1 oz floor) and must not be cut up by routing. Do not size anything from the 42.9 C/W in the thermal table - the datasheet says it is not valid for design - and do not read the copper-area curve literally either: it is drawn for a four-layer board with 2 oz outers and 1 oz inners, falling from about 43 C/W at small area to about 28 C/W near 20 cm2 per layer.

Rule: area_curve_caveat=the RthetaJA versus copper-area curve is drawn for a FOUR-layer board with 2 oz outers destination_on_2_layers=the bottom-layer ground pour, 2 oz preferred and 1 oz the floor pad_role=the exposed pad is BOTH the heat path and AGND - all electrical parameters are measured with respect to it solder_integrity=a partly wetted pad degrades effective RthetaJA AND the reference the loop measures against table_rthetaja=42.9 C/W is for package comparison only - explicitly not valid for design via_array=minimum 4 x 3 array of 10-mil (0.25 mm) vias, evenly distributed under the pad

Envelope: integration_kind={"in": ["integrated-fet"]}

Sources: reference/knowledge/sources/lmr33630.pdf p.5 Table 6-1 Pin Functions, THERMAL PAD row; reference/knowledge/sources/lmr33630.pdf p.34 10.1.1 Ground and Thermal Considerations; reference/knowledge/sources/lmr33630.pdf p.33 10.1 Layout Guidelines, rules 6 and 8; reference/knowledge/sources/lmr33630.pdf p.25 9.2.2.11 Maximum Ambient Temperature, Figure 9-3; reference/knowledge/sources/lmr33630.pdf p.7 7.4 Thermal Information

## buck-fb-route-rules [feedback] (topology/approved)

The feedback route needs the most attention of any signal wire - noise here becomes output-voltage error and instability. ROHM's four rules (fig 7-a): (a) the FB pin is high impedance - connect the divider network with a SHORT wire at the pin; (b) sense AFTER the output capacitor or at its terminals; (c) wire the two divider resistors adjacent and parallel for noise tolerance; (d) route far from the switching node, never directly under the inductor or diode, and never parallel to a power line - on multilayer boards the same rules apply layer-to-layer, and the worked example drops the FB route to the bottom layer through a via to get away from the SW region. A feedback trace laid parallel beside the inductor picks up its magnetic field (fig 7-d). Matches and extends the FB half of buck-bst-fb-output-caps.

Rule: divider_at_fb_pin=short wire, resistors adjacent and parallel keep_away_from=SW node, inductor, diode; never under L or D; not parallel to power lines sense_point=after or at both ends of the output capacitor

Envelope: switching_kind={"in": ["hard"]}

Sources: reference/knowledge/sources/rohm-buck-pcb-layout-an.pdf p.7 s7, fig 7-a/7-b; reference/knowledge/sources/rohm-buck-pcb-layout-an.pdf p.8 fig 7-c/7-d

## buck-freewheel-diode-snubber-placement [power-loop, emi] (topology/approved)

Async buck (free-wheel diode) placement rules: the diode must sit CLOSE and on the SAME surface as the IC terminals, wired short and wide, connected directly to the IC's GND and SW terminals - distance adds wiring inductance whose spike noise piles onto the output, and dropping the diode to the bottom layer through vias makes it worse (via inductance). If spike noise still needs an RC snubber, place it CLOSE TO THE IC's SW and GND terminals; a snubber across the diode's own ends does NOT absorb the spike generated by the wiring inductance (ROHM fig 3-g vs 3-h). Sync bucks lose the diode but keep the rule's core: the LS-FET return corner of the hot loop stays tight to the IC.

Rule: diode_surface=same layer as IC terminals, never via-dropped to bottom snubber_at=IC SW + GND terminals, NOT across the diode ends

Envelope: rectifier_kind={"in": ["async"]}

Sources: reference/knowledge/sources/rohm-buck-pcb-layout-an.pdf p.3 s3, fig 3-e..3-h; reference/knowledge/sources/rohm-buck-pcb-layout-an.pdf p.5 fig 3-g/3-h placement

## buck-inductor-copper-and-gnd-void [emi] (topology/approved)

Inductor region rules (ROHM s5): place L close to the IC but NOT as close as the input cap; do NOT expand the SW/inductor copper beyond what current needs - enlarged copper works as an antenna (EMI), even though instinct says more copper = cooler. Practical width floors with margin: 1 mm per A at 1 oz, 0.7 mm per A at 2 oz. Do not put a ground plane DIRECTLY UNDER the inductor: eddy currents derate the inductance and Q, and any signal line under it picks up switching noise - keep wiring out from under L, or use a closed-magnetic-circuit (shielded) inductor when routing there is unavoidable. Keep the two inductor terminals' wiring apart: close spacing couples the SW edge to the output through stray capacitance (fig 6-d).

Rule: gnd_plane_under_inductor=avoid (eddy current derates L, couples noise) width_per_amp_1oz_mm=1.0 width_per_amp_2oz_mm=0.7

Envelope: switching_kind={"in": ["hard"]}

Sources: reference/knowledge/sources/rohm-buck-pcb-layout-an.pdf p.5 s5; reference/knowledge/sources/rohm-buck-pcb-layout-an.pdf p.6 fig 6-a..6-d

## buck-inductor-selection [selection] (topology/approved)

The three inductor rules every run re-derived from scratch. L VALUE: start from the vendor's recommended-components table row for your Vout; the next STANDARD value up is usually right - light-load efficiency improves with larger L (DS41326 s10 says so explicitly; usb-buck: table said 3.9 uH, chosen 4.7 uH). ISAT: must beat the part's PEAK CURRENT LIMIT / PFM clamp, not the load current (usb-buck: 450 mA clamp meant a 1 A-rated part was ample at a 57 mA load). DCR: budget it as real dissipation - target < 30 mohm for A-class rails (usb-buck P3 rule, waived at 60 mohm with math); ~0.1 ohm cost 0.29 W at 2 A on the carrier. DCR losses scale I^2 - cheap inductors tax high-current rails hard. Derate Isat/Irms/DCR to the real ambient before comparing (sbuck: 18.5 mohm at 20 C -> 24 mohm hot).

Rule: dcr_mohm_max_a_class_rail=30 isat_must_beat=peak current limit / PFM clamp, not load current

Envelope: iout_a={"max": 5, "min": 0.01}

Sources: boards/usb-buck/parts/C5248536.pdf DS41326 s10 (inductor selection); boards/usb-buck/research/power.md s5 (L/Isat/DCR derivation); boards/lumina-carrier/research/poe-power.md DCR dissipation at 2 A

## buck-input-hot-loop [power-loop, emi] (principle/approved)

Layout rule #1 for every buck: the input cap carries the DISCONTINUOUS switch current. C_IN plus a 100 nF HF bypass AT the VIN pin, on the SAME layer as the IC; the C_IN -> VIN -> GND -> C_IN loop must be the shortest loop on the board; SW-node copper minimal. At ~1 MHz switching this matters more than copper weight (usb-buck power.md s7-9; every vendor layout section says the same). Solid GND pour + vias under the IC. At P6 this is a placement GROUP (constraints placement.groups) anchored on the IC with the input ceramics + HF bypass as members, placed and locked BEFORE the annealer runs - the cost function does not know the loop exists.

Rule: hf_bypass_at=VIN pin, same layer as the IC hf_bypass_nf=100 loop=C_IN -> VIN -> GND -> C_IN shortest on the board

Sources: boards/usb-buck/research/power.md s7-9 (hot loop); boards/usb-buck/parts/C5248536.pdf DS41326 layout section

## buck-integrated-fet-bypass-trio [decoupling] (topology/approved)

An integrated-FET regulator hides the power switches but exposes THREE high-di/dt bypass loops, and all three are short-loop items at their own pins. (1) Input: a minimum of 10 uF of ceramic, rated for at least the maximum input voltage and preferably twice it - at a 30 V input that argues for 63 V-class parts, and it is the DC-bias derating at working voltage that decides whether "10 uF" is really 10 uF. Beside it a small-case 220 nF - not the generic 100 nF - as close to the pins as possible: the datasheet calls it the high-frequency bypass for the control circuits INSIDE the device and specifies 50 V X7R. Input RMS current is about Iout/2, so 1 A at this operating point; check it against the ceramic's rating. (2) Bootstrap: 100 nF, 10 V or better, across BOOT and SW on short wide traces - this is the high-side gate driver's own loop, high di/dt at low energy. (3) Internal bias: 1 uF 16 V from VCC to GND, with nothing else hung on VCC except optionally the power-good pull-up. The vendor's sync-buck figure draws C_BOOT and C_VCC hard against their pins for exactly this reason. On the output side the bulk bank gets a 1-100 nF small-case partner for the spikes that inductor and board parasitics leave on the rail.

Rule: cboot_nf=100 chf_dielectric=50 V X7R, small case, as close to the pins as possible chf_nf=220 cin_irms=about Iout/2 cin_min_uf=10 cin_voltage_rating=at least the maximum input voltage, preferably twice it cout_hf_nf=1 to 100 cvcc=1 uF 16 V ceramic; do not load VCC externally beyond a power-good pull-up loops=three, not one: C_IN/C_HF across VIN-PGND, C_BOOT across BOOT-SW, C_VCC across VCC-GND

Envelope: integration_kind={"in": ["integrated-fet"]}

Sources: reference/knowledge/sources/lmr33630.pdf p.23 9.2.2.6 Input Capacitor Selection, 9.2.2.7 C_BOOT, 9.2.2.8 VCC; reference/knowledge/sources/lmr33630.pdf p.5 Table 6-1 Pin Functions; reference/knowledge/sources/lmr33630.pdf p.33 10.1 Layout Guidelines, rules 2 and 3; reference/knowledge/sources/snva721a.pdf p.5 2.3 Minimize Area of Gate Driver Loops, Figure 8

## buck-power-ground-isolation [return-path, power-loop] (topology/approved)

Ground strategy (ROHM s8): analog small-signal ground and power ground must be ISOLATED, and power ground laid on the TOP layer without splitting is the ideal - dropping an isolated power ground to the bottom layer through vias adds via R and L and worsens noise. Inner/bottom ground planes are SUPPLEMENTARY (DC loss, shielding, heat), not the return path design. Multilayer recipe (fig 9): power-ground plane on L2 stitched to top with MANY vias; common ground L3, signal ground L4; connect the ground families together ONLY at the output capacitor's power ground (the low-HF-noise point) - NEVER at the free-wheel diode or input-capacitor ground, which carry the highest switching noise. This is the class-level reason the pipeline's In1-GND-under-the-IC default works, and where to join AGND islands when a board has them.

Rule: never_join_at=free-wheel diode / input capacitor ground (highest HF noise) pgnd_agnd=isolate; join only at the LOW-noise point (output cap ground)

Envelope: board_layers={"max": 4, "min": 2}

Sources: reference/knowledge/sources/rohm-buck-pcb-layout-an.pdf p.8 s8, fig 8/9

## buck-selection-ladder [selection] (topology/approved)

Regulator-type ladder, one line of tradeoff each; if the brief names a part the named part stands (record the alternative as an override option). (1) Integrated synchronous buck is the DEFAULT at <= ~60 V input and <= ~3 A: no catch diode, best efficiency/heat (usb-buck AP63203). (2) Asynchronous + Schottky when the V/I corner has no stocked sync part (lumina-carrier: the only 100 V-rated 2 A part was async COT); the Schottky must be rated >= Vin (SS510 class). (3) Controller + external FETs is the efficiency/current escape hatch (~2-3 pts over async, any current) at the cost of 2 FETs + gate/sense network + layout area (carrier LM5146 fallback). (4) An LDO is the honest option at light load - 3 parts vs 6 and no switch node near analog/RF, at the cost of (Vin-Vout)*I heat; do the comparison table before assuming the buck.

Envelope: iout_a={"max": 10} vin_v={"max": 100}

Sources: boards/usb-buck/research/power.md s4 (LDO-vs-buck table); boards/lumina-carrier/research/poe-power.md s4 (async choice, LM5146 fallback)

## buck-switch-node-containment [emi] (topology/approved)

Keep SW copper as small as electrically possible and treat SW + L as an AGGRESSOR: lumina-par's 2.4 GHz antenna 11 mm from a switch node drove a 4-layer stackup + containment plan; usb-buck keeps the USB pair away from SW/L per ST AN4879 3.3. Hand P6 a separation/keepout entry (constraints placement.separation / placement.keepouts) whenever any antenna, high-Z analog node, or diff pair shares the board; the switch node is also why high_speed references demand an UNBROKEN plane under victims (check_return_path enforces the plane; the separation entry is what keeps the aggressor out of the corridor in the first place). A /SW test-point tap must be a short stub that does not extend the SW pour - it counts against the SW copper budget.

Rule: sw_copper=smallest electrically possible victims=antenna, high-Z analog, diff pairs -> separation/keepout entry

Envelope: switching_kind={"in": ["hard"]}

Sources: boards/usb-buck/research/interface-usb.md AN4879 3.3 (USB vs switcher); boards/sbuck-5v3a/architecture/blocks.md s8 (/SW tap stub rule)

## buck-sync-hot-loop-cin-placement [power-loop, emi] (topology/approved)

In a synchronous buck the commutating loop is C_IN -> high-side FET -> low-side FET -> back to C_IN, and in an integrated-FET part BOTH switches sit inside the package: SNVA721A fig 3 shades exactly that as the "loop area with discontinuous current", and the HS-ON and LS-ON current paths differ only inside the shaded box. So C_IN and its return are the ONLY external elements you can place - the whole hot-loop design freedom is C_IN plus the HF bypass sitting across the VIN and PGND pins on the IC's layer. That is why the async free-wheel-diode corner has no counterpart here: there is no external return element to place or snub. On the LMR33630 pins 1 (PGND) and 2 (VIN) are adjacent, so the loop can be a few mm around; the datasheet's HSOIC layout example puts C_IN and C_HF hard against those two pins with GND pour beneath. Measured payoff on a generic sync buck, same board and conditions: moving C_IN from "same layer but not close" to "as close as possible" cut switch-node overshoot from 18.1 V to 14.5 V on a 12 V input, output noise from 75 mV to 47 mV pk-pk, and the radiated peak from 44 to 41 dBuV/m - CISPR22 class B was missed before and met after. Keep the traces to the bypass caps short and wide on the IC's layer, do not route high-di/dt current through a plane, and parallel vias if one is unavoidable.

Rule: external_elements_in_loop=C_IN only - both switches are inside the package hot_loop_members=C_IN + HF bypass across the VIN and PGND pins, on the IC's own layer measured_gain_from_tightening=SW peak 18.1 V -> 14.5 V at 12 V in; Vout noise 75 mV -> 47 mV pk-pk; radiated peak 44 -> 41 dBuV/m vias_in_hot_loop=avoid; if unavoidable, several in parallel

Envelope: rectifier_kind={"in": ["sync"]}

Sources: reference/knowledge/sources/lmr33630.pdf p.33 10.1 Layout Guidelines; reference/knowledge/sources/lmr33630.pdf p.34 Figure 10-1 + 10.1.1; reference/knowledge/sources/lmr33630.pdf p.35 10.2 Figure 10-2 Example Layout for HSOIC (DDA); reference/knowledge/sources/snva721a.pdf p.3 2 + 2.1 Identify critical paths, Figure 3; reference/knowledge/sources/snva721a.pdf p.4 2.2 Minimize High Power High di/dt Path Loop Area, Figures 5-6; reference/knowledge/sources/snva721a.pdf p.5 Figure 7 Optimized Critical Loop Area and Results

## buck-thermal-via-and-via-current [thermal-via] (topology/approved)

Thermal vias under an exposed-pad regulator (ROHM s4, HTSOP-J8 numbers generalize to EP packages): small drill - 0.3 mm inner diameter - so the via can FILL with solder; larger drills suck solder away from the joint at reflow (solder-wicking). Pitch ~1.2 mm, directly below the reverse- side thermal pad; add a ring of extra vias around the IC when the pad area alone is not enough. Copper area helps but the base material is the real radiator - vias carry the heat to the far layers. For CURRENT via sizing (s10-3): a via's equivalent conductor width is pi x diameter but its wall is only ~18 um plating, so use 2 mm of equivalent width per amp - about double the 1 oz surface-trace rule; measured table: 0.3 mm via 0.4 A, 0.6 mm 0.9 A, 1.0 mm 1.5 A. Count vias against that, not against the drill area.

Rule: thermal_via_drill_mm=0.3 thermal_via_pitch_mm=1.2 via_width_per_amp_mm=2.0

Envelope: pdiss_w={"max": 5}

Sources: reference/knowledge/sources/rohm-buck-pcb-layout-an.pdf p.3 s4 (thermal via); reference/knowledge/sources/rohm-buck-pcb-layout-an.pdf p.10 s10-3 (via current)

## buck-two-layer-ground-shield-continuity [emi, return-path] (topology/approved)

Three boards with identical top and bottom layouts, materials and operating conditions were scanned for radiated EMI: a 2-layer board with no ground-plane shielding peaked at 34 dBuV/m; a 4-layer board with two unbroken mid-layer ground planes peaked at 28.5; a 4-layer board whose planes carry a rectangular cut right under the SW node peaked at 33.5 - i.e. breaking the shield under the noisy node throws away the whole benefit of the extra layers and lands back at 2-layer performance. The mechanism is the mirror return: an unbroken plane under a top-layer current lets an opposite-direction image current form directly beneath, shrinking the loop so the two fields nearly cancel. Three consequences at 2 layers. (1) The ~5 dBuV/m is a budget item, not a surprise: the part's own layout rule 5 asks for a ground plane in a middle layer and 10.1.1 recommends a 4-layer 2/1/1/2 oz stack, so a 2-layer board is a documented deviation and the EMI margin must absorb it. (2) The bottom pour IS the shield - nothing may be routed across it under the hot loop, the SW node or the inductor, because a bottom-layer trace there is precisely the cut that measured 33.5 dBuV/m. (3) The datasheet's "constrain PGND, VIN and SW to one side of the ground plane and put sensitive routes on the other" becomes one side of the BOARD, not one side of a layer stack.

Rule: measured_peak_2l_dbuv_per_m=34 measured_peak_4l_cut_under_sw_dbuv_per_m=33.5 measured_peak_4l_unbroken_dbuv_per_m=28.5 plane_continuity=no route may break the ground pour under the hot loop, the SW node or the inductor two_layer_penalty=about 5 dBuV/m against a 4-layer board with unbroken mid-planes vendor_baseline=the datasheet asks for a mid-layer ground plane and a 4-layer 2/1/1/2 oz stack - 2 layers is a documented deviation

Envelope: board_layers={"max": 2}

Sources: reference/knowledge/sources/snva721a.pdf p.6 2.4 Ground Shielding, Figures 9-14; reference/knowledge/sources/lmr33630.pdf p.33 10.1 Layout Guidelines, rules 5 and 8; reference/knowledge/sources/lmr33630.pdf p.34 10.1.1 Ground and Thermal Considerations

## buck-upstream-inrush-limit [inrush, selection] (topology/approved)

Upstream bulk limit trap: a buck's input capacitance is bounded by the SOURCE, not by the buck. USB 2.0 allows 10 uF || 44 ohm at attach (spec s7.2.4.1); a USB-PD sink is allowed 100 uF under contract (cSnkBulkPd); PoE has its own inrush envelope. Check the source's rule BEFORE sizing C_IN. Soft-start makes OUTPUT caps invisible to the source's inrush test (usb-buck power.md s6) - so the output bank is sized by load step, not by the source rule.

Rule: usb2_attach_limit=10 uF or 44 ohm-limited at attach (s7.2.4.1) usbpd_sink_bulk_uf_max=100

Envelope: source_kind={"in": ["usb", "usb-pd", "poe"]}

Sources: boards/usb-buck/research/power.md s6-7 (inrush vs source rules); boards/usb-buck/research/interface-usb.md attach capacitance

## cot-ripple-injection-raises-vout [feedback] (family/approved)

A constant-on-time regulator ends its off-time when FB falls back through VREF, so it regulates the VALLEY of FB, not its average. With Type 3 injection the ramp is AC-coupled into FB and its mean is genuinely zero, so FB_dc = VREF + Vramp/2 and VOUT = (1 + R_top/R_bot) x (VREF + Vramp_pkpk/2) - NOT VREF times the divider ratio. On an LM5017 at 78.6 mV of injected ripple that is +3.2 %: 5.06 V where a valley calculation says 4.90 V, which is enough to put a 5 V rail over a downstream driver's recommended VDD max. A P4 reviewer computed the rail min/nom/max without the term and concluded the nominal was 100 mV low. Both readings matter in practice because Vramp carries Rr, Cr and K tolerance, so solve the divider against the UNION - effective reference in [VREF_min, VREF_max + Vramp_max/2] - and check both corners rather than picking one model. Also check the FB overvoltage comparator against FB's PEAK (VREF + full Vramp): the LM5017 trips at 1.62 V and terminates the on-time pulse.

Rule: applies_when=constant-on-time regulator with Type 3 (AC-coupled) ripple injection fb_overvoltage=compare against FB PEAK (VREF + full Vramp), not its average solve_against=union of [VREF_min, VREF_max + Vramp_max/2] - check both corners vout=(1 + R_top/R_bot) * (VREF + Vramp_pkpk/2)

Envelope: control_kind={"in": ["cot"]} injection_kind={"in": ["type3"]}

Sources: boards/rf-de-20m/LEARNINGS.md 2026-08-08 Type 3 ripple injection RAISES the DC output; boards/rf-de-20m/parts/C34355.pdf LM5017 (the part this was measured on)
