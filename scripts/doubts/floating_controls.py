"""Is any IC's control pin (enable, reset, shutdown, mute, select, address,
mode) left unconnected?

A control pin left open reads whatever its internal pull or leakage says.
Some parts pull it to the working level and say so; many don't, and the
part then starts or not by chance, or switches with a finger near it. ERC
lets an open passive pin through and nothing else looks.
"""
import re

import doubtlib

QUESTION = ("Is any IC control pin (enable, reset, shutdown, mute, select, "
            "address, mode) left unconnected?")
DOMAIN = "digital"
WHY = "an open enable or reset reads its leakage: the part starts by chance"

CONTROL_RE = re.compile(
    r"^N?(EN|ENA|ENABLE|CE|SHDN|SD|SDZ|SDB|STBY|STANDBY|PDN|PD|PWDN|RESET|"
    r"RST|NRST|MR|MUTE|SMUTE|CMUTE|OE|CS|SEL|S\d|A\d|ADDR\d*|AD\d|MODE\d*|"
    r"M\d|BOOT\d*|SYNC|FMT|DEMP|GAIN|UVLO|ON|OFF|CTRL)$")


def controls(board):
    out = []
    for ref in board.ics():
        for pin, meta in board.pins[ref].items():
            if CONTROL_RE.match(meta["name"]):
                out.append((ref, pin, meta))
    return out


def applies(board):
    return bool(controls(board))


def answer(board):
    unknowns, ev, n = [], [], 0
    for ref, pin, meta in controls(board):
        n += 1
        if board.is_unconnected(meta["net"]):
            unknowns.append(
                f"{ref} ({board.mpn(ref)}) pin {pin} {meta['name']} is open: "
                "does the datasheet give it an internal pull to the level "
                "the board needs? (else tie it)")
    ev.append(f"{n} control pin(s) on {len(board.ics())} IC(s); "
              f"{n - len(unknowns)} connected")
    return doubtlib.combine([], unknowns, ev)
