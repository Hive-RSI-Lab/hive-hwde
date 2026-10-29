"""routelib.dsn_merge_wires: KiCad's DSN export splits pre-routed copper into
many short wires, which overflow Freerouting 2.2.4's stack in
PolylineTrace.combine (PCB-0019). Pure text tests on synthetic DSN snippets;
no KiCad needed."""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / ".claude" / "skills" / "hwde" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
import routelib  # noqa: E402

merge = routelib.dsn_merge_wires


def _dsn(*wiring: str) -> str:
    # the parser header's lone quote is real KiCad output: a naive
    # quote-aware scan reads everything after it as one string
    body = "\n".join("    " + w for w in wiring)
    return ("(pcb test.dsn\n  (parser\n    (string_quote \")\n"
            "    (space_in_quoted_tokens on)\n  )\n"
            "  (resolution um 10)\n  (unit um)\n"
            "  (network\n    (net /D+\n      (pins U1-1 J1-2)\n    )\n  )\n"
            f"  (wiring\n{body}\n  )\n)\n")


def _wire(pts: str, net: str = "/D+", typ: str = "protect",
          layer: str = "F.Cu", width: str = "200") -> str:
    return f"(wire (path {layer} {width}  {pts})(net {net})(type {typ}))"


def _paths(text: str) -> list[list[str]]:
    """Each wire's point list as 'x y' strings, in text order."""
    out = []
    for m in re.finditer(r"\(path \S+ \S+((?:\s+-?[\d.]+)+)\)", text):
        n = m.group(1).split()
        out.append([f"{n[k]} {n[k + 1]}" for k in range(0, len(n), 2)])
    return out


def _segments(text: str) -> int:
    return sum(len(p) - 1 for p in _paths(text))


def test_four_segment_chain_becomes_one_path():
    # out of order and two reversed: the chain is found by endpoints alone
    text = _dsn(_wire("3000 0  2000 0"),
                _wire("0 0  1000 0"),
                _wire("3000 0  4000 -500.5"),
                _wire("2000 0  1000 0"))
    out, n = merge(text)
    assert n == 4
    paths = _paths(out)
    assert len(paths) == 1 and len(paths[0]) == 5
    assert paths[0] in (
        ["0 0", "1000 0", "2000 0", "3000 0", "4000 -500.5"],
        ["4000 -500.5", "3000 0", "2000 0", "1000 0", "0 0"])
    assert "-500.5" in out                       # original strings kept
    assert "(net /D+)(type protect))" in out
    assert out.count("(wire ") == 1
    assert out.startswith(text[:text.index("(wiring")])
    assert "\n\n" not in out                     # deleted wires take their line


def test_t_junction_stays_split_at_the_branch():
    # P = (1000, 0) carries three wires; C-D continues one leg
    text = _dsn(_wire("0 0  1000 0"), _wire("1000 0  2000 0"),
                _wire("1000 0  1000 1000"), _wire("1000 1000  1000 2000"))
    out, n = merge(text)
    assert n == 2
    paths = sorted(_paths(out))
    assert ["0 0", "1000 0"] in paths
    assert ["1000 0", "2000 0"] in paths
    assert ["1000 0", "1000 1000", "1000 2000"] in paths
    assert _segments(out) == _segments(text) == 4


def test_differing_wires_are_not_merged():
    for other in (_wire("1000 0  2000 0", layer="B.Cu"),
                  _wire("1000 0  2000 0", width="250"),
                  _wire("1000 0  2000 0", net="/D-"),
                  _wire("1000 0  2000 0", typ="fix"),
                  "(wire (path F.Cu 200  1000 0  2000 0)(net /D+))"):
        text = _dsn(_wire("0 0  1000 0"), other)
        assert merge(text) == (text, 0), other


def test_via_on_the_joint_ends_the_chain():
    text = _dsn(_wire("0 0  1000 0"), _wire("1000 0  2000 0"),
                '(via "Via[0-1]_600:300_um" 1000 0 (net /D+)(type protect))')
    assert merge(text) == (text, 0)
    # a via elsewhere does not stop it
    text = _dsn(_wire("0 0  1000 0"), _wire("1000 0  2000 0"),
                "(via Via[0-1]_600:300_um 5000 5000 (net /D+))")
    out, n = merge(text)
    assert n == 2 and "(via Via[0-1]_600:300_um 5000 5000" in out


def test_quoted_net_and_multi_point_paths_merge():
    net = '"Net-(C1-Pad2)"'
    text = _dsn(_wire("0 0  500 500  1000 500", net=net),
                _wire("2000 0  1500 500  1000 500", net=net))
    out, n = merge(text)
    assert n == 2
    assert _paths(out)[0] == ["0 0", "500 500", "1000 500", "1500 500",
                              "2000 0"]
    assert f"(net {net})" in out


def test_merge_is_idempotent():
    text = _dsn(_wire("0 0  1000 0"), _wire("1000 0  2000 0"),
                _wire("2000 0  3000 0"), _wire("1000 0  1000 1000"),
                _wire("5000 0  6000 0", net="/D-"),
                _wire("6000 0  7000 0", net="/D-"))
    once, n = merge(text)
    assert n == 4                                 # 2 + 2; the T leg stays
    assert merge(once) == (once, 0)
    assert _segments(once) == _segments(text)


def test_cycle_and_closed_chain_left_alone():
    square = _dsn(_wire("0 0  1000 0"), _wire("1000 0  1000 1000"),
                  _wire("1000 1000  0 1000"), _wire("0 1000  0 0"))
    assert merge(square) == (square, 0)
    # B -> A -> B over two wires, B also a branch: would close on itself
    loop = _dsn(_wire("0 0  1000 0"), _wire("1000 0  0 0"),
                _wire("0 0  0 -1000"))
    assert merge(loop) == (loop, 0)


def test_unparseable_input_is_returned_unchanged():
    chain = (_wire("0 0  1000 0"), _wire("1000 0  2000 0"))
    for unbalanced in (_dsn(*chain)[:-3],                 # pcb unclosed
                       _dsn(*chain).replace("(type protect))\n  )",
                                            "(type protect))\n", 1),
                       _dsn(*chain) + ")"):
        assert merge(unbalanced) == (unbalanced, 0)
    no_wiring = "(pcb x (resolution um 10))"
    assert merge(no_wiring) == (no_wiring, 0)
    assert merge("") == ("", 0)
    # an odd coordinate list or a non-numeric one is skipped, not guessed
    for bad in (_wire("1000 0  2000"), _wire("1000 0  2000 zz"),
                "(wire (polygon F.Cu 0  1000 0  2000 0  2000 100))"):
        text = _dsn(_wire("0 0  1000 0"), bad)
        assert merge(text) == (text, 0), bad
