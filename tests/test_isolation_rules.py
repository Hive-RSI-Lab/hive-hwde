"""rules_gen writes barrier rules from constraints isolation (#595), and the DSN
carries them, so DRC, the router, pours and check_isolation share one number.

Known answers: 250 V, 4 kV, PD2, IIIa is 3.000 mm clearance (Table F.2) and 2.5
mm creepage (Table F.4), raised to 3.000; at 1.5 kV the clearance is 0.5 mm and
the creepage stays 2.5. Live tests build a 4-layer board with two domains.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import bgalib  # noqa: E402
import board_init  # noqa: E402
import check_isolation as ci  # noqa: E402
import env  # noqa: E402
import geom  # noqa: E402
import kc  # noqa: E402
import route_critical as rc  # noqa: E402
import routelib  # noqa: E402
import rules_gen  # noqa: E402

CAP = yaml.safe_load((REPO / "reference" / "jlc_capabilities.yaml").read_text("utf-8")
                    )["design_rules"]["4layer_1oz"]
STACKS = yaml.safe_load((REPO / "reference" / "stackups.yaml").read_text("utf-8"))
STK4 = STACKS["stackups"][STACKS["defaults"][4]]
STK2 = STACKS["stackups"][STACKS["defaults"][2]]
BARRIER = {"name": "b1", "between": ["A", "B"], "standard": "iec60664-1",
           "grade": "basic", "working_v_rms": 250, "impulse_v": 4000,
           "pollution_degree": 2, "material_group": "IIIa", "altitude_m": 2000,
           "inner_min_mm": 1.0}
DOMAINS = {"A": {"nets": ["GND_A", "/a/*"]}, "B": {"nets": ["GND_B", "/b/*"]}}
RATING = {"grade": "basic", "working_v_rms": 400, "impulse_v": 8000, "source": "test"}
COND = ("(A.NetName == 'GND_A' || A.NetName == '/a/*') && "
        "(B.NetName == 'GND_B' || B.NetName == '/b/*')")


def cons(**over):
    barrier = {**BARRIER, **over.pop("barrier", {})}
    return {"isolation": {"domains": DOMAINS, "barriers": [barrier], **over}}


def iso_rules(c, stack=STK4, bg=None, hv=()):
    return rules_gen.isolation_rules(c, CAP, stack, list(hv), bg)


def by_name(rules):
    return {r.name: r for r in rules}


# ------------------------------------------------------------------ the numbers

def test_one_rule_per_barrier_and_layer_class_at_the_requirement():
    rules, facts = iso_rules(cons())
    r = by_name(rules)
    assert list(r) == ["aiee_iso_b1_outer", "aiee_iso_b1_inner"]
    out, inner = r["aiee_iso_b1_outer"], r["aiee_iso_b1_inner"]
    assert (out.minv, out.layer, out.condition) == (3.0, "outer", COND)
    assert (inner.minv, inner.layer, inner.condition) == (1.0, "inner", COND)
    assert facts[0]["clearance_mm"] == 3.0 and facts[0]["verified"] is True


def test_the_same_number_as_check_isolation_reads():
    import isotables
    for over in ({"impulse_v": 1500}, {"impulse_v": 4000, "working_v_rms": 300},
                 {"grade": "reinforced", "impulse_v": 2500}):
        c = cons(barrier=over)
        want = isotables.requirement(c["isolation"]["barriers"][0])["creepage"]["mm"]
        got = by_name(iso_rules(c)[0])["aiee_iso_b1_outer"].minv
        assert want <= got < want + 1e-4


def test_two_layer_board_has_no_inner_rule():
    assert [r.name for r in iso_rules(cons(), STK2)[0]] == ["aiee_iso_b1_outer"]


def test_rule_rounds_up_never_under_the_table():
    assert rules_gen._mm_up(2.46661) == 2.4667
    assert rules_gen._mm_up(3.0) == 3.0


def test_no_slot_credit_and_the_comment_says_which_number():
    """1.5 kV: clearance 0.5, creepage 2.5. A DRU is a straight distance, so it
    holds the full creepage number unless the board says a slot earns it."""
    r = by_name(iso_rules(cons(barrier={"impulse_v": 1500}))[0])["aiee_iso_b1_outer"]
    assert r.minv == 2.5
    assert "no slot credit" in r.comment
    assert r.render().startswith("# barrier b1 A/B, outer layers: creepage 2.500 mm")
    s = by_name(iso_rules(cons(barrier={"impulse_v": 1500, "slot_earns_creepage": True}))[0])
    assert s["aiee_iso_b1_outer"].minv == 0.5
    assert "slot_earns_creepage" in s["aiee_iso_b1_outer"].comment
    assert "check_isolation" in s["aiee_iso_b1_outer"].comment
    # the inner rule has nothing to do with a slot
    assert s["aiee_iso_b1_inner"].minv == 1.0


def test_unread_row_is_said_in_the_comment():
    c = cons(barrier={"standard": "iec62368-1"})
    r = by_name(iso_rules(c)[0])["aiee_iso_b1_outer"]
    assert "not read from a document" in r.comment
    inner = by_name(iso_rules(cons(barrier={"inner_min_mm": None}))[0])["aiee_iso_b1_inner"]
    assert "from memory" in inner.comment
    # as check_isolation: a warning until the board declares it
    assert inner.severity == "warning"
    assert by_name(iso_rules(cons())[0])["aiee_iso_b1_inner"].severity is None


# ---------------------------------------------------------------- what's refused

@pytest.mark.parametrize("c, word", [
    (cons(barrier={"altitude_m": None}), "altitude_m"),
    (cons(barrier={"between": ["A", "Z"]}), "unknown domain"),
    (cons(barrier={"working_v_rms": 99999}), "above the table"),
    ({"isolation": {"domains": DOMAINS, "barriers": []}}, "at least one barrier"),
    ({"isolation": {"domains": {"A": {"nets": ["/a[0]"]}, "B": DOMAINS["B"]},
                    "barriers": [BARRIER]}}, "no [..] set"),
    (cons(parts={"U*": RATING}), "bad ref"),
])
def test_a_declaration_it_cant_read_is_an_error(c, word):
    with pytest.raises(ValueError, match=word.replace("[", r"\[").replace("]", r"\]")
                       .replace("*", r"\*")):
        iso_rules(c)


def test_no_isolation_block_changes_nothing():
    assert iso_rules({}) == ([], [])
    rules, report = rules_gen.build({}, CAP, STK4, False)
    assert not [r for r in rules if r.name.startswith("aiee_iso_")]
    assert report["isolation_rules"] == []


# ------------------------------------------------------------------- precedence

def test_rules_follow_hv_and_package_rules():
    c = {**cons(), "voltages": [{"net": "/a/V", "voltage": 54}],
         "package_ratings": [{"ref": "U2", "clearance_mm": 0.1}]}
    names = [r.name for r in rules_gen.build(c, CAP, STK4, False)[0]]
    iso = names.index("aiee_iso_b1_outer")
    assert iso > names.index("aiee_pkg_rating_U2") > names.index("aiee_hv_54v_a_V")
    assert "aiee_iso_" in bgalib.KEEP_LAST


def test_a_barrier_rule_carries_a_larger_hv_rule_it_would_override():
    hv = [{"net": "/a/V", "clearance_mm": 4.2}]
    r = by_name(iso_rules(cons(), hv=hv)[0])
    assert r["aiee_iso_b1_outer"].minv == 4.2 and r["aiee_iso_b1_inner"].minv == 4.2
    assert "HV" in r["aiee_iso_b1_outer"].comment
    # a pair rule across the barrier counts, one inside a domain or a waiver doesn't
    across = [{"pair": ["/b/V", "/a/V"], "clearance_mm": 5.0}]
    assert by_name(iso_rules(cons(), hv=across)[0])["aiee_iso_b1_outer"].minv == 5.0
    inside = [{"pair": ["/a/V", "/a/W"], "clearance_mm": 5.0},
              {"pair": ["/a/V", "/b/V"], "clearance_mm": 9.0, "waiver": True},
              {"net": "OTHER", "clearance_mm": 9.0}]
    assert by_name(iso_rules(cons(), hv=inside)[0])["aiee_iso_b1_outer"].minv == 3.0


def test_barrier_rules_never_read_as_net_wide():
    """Every reader of parse_dru_rules takes a NetName as net-wide (route_auto's
    DSN classes, KRT's --net-clearances, route_cleanup's hard floor): a barrier
    read that way holds a net 3 mm from its own domain."""
    text = rules_gen.render_dru(iso_rules(cons(parts={"U1": RATING}))[0])
    assert rc.parse_dru_rules(text) == []
    # and the reader that wants them gets the domains and the larger number
    got = routelib.dru_barriers(text)
    assert got == [{"a": ["GND_A", "/a/*"], "b": ["GND_B", "/b/*"], "clearance_mm": 3.0}]


# ------------------------------------------------------------------- rated parts

def test_part_exception_only_for_a_rating_that_covers():
    c = cons(parts={"U1": RATING,
                    "U2": {**RATING, "impulse_v": 2500},      # short
                    "U3": {"grade": "basic"}})                # says nothing
    rules, facts = iso_rules(c)
    r = by_name(rules)
    assert "aiee_iso_part_U1_b1" in r
    assert "aiee_iso_part_U2_b1" not in r and "aiee_iso_part_U3_b1" not in r
    p = r["aiee_iso_part_U1_b1"]
    assert p.minv == float(CAP["min_clearance_mm"])
    assert p.condition == ("A.memberOfFootprint('U1') && B.memberOfFootprint('U1') && "
                           + COND)
    assert list(r).index("aiee_iso_part_U1_b1") > list(r).index("aiee_iso_b1_inner")
    outer = facts[0]
    assert outer["parts_excepted"] == [{"ref": "U1", "rule": "aiee_iso_part_U1_b1"}]
    assert sorted(x["ref"] for x in outer["parts_refused"]) == ["U2", "U3"]
    assert "test" in p.comment


# --------------------------------------------------------------------------- DSN

_DSN = '''(pcb x
  (parser
    (string_quote ")
    (space_in_quoted_tokens on)
  )
  (resolution um 10)
  (structure
    (layer F.Cu (type signal) (property (index 0)))
  )
  (network
    (class kicad_default /a/S /b/S GND_A "/c d"
      (circuit
        (use_via "Via[0-3]_450:200_um")
      )
      (rule
        (width 200)
        (clearance 125)
      )
    )
    (class Pwr /b/P
      (circuit
        (use_via "Via[0-3]_450:200_um")
      )
      (rule
        (width 500)
        (clearance 125)
      )
    )
  )
)
'''
_B = [{"a": ["GND_A", "/a/*"], "b": ["GND_B", "/b/*"], "clearance_mm": 3.0}]


def test_dsn_splits_classes_by_side_and_writes_pair_clearances():
    text, pairs = routelib.dsn_apply_barriers(_DSN, _B)
    assert text.count("(") == text.count(")")
    assert routelib._sexp_end(routelib._parser_blanked(text), 0) == len(text.rstrip())
    assert '(class kicad_default "/c d"' in text         # no domain: stays
    assert "(class kicad_default_iso0a /a/S GND_A" in text
    assert "(class kicad_default_iso0b /b/S" in text
    assert "(class Pwr_iso0b /b/P" in text
    assert text.count("(width 500)") == 1 and text.count("(width 200)") == 3
    assert {tuple(p["classes"]) for p in pairs} == {
        ("kicad_default_iso0a", "kicad_default_iso0b"),
        ("kicad_default_iso0a", "Pwr_iso0b")}
    assert ("(class_class (classes kicad_default_iso0a Pwr_iso0b)\n"
            "      (rule (clearance 3000))\n    )\n  )\n)") in text     # last in network


def test_dsn_glob_folds_case_as_kicad_does():
    text, _ = routelib.dsn_apply_barriers(_DSN.replace("/b/P", "/B/P"), _B)
    assert "(class Pwr_iso0b /B/P" in text


def test_dsn_unchanged_without_a_barrier_or_a_side():
    assert routelib.dsn_apply_barriers(_DSN, []) == (_DSN, [])
    none = [{"a": ["/x/*"], "b": ["/y/*"], "clearance_mm": 3.0}]
    assert routelib.dsn_apply_barriers(_DSN, none) == (_DSN, [])


def test_dsn_shuffle_keeps_the_pair_clearances():
    text, _ = routelib.dsn_apply_barriers(_DSN, _B)
    out = routelib.dsn_shuffle(text, seed=2)
    assert out.count("(class_class") == 2


# -------------------------------------------------------------------------- live

def _tools():
    cli = env.find_kicad_cli()
    if cli is None:
        pytest.skip("needs kicad-cli")
    return cli


def _pad(ref, x, y, nets, dx=0.0):
    pads = "".join(
        f'    (pad "{i + 1}" smd rect (at {i * dx} 0) (size 1 1)\n'
        f'      (layers "F.Cu" "F.Mask") (net "{n}") (uuid "p-{ref}-{i}"))\n'
        for i, n in enumerate(nets))
    return (f'  (footprint "t:{ref}" (layer "F.Cu") (at {x} {y})\n'
            f'    (property "Reference" "{ref}" (at 0 -2 0) (layer "F.SilkS") '
            f'(uuid "r-{ref}"))\n{pads}  )\n')


def _track(net, x0, y0, x1, y1, layer="F.Cu", tag="t"):
    return (f'  (segment (start {x0} {y0}) (end {x1} {y1}) (width 0.25) (layer "{layer}") '
            f'(net "{net}") (uuid "{tag}-{net.replace("/", "_")}-{y0}-{layer}"))\n')


def _board(tmp_path, body, c, name="iso"):
    """A 4-layer board 60 x 30, its .kicad_pro and rules_gen's .kicad_dru."""
    pcb = tmp_path / f"{name}.kicad_pcb"
    nets = ["/a/S", "/a/T", "/b/S", "/b/T"]
    decl = "".join(f'(net {i + 1} "{n}") ' for i, n in enumerate(nets))
    pcb.write_text(f"""(kicad_pcb
  (version 20260206) (generator "test")
  (general (thickness 1.6))
  (layers (0 "F.Cu" signal) (4 "In1.Cu" signal) (6 "In2.Cu" signal) (2 "B.Cu" signal)
    (13 "F.Paste" user) (1 "F.Mask" user) (3 "B.Mask" user)
    (5 "F.SilkS" user "F.Silkscreen") (31 "F.CrtYd" user "F.Courtyard")
    (25 "Edge.Cuts" user))
  (setup
  )
  (net 0 "") {decl}
  (gr_rect (start 0 0) (end 60 30) (stroke (width 0.1)) (fill no) (layer "Edge.Cuts"))
{body})
""", encoding="utf-8")
    board_init.inject_stackup(pcb, STK4)
    pcb.with_suffix(".kicad_pro").write_text(
        json.dumps(board_init.build_pro(name, CAP)), encoding="utf-8")
    cf = tmp_path / "constraints.json"
    cf.write_text(json.dumps(c), encoding="utf-8")
    assert rules_gen.main(["--constraints", str(cf), "--layers", "4",
                           "--out-dru", str(pcb.with_suffix(".kicad_dru")),
                           "--pro", str(pcb.with_suffix(".kicad_pro")),
                           "--pcb", str(pcb),
                           "--out", str(tmp_path / "rg.json")]) == 0
    return pcb


