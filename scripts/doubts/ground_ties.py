"""Is every ground net tied to the board's main ground?

Each ground name (GND, AGND, PGND, DGND, a sheet's own /GND) is a net of its
own until a part joins it: a 0 R, a net tie, a ferrite or a jumper. A ground
that nothing ties floats: the block on it has no return, ERC sees no error
(every pin is on a net) and DRC sees no unrouted copper, because the split
was drawn on purpose and its tie forgotten.
"""
import doubtlib

QUESTION = "Is every ground net tied to the board's main ground?"
DOMAIN = "power"
WHY = "a ground nothing ties floats, and the block on it has no return"


def _grounds(board):
    groups = {}
    for net in board.nets:
        if board.is_ground(net) and not board.is_unconnected(net):
            groups.setdefault(board.group(net), set()).add(net)
    return groups


def applies(board):
    return len({n for g in _grounds(board).values() for n in g}) > 1


def answer(board):
    groups = sorted(_grounds(board).values(),
                    key=lambda g: -sum(len(board.nets[n]) for n in g))
    if not groups:
        return doubtlib.unknown(["the board has no net named as a ground"])
    main = groups[0]
    ev = [f"main ground: {', '.join(sorted(main))} "
          f"({sum(len(board.nets[n]) for n in main)} pins)"]
    fails = []
    for g in groups[1:]:
        pins = sum(len(board.nets[n]) for n in g)
        fails.append({"nets": sorted(g),
                      "msg": f"{', '.join(sorted(g))} ({pins} pins) is not "
                             f"joined to {board.label(next(iter(main)))} by "
                             "any 0 R, net tie, ferrite or jumper"})
    ties = sorted(r for r in board.joins
                  if any(board.is_ground(p["net"])
                         for p in board.pins[r].values()))
    if ties:
        ev.append("ground ties: " + ", ".join(ties))
    return doubtlib.combine(fails, [], ev, f"{len(groups)} ground group(s)")
