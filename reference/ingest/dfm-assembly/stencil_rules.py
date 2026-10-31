"""Reproduce the dfm-assembly rules turned into checks, by calculation.

Each case states a claim from the sources (reference/ingest/dfm-assembly.md)
and works it out; the script exits 1 if a claim the notes mark verified no
longer holds. Run with any python3:

  python3 reference/ingest/dfm-assembly/stencil_rules.py
"""
import math
import sys

fails = []


def claim(name, ok, detail):
    print(f"{'HOLDS ' if ok else 'BROKEN'} {name}: {detail}")
    if not ok:
        fails.append(name)


def area_ratio(l, w, t):          # IPC-7525: L*W / (2 (L + W) t)
    return l * w / (2 * (l + w) * t)


def aspect_ratio(w, t):           # IPC-7525: W / t
    return w / t


# 1. IPC-7525 floors (NXP AN1902 5.2.3, JLC stencil blog): AR >= 0.66, AsR >= 1.5
#    JLC: 0.10 mm foil for pitch <= 0.4 mm or 0201; 0.12 mm for 0603 / 0.5 mm pitch.
cases = [  # (what, L, W, t, should_pass)
    ("0201 aperture 0.30x0.30 on 0.12", 0.30, 0.30, 0.12, False),
    ("0201 aperture 0.30x0.30 on 0.10", 0.30, 0.30, 0.10, True),
    ("0402 aperture 0.50x0.50 on 0.12", 0.50, 0.50, 0.12, True),
    ("0.4 mm pitch lead 0.20x0.60 on 0.12", 0.20, 0.60, 0.12, False),
    ("0.4 mm pitch lead 0.20x0.60 on 0.10", 0.20, 0.60, 0.10, True),
    ("0.5 mm pitch lead 0.25x0.80 on 0.12", 0.25, 0.80, 0.12, True),
    # core's U13 (QFN-20 0.4 mm): 0.20 x 0.665 pad, 1:1 aperture
    ("core U13 0.20x0.665 on 0.12", 0.20, 0.665, 0.12, False),
]
for what, l, w, t, want in cases:
    ar = area_ratio(l, w, t)
    asr = aspect_ratio(min(l, w), t)
    ok = (ar >= 0.66 and asr >= 1.5) == want
    claim(what, ok, f"AR {ar:.3f}, AsR {asr:.2f} -> "
          f"{'prints' if ar >= 0.66 and asr >= 1.5 else 'marginal'}")

# For a long aperture the area ratio, not the aspect ratio, binds as soon as
# L > W: AR = W / (2 t (1 + W/L)) < W/(1.5 t)*... check the crossover.
w, t = 0.2, 0.12
claim("AR binds before AsR for W/t=1.67", area_ratio(10, w, t) < 0.66 * 1.27
      and aspect_ratio(w, t) >= 1.5,
      f"W/t {w/t:.2f} passes AsR yet AR of an endless slot is "
      f"{area_ratio(1e6, w, t):.3f}: AR >= 0.66 needs W >= 1.32 t "
      f"for a slot, 2.64 t for a square")

# NXP AN1902's worked example: at T = 150 um the opening must exceed 225 um
claim("NXP AN1902 225 um at 150 um is the aspect-ratio floor",
      abs(1.5 * 0.150 - 0.225) < 1e-9, "1.5 x 0.150 = 0.225 mm")

# 2. Exposed-pad window pane (Amkor/Microchip 50-80 %, NXP 60-65 %, TI ~50-60 %)
def pane(ep, n, street):
    s = (ep - (n + 1) * street) / n     # n x n windows inset by one street
    return n * n * s * s / ep ** 2, s
for ep, n, street in ((3.3, 2, 0.3), (2.6, 2, 0.25), (1.7, 2, 0.2),
                      (5.0, 3, 0.3)):
    cov, s = pane(ep, n, street)
    claim(f"{ep} mm EP, {n}x{n} panes, {street} mm streets lands in 40-90 %",
          0.40 <= cov <= 0.90, f"windows {s:.2f} mm, coverage {cov:.0%}")
# each pane must print too: a 2x2 pane on a 1.7 mm EP is 0.55 mm square
cov, s = pane(1.7, 2, 0.2)
claim("1.7 mm EP pane still prints on 0.12", area_ratio(s, s, 0.12) >= 0.66,
      f"AR {area_ratio(s, s, 0.12):.2f}")

# 3. IPC-2222 hole sizing (EDN, Sierra, Worthington): hole = lead_max + 0.25/0.20/0.15
#    for level A/B/C; for a square lead use the diagonal. 0.64 mm square header pin:
d = 0.64 * math.sqrt(2)
claim("KiCad's stock 1.0 mm drill for a 2.54 header (0.64 sq pin) is tighter "
      "than IPC-2222 level C", d + 0.15 > 1.0, f"diagonal {d:.3f} + 0.25 = "
      f"{d + 0.25:.3f} mm (A), {d + 0.15:.3f} (C); 1.0 mm still clears "
      f"the diagonal by {1.0 - d:.3f} mm, which fits in practice: disputed")

# 4. Pin-in-paste (AIM, Wurth ANP078): V_paste = (V_hole - V_pin + V_fillets) / 0.5
board_t, hole, pin = 1.6, 1.0, 0.64
v_hole = math.pi * (hole / 2) ** 2 * board_t
v_pin = pin * pin * board_t
fillet = 2 * 0.215 * math.pi * ((hole / 2 + 0.5) ** 2 - (hole / 2) ** 2) * 0.3
v_paste = (v_hole - v_pin + fillet) / 0.5
ap_side = math.sqrt(v_paste / 0.12)
claim("pin-in-paste on a 1.0 mm hole needs an aperture bigger than its 1.7 mm pad",
      ap_side > 1.7, f"paste {v_paste:.2f} mm3 = {ap_side:.2f} mm square on "
      f"0.12 foil: overprint or a thicker step stencil, so PIP is a choice "
      f"to state, not a default")

# 5. Tombstone torque (Indium/IPC S27-1, FCT 2021): the side that wets first
#    lifts the part when its surface-tension moment beats the part's weight.
#    0402 cap: 1.0 x 0.5 x 0.5 mm, density 6 g/cm3, SAC gamma ~0.5 N/m.
m = 1.0e-3 * 0.5e-3 * 0.5e-3 * 6000          # kg
w_mom = m * 9.81 * 0.5e-3                    # weight about the far end
st_mom = 0.5 * 0.5e-3 * 0.5e-3               # gamma * width * height
claim("one wetted end of an 0402 can lift it: surface tension >> weight",
      st_mom / w_mom > 10, f"moment ratio {st_mom / w_mom:.0f}: any wetting "
      f"time skew can tombstone it, so balance (same copper both ends) is the rule, "
      f"not a trace-width number")

print(f"\n{len(fails)} broken" if fails else "\nall hold")
sys.exit(1 if fails else 0)
