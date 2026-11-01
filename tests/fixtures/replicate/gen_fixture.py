#!/usr/bin/env python3
"""Build the place_replicate fixtures (#482): a 3-channel board whose channel
is ONE hierarchical sheet file (ch.kicad_sch) instanced three times, and a
mutant where channel 3's sheet (ch_x.kicad_sch) carries one extra part (C3).

    python gen_fixture.py OUT_DIR [--mutant]

writes OUT_DIR/rep.kicad_sch + its child sheet(s). The schematic is the
SOURCE of the committed fixture; build.sh turns it into rep.net and
rep.kicad_pcb (board_init, a hand placement of channel 1 and of J1, channel
1's hand routing). Deterministic: every uuid is uuid5 of a fixed name.

Channel (per sheet instance): R1 IN-MID, C1 MID-GND, R2 MID-OUT, C2 OUT-GND
(+ C3 MID-GND in the mutant's channel 3). Root: J1 1x07 header carrying
IN1..3, OUT1..3, GND.
"""
import sys
import uuid
from pathlib import Path

import os
KICAD = Path(os.environ.get("KICAD_SYMBOL_DIR") or Path.home() / "tools/hive-tools/kicad-10.0.6/share/kicad/symbols")
NS = uuid.UUID("6f1c2a4e-4820-4e5b-9b0c-482482482482")
PROJECT = "rep"


def U(name: str) -> str:
    return str(uuid.uuid5(NS, name))


def libsym(lib: str, name: str) -> str:
    t = (KICAD / f"{lib}.kicad_symdir" / f"{name}.kicad_sym").read_text()
    i = t.index(f'(symbol "{name}"')
    d = 0
    for j in range(i, len(t)):
        if t[j] == "(":
            d += 1
        elif t[j] == ")":
            d -= 1
            if d == 0:
                break
    blk = t[i:j + 1]
    return blk.replace(f'(symbol "{name}"', f'(symbol "{lib}:{name}"', 1)


FONT = "(effects (font (size 1.27 1.27)))"
FONT_HIDE = "(effects (font (size 1.27 1.27)) (hide yes))"
FP = {"R": "Resistor_SMD:R_0603_1608Metric",
      "C": "Capacitor_SMD:C_0603_1608Metric",
      "J": "Connector_PinHeader_2.54mm:PinHeader_1x07_P2.54mm_Vertical"}
LIB = {"R": ("Device", "R"), "C": ("Device", "C"),
       "J": ("Connector_Generic", "Conn_01x07")}


def header(sheet_uuid: str, libs: list[str]) -> list[str]:
    out = ['(kicad_sch', '\t(version 20250114)', '\t(generator "eeschema")',
           '\t(generator_version "9.0")', f'\t(uuid "{sheet_uuid}")',
           '\t(paper "A4")', '\t(lib_symbols']
    out += [libsym(*LIB[k]) for k in libs]
    out.append('\t)')
    return out


def wire(a, b, tag):
    return (f'\t(wire (pts (xy {a[0]:.2f} {a[1]:.2f}) (xy {b[0]:.2f} {b[1]:.2f}))'
            f' (stroke (width 0) (type default)) (uuid "{U("w" + tag)}"))')


def label(kind, name, at, ang, tag):
    if kind == "hier":
        return (f'\t(hierarchical_label "{name}" (shape passive) (at {at[0]:.2f}'
                f' {at[1]:.2f} {ang}) (effects (font (size 1.27 1.27))'
                f' (justify left)) (uuid "{U("h" + tag)}"))')
    return (f'\t(label "{name}" (at {at[0]:.2f} {at[1]:.2f} {ang})'
            f' (effects (font (size 1.27 1.27)) (justify left bottom))'
            f' (uuid "{U("l" + tag)}"))')


def symbol(kind, local_ref, value, at, sym_uuid, instances, npins):
    lib = "%s:%s" % LIB[kind]
    s = [f'\t(symbol (lib_id "{lib}") (at {at[0]:.2f} {at[1]:.2f} 0) (unit 1)',
         '\t\t(exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no)',
         f'\t\t(uuid "{sym_uuid}")',
         f'\t\t(property "Reference" "{local_ref}" (at {at[0] + 2.54:.2f} {at[1]:.2f} 0) {FONT})',
         f'\t\t(property "Value" "{value}" (at {at[0] + 2.54:.2f} {at[1] + 2.54:.2f} 0) {FONT})',
         f'\t\t(property "Footprint" "{FP[kind]}" (at {at[0]:.2f} {at[1]:.2f} 0) {FONT_HIDE})',
         f'\t\t(property "Datasheet" "" (at {at[0]:.2f} {at[1]:.2f} 0) {FONT_HIDE})']
    for p in range(1, npins + 1):
        s.append(f'\t\t(pin "{p}" (uuid "{U(sym_uuid + "p" + str(p))}"))')
    s.append(f'\t\t(instances (project "{PROJECT}"')
    for path, ref in instances:
        s.append(f'\t\t\t(path "{path}" (reference "{ref}") (unit 1))')
    s.append('\t\t))')
    s.append('\t)')
    return "\n".join(s)