def _drc_clearance(cli, pcb, refill=False):
    rep = kc.run_drc(cli, pcb, refill=refill)
    return [v for v in rep["violations"] if v.get("check") == "clearance"]


def _iso(pcb, c):
    return ci.check(pcb, c)


def _kinds(viols):
    return sorted({v["kind"] for v in viols})


@pytest.mark.parametrize("layer, rule, need, kind", [
    ("F.Cu", "aiee_iso_b1_outer", 3.0, "iso_clearance"),
    ("B.Cu", "aiee_iso_b1_outer", 3.0, "iso_clearance"),
    ("In1.Cu", "aiee_iso_b1_inner", 2.0, "iso_inner"),
])
def test_live_drc_and_check_isolation_agree_on_the_same_copper(tmp_path, layer, rule, need, kind):
    """A planted 2 mm (1 mm inner: inner_min 2.0 here) gap: both flag it, and at
    the requirement neither does. Tracks 0.25 wide, so the edge gap is y - 0.25."""
    cli = _tools()
    c = cons(barrier={"inner_min_mm": 2.0})
    planted = need - 1.0
    y = 10 + planted + 0.25
    body = _track("/a/S", 5, 10, 40, 10, layer) + _track("/b/S", 5, y, 40, y, layer)
    pcb = _board(tmp_path, body, c)
    hits = _drc_clearance(cli, pcb)
    assert len(hits) == 1 and rule in hits[0]["msg"]
    assert f"actual {planted:.4f} mm" in hits[0]["msg"]
    assert {"/a/S", "/b/S"} <= {i["msg"].split("[")[1].split("]")[0] for i in hits[0]["items"]}
    viols, _ = _iso(pcb, c)
    got = [v for v in viols if v["kind"] == kind]
    assert got and got[0]["spacing_mm"] == pytest.approx(planted, abs=1e-3)
    assert got[0]["required_mm"] == pytest.approx(need)
    # same copper at exactly the requirement
    y = 10 + need + 0.25
    body = _track("/a/S", 5, 10, 40, 10, layer) + _track("/b/S", 5, y, 40, y, layer)
    pcb = _board(tmp_path, body, c, name="ok")
    assert _drc_clearance(cli, pcb) == []
    assert kind not in _kinds(_iso(pcb, c)[0])


