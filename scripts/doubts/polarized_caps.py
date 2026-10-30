"""Does every polarised capacitor sit with its + pad on the higher rail?

An electrolytic, polymer or tantalum cap is footprint pad 1 = +. One placed
the wrong way round passes ERC (its pins are passive), DRC and the CPL check
(which follows the footprint), and vents or shorts at the first power-on.
"""
import re

import doubtlib

QUESTION = "Does every polarised capacitor sit with its + pad on the higher rail?"
DOMAIN = "power"
WHY = "a reversed electrolytic or tantalum cap vents or shorts at power-on"

POLAR_RE = re.compile(r"CP_|CP\b|Elec|Tantal|Polymer|_Pol|CASE-[A-E]_|"
                      r"CAP-SMD_BD|Radial|EEE-|POSCAP|OSCON", re.I)


def polar(board):
    return [r for r in board.refs("C", "CP", "CE")
            if POLAR_RE.search(board.footprint(r)) and len(board.pins[r]) == 2]


def applies(board):
    return bool(polar(board))


def answer(board):
    fails, unknowns, ev = [], [], []
    for ref in polar(board):
        pins = board.pins[ref]
        if "1" not in pins or "2" not in pins:
            unknowns.append(f"{ref}: pads aren't 1/2, can't tell which is +")
            continue
        vp = board.rail(pins["1"]["net"])
        vn = board.rail(pins["2"]["net"])
        lp, ln = board.label(pins["1"]["net"]), board.label(pins["2"]["net"])
        if vp is None or vn is None:
            unknowns.append(f"{ref} (+ {lp}, - {ln}): the voltage of "
                            f"{lp if vp is None else ln} (doubt.py fact --rail)")
        elif vp < vn:
            fails.append({"refs": [ref], "nets": [lp, ln],
                          "msg": f"{ref} + pad on {lp} ({vp} V) sits below its "
                                 f"- pad on {ln} ({vn} V): reversed"})
        else:
            ev.append(f"{ref}: + {lp} {vp} V, - {ln} {vn} V")
    return doubtlib.combine(fails, unknowns, ev,
                            f"{len(polar(board))} polarised cap(s)")