# channel parts: (kind, local ref, value, x, pin1 net, pin2 net)
CH = [("R", "R1", "1k", 50.8, "IN", "MID"),
      ("C", "C1", "10n", 63.5, "MID", "GND"),
      ("R", "R2", "1k", 76.2, "MID", "OUT"),
      ("C", "C2", "10n", 88.9, "OUT", "GND")]
EXTRA = ("C", "C3", "100n", 101.6, "MID", "GND")
HIER = {"IN", "OUT", "GND"}


def child(fname: str, parts, instances_of) -> str:
    """instances_of(local_ref) -> [(path, ref)]"""
    out = header(U(fname), ["R", "C"])
    y = 50.8
    for kind, lref, val, x, n1, n2 in parts:
        su = U(f"sym:{lref}")   # same uuid in ch and ch_x: the local identity
        out.append(symbol(kind, lref, val, (x, y), su, instances_of(lref), 2))
        for pin, net, sy, ey, ang in ((1, n1, y - 3.81, y - 6.35, 90),
                                      (2, n2, y + 3.81, y + 6.35, 270)):
            tag = f"{fname}{lref}{pin}"
            out.append(wire((x, sy), (x, ey), tag))
            out.append(label("hier" if net in HIER else "local", net,
                             (x, ey), ang, tag))
    out.append('\t(embedded_fonts no)')
    out.append(')')
    return "\n".join(out) + "\n"


def build(out: Path, mutant: bool) -> None:
    out.mkdir(parents=True, exist_ok=True)
    root = U("root")
    sheets = [(f"ch{k}", U(f"sheet:ch{k}"),
               "ch_x.kicad_sch" if (mutant and k == 3) else "ch.kicad_sch")
              for k in (1, 2, 3)]

    def inst(files):
        def f(lref):
            num = int(lref[1:])
            return [(f"/{root}/{su}", f"{lref[0]}{k}{num:02d}")
                    for k, (name, su, sf) in enumerate(sheets, 1)
                    if sf in files]
        return f

    (out / "ch.kicad_sch").write_text(child("ch.kicad_sch", CH,
                                            inst({"ch.kicad_sch"})))
    if mutant:
        (out / "ch_x.kicad_sch").write_text(
            child("ch_x.kicad_sch", CH + [EXTRA], inst({"ch_x.kicad_sch"})))
    elif (out / "ch_x.kicad_sch").exists():
        (out / "ch_x.kicad_sch").unlink()

    r = header(root, ["J"])
    jx, jy = 25.4, 76.2
    jnets = ["IN1", "IN2", "IN3", "OUT1", "OUT2", "OUT3", "GND"]
    r.append(symbol("J", "J1", "Conn_01x07", (jx, jy), U("sym:J1"),
                    [(f"/{root}", "J1")], 7))
    for i, net in enumerate(jnets):
        py = jy - (7.62 - 2.54 * i)
        r.append(wire((jx - 5.08, py), (jx - 7.62, py), f"J{i}"))
        r.append(label("local", net, (jx - 7.62, py), 180, f"J{i}"))
    for k, (name, su, sf) in enumerate(sheets, 1):
        sx, sy = 50.8, 30.48 + 25.4 * (k - 1)
        r.append(f'\t(sheet (at {sx:.2f} {sy:.2f}) (size 25.40 12.70)'
                 ' (exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no)')
        r.append('\t\t(stroke (width 0.1524) (type solid))'
                 ' (fill (color 0 0 0 0.0000))')
        r.append(f'\t\t(uuid "{su}")')
        r.append(f'\t\t(property "Sheetname" "{name}" (at {sx:.2f} {sy - 0.71:.2f} 0)'
                 ' (effects (font (size 1.27 1.27)) (justify left bottom)))')
        r.append(f'\t\t(property "Sheetfile" "{sf}" (at {sx:.2f} {sy + 13.41:.2f} 0)'
                 ' (effects (font (size 1.27 1.27)) (justify left top)))')
        pins = (("IN", sx, sy + 2.54, 180, -2.54, f"IN{k}"),
                ("GND", sx, sy + 7.62, 180, -2.54, "GND"),
                ("OUT", sx + 25.4, sy + 2.54, 0, 2.54, f"OUT{k}"))
        for pn, px, py, ang, dx, net in pins:
            r.append(f'\t\t(pin "{pn}" passive (at {px:.2f} {py:.2f} {ang})'
                     f' (uuid "{U(su + pn)}") (effects (font (size 1.27 1.27))'
                     f' (justify {"left" if ang == 180 else "right"})))')
        r.append(f'\t\t(instances (project "{PROJECT}" (path "/{root}"'
                 f' (page "{k + 1}"))))')
        r.append('\t)')
        for pn, px, py, ang, dx, net in pins:
            tag = f"S{k}{pn}"
            r.append(wire((px, py), (px + dx, py), tag))
            r.append(label("local", net, (px + dx, py), ang, tag))
    r.append('\t(sheet_instances (path "/" (page "1")))')
    r.append('\t(embedded_fonts no)')
    r.append(')')
    (out / "rep.kicad_sch").write_text("\n".join(r) + "\n")


if __name__ == "__main__":
    build(Path(sys.argv[1]), "--mutant" in sys.argv[2:])