def test_live_a_domains_own_nets_keep_their_ordinary_spacing(tmp_path):
    cli = _tools()
    c = cons()
    body = _track("/a/S", 5, 10, 40, 10) + _track("/a/T", 5, 11, 40, 11)
    pcb = _board(tmp_path, body, c)
    assert _drc_clearance(cli, pcb) == []


def test_live_slot_board_rule_falls_to_the_clearance(tmp_path):
    """slot_earns_creepage: a 1 mm gap (clearance 0.5, creepage 2.5) passes DRC,
    and check_isolation still fails the creepage when no slot is there."""
    cli = _tools()
    c = cons(barrier={"impulse_v": 1500, "slot_earns_creepage": True})
    body = _track("/a/S", 5, 10, 40, 10) + _track("/b/S", 5, 11.25, 40, 11.25)
    pcb = _board(tmp_path, body, c)
    assert _drc_clearance(cli, pcb) == []
    assert "iso_creepage" in _kinds(_iso(pcb, c)[0])


def test_live_rated_part_excepted_pad_to_pad_only(tmp_path):
    """Pads 2 mm apart in one part: rated, DRC passes those pads and the gap is
    still reported; a track of the other domain at that gap is still flagged;
    short or no rating, DRC flags the pads, as check_isolation does."""
    cli = _tools()
    part = _pad("U1", 20, 15, ["/a/S", "/b/S"], dx=3.0)      # pad edges 2 mm apart
    c = cons(parts={"U1": RATING})
    pcb = _board(tmp_path, part, c)
    assert _drc_clearance(cli, pcb) == []
    viols, facts = _iso(pcb, c)
    assert _kinds(viols) == ["iso_part_pin_gap"]
    assert facts["barriers"][0]["parts"][0]["pin_gap_mm"] == pytest.approx(2.0, abs=1e-3)
    # a track off the part's pad is not the part's pad: flagged by both
    track = part + _track("/b/T", 19.5, 17.125, 40, 17.125)
    pcb = _board(tmp_path, track, c, name="track")
    hits = _drc_clearance(cli, pcb)
    assert hits and all("aiee_iso_b1_outer" in h["msg"] for h in hits)
    assert "iso_clearance" in _kinds(_iso(pcb, c)[0])
    for name, parts, want in (("short", {"U1": {**RATING, "impulse_v": 2500}}, "iso_part_rating_short"),
                              ("none", {}, "iso_part_unrated")):
        c2 = cons(parts=parts)
        pcb = _board(tmp_path, part, c2, name=name)
        hits = _drc_clearance(cli, pcb)
        assert len(hits) == 1 and "aiee_iso_b1_outer" in hits[0]["msg"]
        assert want in _kinds(_iso(pcb, c2)[0])


