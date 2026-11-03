"""#477: routing on an HDI build's via spans (lib/hdilib, route_escape --hdi).

Pure tests need no toolchain. The live tests escape the balls hw-lab hdi-bga
carries (STM32F411 WLCSP-49 0.4 mm, STM32G431 UFBGA-64 0.5 mm, KLM8G1GETF
FBGA-153 0.5 mm, W9825G6KB TFBGA-54 0.8 mm) on fixture boards and grade them
apart from the router: each ball walked through the saved copper to a stub
past the field, check_vias, and kicad-cli DRC.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
import bgalib  # noqa: E402
import checklib  # noqa: E402
import board_init  # noqa: E402
import check_vias  # noqa: E402
import dfm_check  # noqa: E402
import env  # noqa: E402
import fab_export  # noqa: E402
import geom  # noqa: E402
import hdilib  # noqa: E402
import jlcstack  # noqa: E402
import kc  # noqa: E402
import route_edit  # noqa: E402
import route_escape  # noqa: E402
import route_finish as rf  # noqa: E402
import routelib  # noqa: E402
import rules_gen  # noqa: E402
from checklib import CheckError  # noqa: E402

STACKUPS = yaml.safe_load((REPO / "reference" / "stackups.yaml").read_text("utf-8"))["stackups"]
TEN = "JLCH101611Y2-1078L"
EIGHT = "JLCH081611N2-2116"
CU10 = [jlcstack.copper_name(i, 10) for i in range(1, 11)]


def grid(n, pitch, skip=lambda r, c: False, net=lambda r, c: None):
    return [(f"{chr(65 + r)}{c + 1}", (c - (n - 1) / 2) * pitch,
             (r - (n - 1) / 2) * pitch, net(r, c) or f"/S{r}_{c}")
            for r in range(n) for c in range(n) if not skip(r, c)]


def fbga153():
    return [tuple(b) for b in json.loads(
        (REPO / "tests/fixtures/hdi_fields/fbga153_balls.json").read_text("utf-8"))]


def make_board(tmp: Path, balls, pad, *, stackup=TEN, zones=(), rot=0, back=False,
               name="b") -> Path:
    s = STACKUPS[stackup]
    n = s["layers"]
    cu = [jlcstack.copper_name(i, n) for i in range(1, n + 1)]
    ids = {"F.Cu": 0, "B.Cu": 2}
    ids.update({f"In{k}.Cu": 2 + 2 * k for k in range(1, n - 1)})
    layers = "\n".join(f'\t\t({ids[c]} "{c}" signal)' for c in cu)
    side = "B" if back else "F"
    pads = "".join(
        f'(pad "{num}" smd circle (at {x:.4f} {y:.4f}) (size {pad} {pad}) '
        f'(layers "{side}.Cu" "{side}.Mask" "{side}.Paste") (net "{net}") '
        f'(uuid "00000000-0000-0000-0000-{i:012d}"))\n'
        for i, (num, x, y, net) in enumerate(balls))
    h = max(max(abs(b[1]), abs(b[2])) for b in balls) + 0.6
    z = "".join(
        f'(zone (net "{net}") (layer "{ly}") (uuid "00000000-0000-0000-0001-{i:012d}") '
        '(fill yes) (polygon (pts (xy 5 5) (xy 45 5) (xy 45 45) (xy 5 45))))\n'
        for i, (net, ly) in enumerate(zones))
    p = tmp / f"{name}.kicad_pcb"
    p.write_text(f'''(kicad_pcb (version 20260206) (generator "pcbnew") (general (thickness 1.6))
(layers
{layers}
(1 "F.Mask" user) (3 "B.Mask" user) (13 "F.Paste" user) (15 "B.Paste" user)
(5 "F.SilkS" user "F.Silkscreen") (7 "B.SilkS" user "B.Silkscreen") (31 "F.CrtYd" user "F.Courtyard")
(29 "B.CrtYd" user "B.Courtyard") (25 "Edge.Cuts" user))
(setup {board_init.build_stackup_block(s)})
(gr_rect (start 5 5) (end 45 45) (stroke (width 0.1)) (fill no) (layer "Edge.Cuts"))
(footprint "t:U1" (layer "{side}.Cu") (at 25 25 {rot})
(property "Reference" "U1" (at 0 0 0) (layer "F.SilkS"))
(fp_rect (start {-h} {-h}) (end {h} {h}) (stroke (width 0.05)) (fill no) (layer "{side}.CrtYd"))
{pads})
{z})
''', encoding="utf-8")
    p.with_suffix(".kicad_pro").write_text(json.dumps({
        "meta": {"filename": p.with_suffix(".kicad_pro").name, "version": 3},
        "board": {"design_settings": {"rules": {}}},
        "net_settings": {"classes": [{"name": "Default", "clearance": 0.2,
                                      "track_width": 0.2, "via_diameter": 0.45,
                                      "via_drill": 0.2}]}}), encoding="utf-8")
    return p


# ------------------------------------------------------------ spans

def test_spans_of_10_layer_build_at_jlc_sizes():
    spans = hdilib.spans_of(STACKUPS[TEN], CU10)
    assert [(s.kind, s.top, s.bot) for s in spans] == [
        ("micro", "F.Cu", "In1.Cu"), ("micro", "In1.Cu", "In2.Cu"),
        ("buried", "In2.Cu", "In7.Cu"), ("micro", "In7.Cu", "In8.Cu"),
        ("micro", "In8.Cu", "B.Cu")]
    micro = spans[0]
    assert (micro.drill, micro.size) == (0.1, 0.25)
    assert (spans[2].drill, spans[2].size) == (0.15, 0.3)


def test_laser_drill_grows_to_the_dielectric():
    """8-layer 2116: 0.112 mm prepreg, so 0.1 mm would be deeper than wide."""
    cu = [jlcstack.copper_name(i, 8) for i in range(1, 9)]
    spans = hdilib.spans_of(STACKUPS[EIGHT], cu)
    assert {s.drill for s in spans if s.kind == "micro"} == {0.115}
    assert not [s for s in spans if s.kind == "buried"]


def test_stack_to_goes_through_the_buried_via():
    spans = hdilib.spans_of(STACKUPS[TEN], CU10)
    names = lambda st: [(s.top, s.bot) for s in st]  # noqa: E731
    assert hdilib.stack_to(spans, 0, 0) == []
    assert names(hdilib.stack_to(spans, 0, 2)) == [("F.Cu", "In1.Cu"), ("In1.Cu", "In2.Cu")]
    # In3 is inside the buried via's span: it reaches it
    assert names(hdilib.stack_to(spans, 0, 3))[-1] == ("In2.Cu", "In7.Cu")
    assert len(hdilib.stack_to(spans, 0, 9)) == 5
    assert names(hdilib.stack_to(spans, 9, 7))[0] == ("In8.Cu", "B.Cu")


def test_stack_to_refuses_a_layer_the_build_cannot_reach():
    cu = [jlcstack.copper_name(i, 8) for i in range(1, 9)]
    spans = hdilib.spans_of(STACKUPS[EIGHT], cu)
    assert hdilib.stack_to(spans, 0, 2) is not None
    assert hdilib.stack_to(spans, 0, 3) is None    # no buried via on this build


def test_a_standard_build_is_never_hdi(tmp_path):
    p = make_board(tmp_path, grid(4, 0.5), 0.25, stackup="JLC10161H-2116")
    assert hdilib.board_build(geom.BoardGeom.from_file(p)) == (None, None)
    with pytest.raises(CheckError, match="not an HDI build"):
        route_escape.run(["--pcb", str(p), "--hdi", "on", "--plan"])


def test_a_standard_build_stays_on_krt(tmp_path):
    p = make_board(tmp_path, grid(6, 0.8), 0.35, stackup="JLC10161H-2116")
    payload, _ = route_escape.run(["--pcb", str(p), "--plan"])
    f = payload["facts"]["bgas"][0]
    assert "hdi" not in f and f["method"] != "hdi" and "hdi_build" not in f


def test_an_hdi_build_is_read_from_the_stack_block(tmp_path):
    p = make_board(tmp_path, grid(4, 0.5), 0.25)
    assert hdilib.board_build(geom.BoardGeom.from_file(p))[0] == TEN


# ------------------------------------------------------------ plan

def _plan(tmp_path, balls, pad, **kw):
    p = make_board(tmp_path, balls, pad, **kw)
    payload, _ = route_escape.run(["--pcb", str(p), "--plan"])
    return payload["facts"]["bgas"][0]


def test_seg_seg():
    assert hdilib.seg_seg((0, 0), (1, 0), (0.5, -1), (0.5, 1)) == 0.0
    assert hdilib.seg_seg((0, 0), (1, 0), (0, 2), (1, 2)) == pytest.approx(2)
    assert hdilib.seg_seg((0, 0), (1, 0), (2, 1), (2, 1)) == pytest.approx(math.sqrt(2))


def test_04mm_holds_one_ring_per_layer(tmp_path):
    """No 3 mil track fits between 0.25 mm via pads 0.4 mm apart."""
    f = _plan(tmp_path, grid(7, 0.4), 0.25)
    assert f["method"] == "hdi"
    assert f["hdi"]["per_layer"] == {"F.Cu": 24, "In1.Cu": 16, "In2.Cu": 8, "In3.Cu": 1}
    assert f["hdi"]["left"] == []
    assert f["hdi"]["vias"]["buried In2.Cu-In7.Cu"] == 1


def test_05mm_needs_only_the_first_microvia(tmp_path):
    f = _plan(tmp_path, grid(8, 0.5), 0.28)
    assert f["hdi"]["per_layer"] == {"F.Cu": 28, "In1.Cu": 36}
    assert list(f["hdi"]["vias"]) == ["micro F.Cu-In1.Cu"]


def test_real_emmc_field_has_a_full_plan(tmp_path):
    f = _plan(tmp_path, fbga153(), 0.25)
    assert sum(f["hdi"]["per_layer"].values()) == 153 and not f["hdi"]["left"]


def test_plane_balls_stack_down_to_their_plane(tmp_path):
    def net(r, c):
        return "GND" if (r + c) % 5 == 0 else None
    f = _plan(tmp_path, grid(8, 0.5, net=net), 0.28,
              zones=[("GND", "In3.Cu"), ("VDD", "In4.Cu"), ("VDD", "In5.Cu"),
                     ("GND", "In6.Cu")])
    gnd = sum(1 for b in grid(8, 0.5, net=net) if b[3] == "GND")
    assert f["hdi"]["plane_balls"] == gnd
    assert f["hdi"]["vias"]["buried In2.Cu-In7.Cu"] >= gnd
    assert f["plane_layers"] == ["In3.Cu", "In4.Cu", "In5.Cu", "In6.Cu"]
    assert not {"In3.Cu", "In4.Cu", "In5.Cu", "In6.Cu"} & set(f["hdi"]["per_layer"])


def test_8_layer_build_has_no_buried_via_and_says_so(tmp_path):
    """A 14 x 14 full field needs more layers than F, In1 and In2 reach."""
    f = _plan(tmp_path, grid(14, 0.4), 0.25, stackup=EIGHT)
    assert f["hdi"]["left"]
    assert set(f["hdi"]["per_layer"]) == {"F.Cu", "In1.Cu", "In2.Cu"}


def test_other_copper_in_the_way_is_respected():
    b = bgalib.Bga("U1", "F.Cu", 0.5, 3, 3, 0.25, (0, 0, 1, 1), 1, {"A": "N"})

    class P:
        number, net, center = "A", "N", (0.5, 0.5)

    spans = hdilib.spans_of(STACKUPS[TEN], CU10)
    wall = [hdilib.Obstacle((-5, y), (5, y), 0.05, "X") for y in (-0.5, 1.5)]
    wall += [hdilib.Obstacle((x, -5), (x, 5), 0.05, "X") for x in (-0.5, 1.5)]
    # F.Cu walled in: the ball takes In1
    f = hdilib.plan_field(b, [P()], (0, 0), 0, CU10, spans, ["F.Cu", "In1.Cu"],
                          track=0.0762, clearance=0.0762, hole_clearance=0.15,
                          existing={"F.Cu": wall})
    assert f.per_layer == {"In1.Cu": 1}
    assert [o["kind"] for o in f.ops if o["op"] == "add_via"] == ["micro"]


# ------------------------------------------------------------ rules

def test_via_type_rules_only_for_the_build_kinds():
    names = {r[0] for r in hdilib.via_type_rules(STACKUPS[TEN])}
    assert {"micro_hole", "buried_hole", "buried_hole_to_hole"} <= names
    assert not any(n.startswith("blind") for n in names)
    assert not any(n.startswith("buried") for n in
                   (r[0] for r in hdilib.via_type_rules(STACKUPS[EIGHT])))


def test_ensure_rules_is_idempotent_and_keeps_hv_rules_last():
    dru = '(version 1)\n(rule "aiee_hv_x"\n\t(constraint clearance (min 1.0000mm))\n)\n'
    rows = hdilib.via_type_rules(STACKUPS[TEN])
    text, changed = hdilib.ensure_rules(dru, rows)
    assert changed and text.index("aiee_hdi_micro_hole") < text.index("aiee_hv_x")
    assert hdilib.ensure_rules(text, rows) == (text, [])


def test_rules_gen_and_route_escape_write_the_same_field_rules(tmp_path):
    p = make_board(tmp_path, grid(8, 0.5), 0.28)
    bg = geom.BoardGeom.from_file(p)
    cap = {"min_trace_width_mm": 0.0889, "min_clearance_mm": 0.0889,
           "min_via_diameter_mm": 0.45, "min_via_drill_mm": 0.2}
    rules, _ = rules_gen.bga_rules(bg, cap, STACKUPS[TEN])
    names = [r.name for r in rules]
    assert "aiee_hdi_micro_hole" in names and "aiee_hdi_field_track_U1" in names
    text = "(version 1)\n" + "".join(r.render() + "\n" for r in rules)
    caps = hdilib.load_hdi_caps()
    rows = hdilib.via_type_rules(STACKUPS[TEN], caps) + hdilib.field_rules(
        "U1", caps["min_trace_width_mm"], caps["min_clearance_mm"],
        bgalib.rule_condition("U1"))
    assert hdilib.ensure_rules(text, rows)[1] == []
    # a standard build gets none of them
    plain, _ = rules_gen.bga_rules(bg, cap, STACKUPS["JLC10161H-2116"])
    assert not [r for r in plain if r.name.startswith("aiee_hdi_")]


def test_route_edit_validates_a_via_span():
    ok = {"op": "add_via", "at": [0, 0], "size": 0.25, "drill": 0.1, "net": "N",
          "kind": "micro", "top": "F.Cu", "bot": "In1.Cu"}
    assert route_edit.validate_ops({"version": 1, "ops": [ok]})
    with pytest.raises(CheckError, match="needs top and bot"):
        route_edit.validate_ops({"version": 1, "ops": [
            {k: v for k, v in ok.items() if k != "bot"}]})
    with pytest.raises(CheckError, match="unknown via kind"):
        route_edit.validate_ops({"version": 1, "ops": [{**ok, "kind": "laser"}]})


# ------------------------------------------------------------ live

def test_dfm_rules_take_the_hdi_line_floors():
    caps = dfm_check.load_capabilities(dfm_check.CAPABILITIES)
    row = caps["design_rules"]["10layer_1oz"]
    out = dfm_check.hdi_rules(row, caps["hdi"])
    assert out["min_trace_width_mm"] == 0.0762 and out["min_clearance_mm"] == 0.0762
    assert out["min_hole_diameter_mm"] == 0.075
    assert out["min_annular_ring_mm"] == 0.075
    assert "abs_min_hole_diameter_mm" not in out
    assert row["min_trace_width_mm"] > 0.0762      # the row itself is untouched


# ------------------------------------------------------------ DSN and SES

KICAD_DSN = """(pcb a.dsn
  (parser
    (string_quote ")
    (space_in_quoted_tokens on)
  )
  (resolution um 10)
  (unit um)
  (structure
    (layer F.Cu
      (type signal)
      (property
        (index 0)
      )
    )
    (layer In1.Cu
      (type signal)
      (property
        (index 1)
      )
    )
    (layer In2.Cu
      (type signal)
      (property
        (index 2)
      )
    )
    (layer B.Cu
      (type signal)
      (property
        (index 3)
      )
    )
    (via "Via[0-3]_450:200_um" "Via[0-1]_250:100_um")
    (rule
      (width 200)
      (clearance 125)
    )
  )
  (library
    (padstack "Via[0-3]_450:200_um"
      (shape (circle F.Cu 450))
      (shape (circle In1.Cu 450))
      (shape (circle In2.Cu 450))
      (shape (circle B.Cu 450))
      (attach off)
    )
    (padstack "Via[0-1]_250:100_um"
      (shape (circle F.Cu 250))
      (shape (circle In1.Cu 250))
      (attach off)
    )
  )
  (network
    (class kicad_default A
      (circuit
        (use_via "Via[0-3]_450:200_um")
      )
      (rule
        (width 200)
        (clearance 125)
      )
    )
  )
)
"""


def test_dsn_add_spans_declares_each_span_once():
    assert routelib.dsn_layer_names(KICAD_DSN) == ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]
    text, added = routelib.dsn_add_spans(
        KICAD_DSN, [(0, 1, 0.25, 0.1), (1, 2, 0.25, 0.1), (2, 3, 0.25, 0.1)])
    assert added == ["Via[1-2]_250:100_um", "Via[2-3]_250:100_um"]
    assert text.count('(padstack "Via[0-1]_250:100_um"') == 1
    block = text[text.index('(padstack "Via[1-2]_250:100_um"'):]
    block = block[:block.index("(attach off)")]
    assert "In1.Cu" in block and "In2.Cu" in block and "F.Cu" not in block
    via = next(ln for ln in text.splitlines() if ln.strip().startswith('(via "'))
    use = next(ln for ln in text.splitlines() if "(use_via" in ln)
    for line in (via, use):
        assert '"Via[0-3]_450:200_um"' in line       # the class's own stays
        assert '"Via[2-3]_250:100_um"' in line
        # Freerouting takes them in order: behind the through via it places none
        assert line.index('"Via[2-3]_250:100_um"') < line.index('"Via[0-3]_450:200_um"')
    assert routelib.dsn_add_spans(text, [(1, 2, 0.25, 0.1)])[1] == []


def test_hole_floor_leaves_a_laser_via_at_its_own_size():
    """Grown to the through-via 0.5 mm floor, a microvia would wall off the
    channels it escapes through."""
    text, _ = routelib.dsn_add_spans(KICAD_DSN, [(1, 2, 0.25, 0.1)])
    _, plain = routelib.dsn_hole_floor(text, 0.5)
    assert "Via[1-2]_250:100_um" in plain
    _, grown = routelib.dsn_hole_floor(text, 0.5, 0.24)
    assert "Via[0-3]_450:200_um" in grown
    assert grown.get("Via[1-2]_250:100_um", (0, 0))[1] <= 0.24 * 1000 + 100 - 125 + 1


def test_live_retype_vias_gives_an_imported_via_its_span(tmp_path):
    """KiCad's SES import calls a microvia from an outer layer "buried", which
    check_vias rejects; retype_vias gives it the span's type back."""
    cli = env.find_kicad_cli()
    if cli is None:
        pytest.skip("needs kicad-cli")
    bp = env.find_kicad_python(cli)
    pcb = make_board(tmp_path, grid(3, 1.0, net=lambda r, c: f"/X{r}{c}"), 0.5)
    via = ('(via buried (at 10 10) (size 0.25) (drill 0.1) '
           '(layers "F.Cu" "In1.Cu") (net "/X00") '
           '(uuid "00000000-0000-0000-0002-000000000001"))\n')
    off = ('(via buried (at 12 12) (size 0.25) (drill 0.1) '
           '(layers "In3.Cu" "In5.Cu") (net "/X01") '
           '(uuid "00000000-0000-0000-0002-000000000002"))\n')
    text = pcb.read_text("utf-8").rstrip()
    assert text.endswith(")")
    pcb.write_text(text[:-1] + via + off + ")\n", "utf-8")
    try:
        got = routelib.run_worker(bp, {
            "verb": "retype_vias", "board": str(pcb), "out": str(pcb),
            "spans": [["F.Cu", "In1.Cu", "micro"]]}, tmp_path)
    except checklib.CheckError as e:
        if "wxEntryStart" in str(e) or "X Display" in str(e):
            pytest.skip("route_swig couldn't start wx on this machine's Xvfb")
        raise
    assert got["retyped"] == 1 and got["off_span"] == 1
    kinds = {round(v.at[0]): v.kind for v in geom.BoardGeom.from_file(pcb).vias_of()}
    assert kinds == {10: "micro", 12: "buried"}


# ------------------------------------------------------------ the maze

def _maze_board(tmp_path, stackup):
    """A wall on F.Cu between two pads: the net must change layer."""
    p = make_board(tmp_path, grid(3, 1.0, net=lambda r, c: f"/X{r}{c}"), 0.5,
                   stackup=stackup)
    text = p.read_text("utf-8")
    add = ('(footprint "t:U2" (layer "F.Cu") (at 10 25) '
           '(property "Reference" "U2" (at 0 0 0) (layer "F.SilkS"))\n'
           '(pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu" "F.Mask") (net "SIG") '
           '(uuid "00000000-0000-0000-0003-000000000001")))\n'
           '(footprint "t:U3" (layer "F.Cu") (at 16 25) '
           '(property "Reference" "U3" (at 0 0 0) (layer "F.SilkS"))\n'
           '(pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu" "F.Mask") (net "SIG") '
           '(uuid "00000000-0000-0000-0003-000000000002")))\n'
           '(segment (start 13 5.5) (end 13 44.5) (width 0.3) (layer "F.Cu") (net "W") '
           '(uuid "00000000-0000-0000-0003-000000000003"))\n')
    p.write_text(text.replace('(footprint "t:U1"', add + '(footprint "t:U1"', 1), "utf-8")
    p.with_suffix(".kicad_dru").write_text('(version 1)\n', "utf-8")
    return p


def _maze_vias(pcb):
    bg = geom.BoardGeom.from_file(pcb)
    rules = rf.board_rules(pcb, bg, min_width=0.1)
    pair = {"net": "SIG", "a": {"pos": [10, 25], "msg": "Pad 1 [SIG] of U2 on F.Cu"},
            "b": {"pos": [16, 25], "msg": "Pad 1 [SIG] of U3 on F.Cu"}}
    jobs = rf.plan_routes(bg, rules, [pair], bg.copper_layers)
    assert jobs[0]["status"] == "routed", jobs[0]
    return rules, [op for op in jobs[0]["ops"] if op["op"] == "add_via"]


def test_maze_takes_microvias_on_an_hdi_build(tmp_path):
    _, vias = _maze_vias(_maze_board(tmp_path, TEN))
    assert len(vias) == 2
    for v in vias:
        assert (v["kind"], v["top"], v["bot"], v["drill"], v["size"]) == (
            "micro", "F.Cu", "In1.Cu", 0.1, 0.25)


def test_maze_stays_on_through_vias_on_a_standard_build(tmp_path):
    rules, vias = _maze_vias(_maze_board(tmp_path, "JLC10161H-2116"))
    assert rules.spans == [] and len(vias) == 2
    assert all("kind" not in v for v in vias)


def test_maze_sizes_its_through_via_from_through_vias_only(tmp_path):
    """A board full of microvias must not make 0.25/0.1 its through via."""
    p = _maze_board(tmp_path, TEN)
    text = p.read_text("utf-8").rstrip()[:-1]
    text += "".join(f'(via micro (at {7 + i} 8) (size 0.25) (drill 0.1) (layers "F.Cu" "In1.Cu") '
                    f'(net "M{i}"))\n' for i in range(4))
    text += '(via (at 7 42) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net "T"))\n)\n'
    p.write_text(text, "utf-8")
    rules = rf.board_rules(p, geom.BoardGeom.from_file(p), min_width=0.1)
    assert (rules.via_size, rules.via_drill) == (0.6, 0.3)


def _walk_to_stub(bg, pad, bbox) -> bool:
    """Independent of the planner: from the ball through the saved tracks and
    vias of its net, does copper reach past the ball field?"""
    near = lambda a, b: math.hypot(a[0] - b[0], a[1] - b[1]) < 1e-3  # noqa: E731
    layers = {pad.layers[0]}
    points = {(pad.center, ly) for ly in layers}
    tracks = bg.tracks_of(net=pad.net)
    vias = bg.vias_of(net=pad.net)
    grew = True
    while grew:
        grew = False
        for v in vias:
            if any(near(v.at, q) and ly in v.layers for q, ly in points):
                for ly in v.layers:
                    if (v.at, ly) not in points:
                        points.add((v.at, ly))
                        grew = True
        for t in tracks:
            c = list(t.shape.coords)
            for a, b in ((c[0], c[-1]), (c[-1], c[0])):
                if any(near(a, q) and ly == t.layer for q, ly in points) \
                        and (tuple(b), t.layer) not in points:
                    points.add((tuple(b), t.layer))
                    grew = True
    x0, y0, x1, y1 = bbox
    return any(not (x0 <= q[0] <= x1 and y0 <= q[1] <= y1) for q, _ in points)


def _grade(p: Path, *, stackup=TEN, expect_stubs=True):
    (p.parent / "c.json").write_text("{}", encoding="utf-8")
    assert rules_gen.main(["--layers", str(STACKUPS[stackup]["layers"]),
                           "--stackup", stackup, "--constraints", str(p.parent / "c.json"),
                           "--out-dru", str(p.with_suffix(".kicad_dru")),
                           "--pro", str(p.with_suffix(".kicad_pro")), "--pcb", str(p),
                           "--out", str(p.parent / "rg.json")]) == 0
    payload, _ = route_escape.run(["--pcb", str(p)])
    assert payload["facts"]["board_updated"] is True
    bg = geom.BoardGeom.from_file(p)
    (b,) = bgalib.find_bgas(bg)
    assert payload["facts"]["bgas"][0]["left_for_routers"] == []
    if expect_stubs:
        for pad in bg.pads_of(ref="U1"):
            if pad.net:
                assert _walk_to_stub(bg, pad, b.bbox), f"{pad.number} {pad.net}"
    assert check_vias.check(p)[0] == []
    report = kc.run_drc(env.find_kicad_cli(), p, all_track_errors=True)
    errors = [v for v in report["violations"] if v.get("severity") == "error"]
    assert errors == [], errors[:5]
    return payload["facts"]["bgas"][0], bg


def _need_tools():
    if env.find_kicad_cli() is None or env.find_krt() is None:
        pytest.skip("kicad-cli or KRT not installed")


CASES = {
    "wlcsp49_0.4": (lambda: grid(7, 0.4), 0.25),
    "ufbga64_0.5": (lambda: grid(8, 0.5), 0.28),
    "fbga153_0.5": (fbga153, 0.25),
    "tfbga54_0.8": (lambda: grid(9, 0.8, skip=lambda r, c: 3 <= c <= 5), 0.35),
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_live_every_ball_escaped(tmp_path, case):
    _need_tools()
    balls, pad = CASES[case]
    f, bg = _grade(make_board(tmp_path, balls(), pad))
    assert f["krt"]["failed"] == 0
    assert {v.kind for v in bg.vias_of()} <= {"micro", "buried"}


def test_live_escape_rotated_and_on_the_back(tmp_path):
    _need_tools()
    _grade(make_board(tmp_path, grid(7, 0.4), 0.25, rot=90, back=True))
    bg = geom.BoardGeom.from_file(tmp_path / "b.kicad_pcb")
    # the stack starts from the ball's side
    assert any(set(v.layers) == {"B.Cu", "In8.Cu"} for v in bg.vias_of())
    assert not any("F.Cu" in v.layers for v in bg.vias_of())


def test_live_plane_balls_join_through_the_stack(tmp_path):
    """GND and VDD balls are joined by nothing but the planes: 0 unconnected
    says each stack reaches its plane."""
    _need_tools()
    def net(r, c):
        if (r + c) % 5 == 0:
            return "GND"
        if (r + c) % 5 == 2 and 2 <= r <= 5:
            return "VDD"
        return None
    p = make_board(tmp_path, grid(8, 0.5, net=net), 0.28,
                   zones=[("GND", "In3.Cu"), ("VDD", "In4.Cu"),
                          ("VDD", "In5.Cu"), ("GND", "In6.Cu")])
    f, bg = _grade(p, expect_stubs=False)
    assert f["hdi"]["plane_balls"] > 0
    report = kc.run_drc(env.find_kicad_cli(), p, refill=True, all_track_errors=True)
    assert not [v for v in report["violations"] if v.get("source") == "unconnected"]


def test_live_fab_package_ships_a_drill_file_per_span_and_passes_dfm(tmp_path):
    """Through check_vias and DRC, then the exported files: one Excellon per
    span with the right hits, all of them in the zip, and dfm_check against
    the HDI line's floors."""
    _need_tools()
    import zipfile

    def net(r, c):
        return "GND" if (r + c) % 5 == 0 else None
    p = make_board(tmp_path, grid(8, 0.5, net=net), 0.28,
                   zones=[("GND", "In3.Cu"), ("GND", "In6.Cu")])
    _, bg = _grade(p, expect_stubs=False)
    out = tmp_path / "fab"
    assert fab_export.main(["--pcb", str(p), "--out-dir", str(out),
                            "--out", str(tmp_path / "fe.json")]) == 0
    drills = {f.name: f for f in (out / "gerbers").glob("*.drl")}
    want = {}
    for v in bg.vias_of():
        want[(v.layers[0], v.layers[-1])] = want.get((v.layers[0], v.layers[-1]), 0) + 1
    def word(ly):
        return {"F.Cu": "front", "B.Cu": "back"}.get(ly, ly.split(".")[0].lower())
    assert ("In2.Cu", "In7.Cu") in want
    for span, hits in want.items():
        text = drills[f"b-{word(span[0])}-{word(span[1])}.drl"].read_text("utf-8")
        assert sum(ln.startswith("X") for ln in text.splitlines()) == hits, span
    zipped = zipfile.ZipFile(next(out.glob("*.zip"))).namelist()
    assert set(drills) <= set(zipped)
    report = tmp_path / "dfm.json"
    dfm_check.main(["--pcb", str(p), "--fab-dir", str(out), "--offline",
                    "--out", str(report)])
    doc = json.loads(report.read_text("utf-8"))
    bad = [v for v in doc["violations"] if v["severity"] == "error"]
    assert bad == [], [(v["kind"], v["msg"]) for v in bad[:6]]


def test_a_microvia_gets_the_hdi_hole_spacing():
    """The maze spaces span vias at JLC's HDI figure; without this rule DRC held
    a microvia to the 0.5 mm through-via floor and rolled the route back (#477,
    hw-lab hdi-bga: 94 rollbacks)."""
    import hdilib
    caps = hdilib.load_hdi_caps()
    rules = hdilib.via_type_rules({"vias": [{"kind": "micro"}]}, caps)
    by_name = {r[0]: r for r in rules}
    assert by_name["micro_hole_to_hole"][1:3] == (
        "hole_to_hole", caps["hole_to_hole_diff_net_mm"])
    assert "B.Via_Type == 'Micro'" in by_name["micro_hole_to_hole"][3]
