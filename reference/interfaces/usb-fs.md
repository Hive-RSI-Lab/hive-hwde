# Canonical interface fragment: USB 2.0 full-speed device

Machine-readable half: `usb-fs.json` (exact constraints_schema shapes).
Seeded T6 (2026-08-06) from `boards/usb-buck/research/interface-usb.{json,md}`
- a P1 fragment opus-verified against primary sources whose board passed P8
verify 8/8 first try. The full worked derivation (with the STM32F103-specific
half) stays in that file; this copy keeps the CLASS-level canon.

HOW TO USE (research-interface-spec agents): START here, do not re-derive the
sourced constants. Your job on a repeat USB-FS interface is validate-and-adapt:
(1) confirm the device really is FS-only (no HS path), (2) adapt net names to
the sheet plan's proposals, (3) resolve the stackup-dependent and PHY-specific
rows below against YOUR board, (4) mark every delta from this file in your md.

## Sources

| Tag | Document |
|-----|----------|
| USB2.0 | Universal Serial Bus Specification Rev 2.0, April 2000 |
| AN4879 | ST AN4879 Rev 5 "USB hardware and PCB guidelines using STM32 MCUs" |
| AN11392 | NXP AN11392 Rev 1.1 "Guidelines for full-speed USB on NXP's LPC microcontrollers" (the FS layout guidance; ST removed its own at AN4879 Rev 3) |
| DS5319 | ST STM32F103x8/xB datasheet Rev 18 (worked-example PHY) |

## The numbers that bind layout

| Quantity | Value | Source |
|---|---|---|
| Signalling rate | 12 Mb/s +/-0.25% (T_FDRATE) | USB2.0 Table 7-9 |
| Driver rise/fall (10-90%, 50 pF) | 4 ns min, 20 ns max -> `t_rise_ns: 4.0` (conservative) | USB2.0 Table 7-9, 7.1.2.1 |
| Driver output impedance Z_DRV | 28-44 ohm, embedded in compliant PHY pads | USB2.0 Table 7-9 |
| Board differential impedance | 90 ohm +/-15% nominal | USB2.0 7.1.1.3 |
| Upstream line capacitance | <= 100 pF/line; D+ vs D- match within 10% | USB2.0 7.1.6.1 |
| ESD-diode capacitance for FS | <= 50 pF/line recommended | AN11392 2.6 |
| Bus pull-up | 1.5 kohm +/-5% on D+ to 3.0-3.6 V (PHY-dependent home, see below) | USB2.0 7.1.5.1 + Table 7-7 |
| VBUS load at the device | <= 10 uF in parallel with 44 ohm, else inrush limiting | USB2.0 7.2.4.1 |
| Suspend current (incl. pull-up/-down) | 500 uA low-power device | USB2.0 7.2.3 |

Scale context (computed with `lib/impedance.py` on JLC04161H-3313): 5.81 ps/mm
-> critical length at t_r/6 is ~115 mm, physical skew budget (10% of edge)
~69 mm. FS traces on a small board are 3-6x below transmission-line territory,
which is why the honest reads below say "short and coupled beats impedance".

## Judgment calls carried by the JSON (re-verify, then reuse)

- **90 ohm is free-when-published, not needed**: target it only where the
  stackup has a diff_90 profile; on 2-layer with no reference plane DROP it
  (AN11392 3.2 "not critical ... keep your signal traces as short as
  possible"). Never fake a target the stackup cannot express.
- **`gap_mm` is stackup-dependent and therefore NOT in the seed JSON**:
  centre-to-centre pitch = width + gap of the chosen profile (0.52 on
  JLC04161H-3313). Emit after stackup selection or let check_diffpair
  auto-derive.
- **`return_via_radius_mm: 2.0` (ASSUMED)**: the t_rise-derived radius at 4 ns
  is 119.9 mm - larger than the board, so the explicit-radius precedence in
  check_return_path keeps the check meaningful. Zero vias on the pair remains
  the design target.
- **`max_skew_mm`/`max_uncoupled_mm` 5.0 (ASSUMED)**: order-of-magnitude
  inside the physical budget; skew warnings at FS are cosmetic.
- **`k: 3`** - pipeline default corridor; no standard sets one.

## PHY-specific rows (MUST be re-resolved per part)

1. **DP pull-up home**: embedded on many PHYs, EXTERNAL 1.5 kohm (1%) on
   STM32F102/F103 (AN4879 Tables 3/4, DS5319 Table 44 note 2). Check the
   vendor table; the Blue Pill clones' 10 k fails enumeration. Bus-powered:
   direct to +3V3 satisfies USB2.0 7.1.5.1; self-powered: GPIO-switched
   (AN4879 3.1.1).
2. **Series resistors**: NONE for PHYs with embedded Z_DRV (ST, AN4879 FAQ);
   REQUIRED 33R-class on under-impedance PHYs (NXP LPC, AN11392 2.1). Copying
   the wrong vendor's habit breaks the 28-44 ohm window either way.
3. **Clock source**: check for crystal-less USB support. F102/F103: none -
   HSE crystal mandatory (AN4879 2.4 + FAQ). +/-0.25% data-rate tolerance
   makes any +/-50 ppm crystal fine.
4. **Supply floor**: transceiver 3.0-3.6 V; eye tests fail below 3.0 V
   (AN4879 2.5/FAQ) - a real droop constraint on the 3.3 V rail.

## Connector / ESD / shield (class-level)

- ESD array at the receptacle, in-line, matched 2-channel (10% match rule,
  USB2.0 7.1.6.1), GND straight to plane; USBLC6-2SC6 class + VBUS TVS
  (AN4879 Table 11). No ferrite beads on D+/D- (USB2.0 7.1.6.1).
- Micro-B pinout (KiCad `Connector:USB_B_Micro`): 1 VBUS, 2 D-, 3 D+, 4 ID
  (NC for device-only, AN4879 3.3), 5 GND, SH shield (pipeline renumbers
  SH -> 6). Receptacle at a board edge = architect's `placement.edges` entry.
- Shield: direct shell-to-GND bond for a bare single-ground board (ASSUMED;
  USB2.0 6.8 defers to practice). The bead / 1 M || 4.7 nF alternative needs
  its own copper island - decide at P2, not after layout.

## Layout rules to carry into P5-P7 (AN11392 3.1/3.2 + USB2.0 7.1.1.3)

1. Pair parallel, tightly coupled, lengths matched; 2. run SHORT and direct;
3. minimal corners and vias; 4. solid GND under the entire pair, no split or
slot crossed; 5. away from switch nodes/inductors, VBUS routed away from the
pair; 6. VBUS bulk (incl. through-regulator capacitance) <= 10 uF.