def test_live_pour_stays_out_of_the_gap(tmp_path):
    """A pour of domain B across the board, a track of domain A through it: a
    refill leaves the barrier's 3 mm. Without the rule the fill comes close."""
    cli = _tools()
    zone = ('  (zone (net 3) (net_name "/b/S") (layer "F.Cu") (uuid "z1") (name "z1")\n'
            '    (hatch edge 0.5) (connect_pads (clearance 0.2)) (min_thickness 0.25)\n'
            '    (fill yes (thermal_gap 0.5) (thermal_bridge_width 0.5))\n'
            '    (polygon (pts (xy 1 1) (xy 59 1) (xy 59 29) (xy 1 29))))\n')
    body = _track("/a/S", 15, 15, 45, 15) + zone
    c = cons()
    gaps = {}
    for name, cc in (("with", c), ("without", {})):
        pcb = _board(tmp_path, body, cc, name=name)
        kc.run_drc(cli, pcb, refill=True, save_board=True)
        bg = geom.BoardGeom.from_file(pcb)
        fills = [f for z in bg.zones_of(None, "F.Cu") for f in z.fills.get("F.Cu", [])]
        assert fills
        t = bg.tracks_of("/a/S", "F.Cu")[0].poly
        gaps[name] = min(f.distance(t) for f in fills)
    assert gaps["with"] == pytest.approx(3.0, abs=0.02)
    assert gaps["without"] < 1.0
    pcb = tmp_path / "with.kicad_pcb"
    assert _drc_clearance(cli, pcb) == []
    assert "iso_clearance" not in _kinds(_iso(pcb, c)[0])


def _route_board(tmp_path, c, name):
    """/a/S runs straight along y 10; /b/S has a pad 2 mm off that line, so a
    router that doesn't know the barrier runs past it inside the gap."""
    body = (_pad("J1", 5, 10, ["/a/S"]) + _pad("J2", 55, 10, ["/a/S"])
            + _pad("K1", 30, 12.5, ["/b/S"]) + _pad("K2", 30, 27, ["/b/S"]))
    return _board(tmp_path, body, c, name=name)


def _gap(pcb):
    bg = geom.BoardGeom.from_file(pcb)
    best = None
    for layer in bg.copper_layers:
        a = [t.poly for t in bg.tracks_of("/a/S", layer)] + [v.poly for v in bg.vias_of("/a/S", layer)]
        a += [p.poly for p in bg.pads_of("/a/S", layer)]
        b = [t.poly for t in bg.tracks_of("/b/S", layer)] + [v.poly for v in bg.vias_of("/b/S", layer)]
        b += [p.poly for p in bg.pads_of("/b/S", layer)]
        for x in a:
            for y in b:
                d = x.distance(y)
                best = d if best is None else min(best, d)
    return best


@pytest.mark.parametrize("barrier", [True, False], ids=["with", "without"])
def test_live_route_auto_keeps_out_of_the_gap(tmp_path, barrier, capsys):
    if env.find_kicad_cli() is None or env.find_freerouting_jar() is None \
            or env.find_java() is None:
        pytest.skip("needs kicad-cli, java and Freerouting")
    import route_auto
    c = cons() if barrier else {}
    pcb = _route_board(tmp_path, c, "route")
    out = tmp_path / "ra.json"
    code = route_auto.main(["--pcb", str(pcb), "--out-report", str(out), "--timeout-s", "300",
                            "--no-krt-finish", "--no-maze-finish"])
    rep = json.loads(out.read_text()) if out.is_file() else {}
    said = capsys.readouterr().out
    if code != 0 and any(k in said for k in ("wxEntryStart", "X Display", "importable session")):
        pytest.skip("route_swig couldn't start wx on this machine's Xvfb")
    assert code == 0, rep
    assert geom.BoardGeom.from_file(pcb).tracks_of("/a/S")
    if barrier:
        assert rep["facts"]["dsn_barriers"]
        assert _gap(pcb) >= 3.0 - 1e-3
        assert "iso_clearance" not in _kinds(_iso(pcb, c)[0])
        assert _drc_clearance(env.find_kicad_cli(), pcb) == []
    else:
        assert rep["facts"]["dsn_barriers"] == []
        assert _gap(pcb) < 3.0        # the planted fault: unaware, it runs through
