"""#469: IPC-D-356 netlist in the fab package, and the Gerber netlist compare.

Hermetic: the pd_trigger stage fixture's committed gerbers + its kicad-cli
IPC-D-356 (tests/fixtures/netlist/pd-trigger.ipc), planted faults written
into copies of the gerbers, and synthetic 4-layer stacks for blind vias,
slots and net ties. Slow: the pinned boards exported live (fab_export ships
the .ipc; dfm_check reads it back) give 0 shorts / 0 opens, and the gerber
connectivity agrees with netconn's wherever netconn joins.
"""
from __future__ import annotations

import re
import shutil
import sys
import warnings
import zipfile
from pathlib import Path

import pytest
from shapely.geometry import LineString, Point
from shapely.ops import nearest_points

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "lib"))

import dfm_check  # noqa: E402
import geom  # noqa: E402
import gerblib  # noqa: E402
import ipcd356  # noqa: E402

STAGES = REPO / "tests" / "fixtures" / "stages"
PD_PCB = STAGES / "pd_trigger" / "route" / "pd-trigger.kicad_pcb"
PD_GERBERS = STAGES / "pd_trigger" / "fab" / "gerbers"
PD_IPC = REPO / "tests" / "fixtures" / "netlist" / "pd-trigger.ipc"

warnings.simplefilter("ignore")


# ------------------------------------------------------------ helpers

def rec(code, net, ref, pin, x, y, access, hole=None, size=(0.6, 0.6)):
    """One IPC-D-356A fixed-column record, UNITS CUST 0 (0.0001 in)."""
    def n(v):
        return round(v / 0.00254)
    line = f"{code}{net:<14.14}   {ref:<6.6}{'-' if pin else ' '}{pin:<4.4}"
    line += "M" if ref == "VIA" else " "
    line += f"D{n(hole):04d}P" if hole else "      "
    line += (f"A{access:02d}X{n(x):+07d}Y{-n(y):+07d}"
             f"X{n(size[0]):04d}Y{n(size[1]):04d}R000S0")
    return line


def d356(*records) -> str:
    return "\n".join(["P  CODE 00", "P  UNITS CUST 0", *records, "999"]) + "\n"


def gx(v: float) -> int:
    return round(v * 1e6)


def gerber(*features: str) -> str:
    return "\n".join(["%FSLAX46Y46*%", "%MOMM*%", "%LPD*%", "G01*",
                      "%ADD10C,0.600000*%", "%ADD11C,0.200000*%",
                      *features, "M02*"]) + "\n"


def flash(x, y, d="D10"):
    return f"{d}*\nX{gx(x)}Y{gx(-y)}D03*"


def region(poly) -> str:
    pts = list(poly.exterior.coords)
    out = ["G36*", f"X{gx(pts[0][0])}Y{gx(-pts[0][1])}D02*"]
    out += [f"X{gx(x)}Y{gx(-y)}D01*" for x, y in pts[1:]]
    return "\n".join(out + ["G37*"])


def drill(holes, span=None, slots=()) -> str:
    fn = (f"; #@! TF.FileFunction,Plated,{span[0]},{span[1]},Blind\n"
          if span else "; #@! TF.FileFunction,Plated,1,4,PTH\n")
    body = ["T1"] + [f"X{x:.3f}Y{-y:.3f}" for x, y in holes]
    body += [f"X{x1:.3f}Y{-y1:.3f}G85X{x2:.3f}Y{-y2:.3f}" for (x1, y1), (x2, y2) in slots]
    return ("M48\n" + fn + "FMAT,2\nMETRIC\n"
            "; #@! TA.AperFunction,Plated,PTH,ViaDrill\nT1C0.300\n%\nG90\nG05\n"
            + "\n".join(body) + "\nM30\n")


def stack(tmp: Path, layers: dict, drills: dict) -> gerblib.FabStack:
    ext = {"F.Cu": "gtl", "In1.Cu": "g1", "In2.Cu": "g2", "B.Cu": "gbl"}
    tmp.mkdir(parents=True, exist_ok=True)
    for name, feats in layers.items():
        (tmp / f"s-{name.replace('.', '_')}.{ext[name]}").write_text(
            gerber(*feats))
    for name, text in drills.items():
        (tmp / name).write_text(text)
    return gerblib.open_fab(tmp)


ALL4 = ("F.Cu", "In1.Cu", "In2.Cu", "B.Cu")


def pd_copy(tmp: Path) -> Path:
    dst = tmp / "gerbers"
    shutil.copytree(PD_GERBERS, dst)
    return dst


def plant(gdir: Path, layer_file: str, feature: str) -> None:
    p = gdir / layer_file
    text = p.read_text()
    assert text.rstrip().endswith("M02*")
    p.write_text(text.rstrip()[:-4] + feature + "\nM02*\n")


_XY = re.compile(r"^(?:G0[123])?X(-?\d+)Y(-?\d+)(?:I-?\d+J-?\d+)?D0([123])\*$")


def pen_up(gdir: Path, layer_file: str, a, b) -> int:
    """Turn the drawn segment a->b (either way) into a move: the trace is
    gone from the gerber, everything else byte-identical."""
    p = gdir / layer_file
    want = {(gx(a[0]), gx(-a[1])), (gx(b[0]), gx(-b[1]))}
    cur, out, hits = None, [], 0
    for line in p.read_text().splitlines():
        m = _XY.match(line)
        if m:
            pt = (int(m.group(1)), int(m.group(2)))
            if m.group(3) == "1" and {cur, pt} == want:
                line = line[:-4] + "D02*"
                hits += 1
            cur = pt
        out.append(line)
    p.write_text("\n".join(out) + "\n")
    return hits


def pd_compare(gdir: Path) -> dict:
    bg = geom.load_board(PD_PCB)
    d = ipcd356.parse(PD_IPC.read_text())
    ipcd356.resolve(d, bg)
    return ipcd356.compare(gerblib.open_fab(gdir), d,
                           ipcd356.net_tie_classes(PD_PCB.read_text(), bg))


# ------------------------------------------------------------ parser

def test_parse_kicad_records():
    d = ipcd356.parse(PD_IPC.read_text())
    assert d.units == "CUST 0"
    codes = {p.code for p in d.points}
    assert {"317", "327"} <= codes
    via = next(p for p in d.points if p.ref == "VIA")
    assert via.pin == "" and via.access == 0 and via.plated
    # coordinates come back in board space (mm, Y down): every point sits on
    # a pad or via of the board
    bg = geom.load_board(PD_PCB)
    assert ipcd356.resolve(d, bg) == []
    assert all(p.board_net for p in d.points if p.net != "N/C"
               and p.code != "367")
    # the pin field is 4 characters: J1's "A1-B12" comes back "A1-B"
    assert any(p.ref == "J1" and p.pin == "A1-B" and p.board_net == "GND"
               for p in d.points)
    smd = next(p for p in d.points if p.code == "327" and p.ref == "D5")
    pad = next(q for q in bg.pads_of(ref=smd.ref) if q.number == smd.pin)
    assert abs(pad.center[0] - smd.x) < 0.003
    assert abs(pad.center[1] - smd.y) < 0.003


def test_parse_alias_units_and_unplated():
    text = ("P  CODE 00\nP  UNITS CUST 1\nP  NNAME1  /A_VERY_LONG_NET_NAME_X\n"
            "327NNAME1            U1    -1          A01X+010000Y-020000"
            "X0500Y0500R000S0\n"
            "367N/C              H1          D3200UA00X+001000Y-001000"
            "X3200Y0000R000S0\n999\n")
    d = ipcd356.parse(text)
    smd, hole = d.points
    assert smd.net == "/A_VERY_LONG_NET_NAME_X"
    assert (smd.x, smd.y) == pytest.approx((10.0, 20.0))
    assert hole.code == "367" and hole.plated is False
    assert hole.hole_mm == pytest.approx(3.2)
    with pytest.raises(ipcd356.D356Error):
        ipcd356.parse("P  UNITS CUST 0\n999\n")


def test_drill_span_reads_the_x2_header(tmp_path):
    p = tmp_path / "b-1-2.drl"
    p.write_text("M48\n; #@! TF.FileFunction,Plated,1,2,Blind\n")
    assert ipcd356.drill_span(p, 4) == (1, 2)
    p.write_text("M48\n; #@! TF.FileFunction,MixedPlating,1,4\n")
    assert ipcd356.drill_span(p, 4) == (1, 4)


# ------------------------------------------------------------ known answer

def test_pd_trigger_shipped_gerbers_match_the_netlist():
    res = pd_compare(PD_GERBERS)
    assert res["shorts"] == [] and res["opens"] == []
    assert res["n_points"] > 100 and res["n_plated_holes"] > 40
    assert set(res["fragments_per_net"].values()) == {1}


def test_planted_short_is_one_short(tmp_path):
    """A 0.1 mm copper sliver from D5-2 (/D5_A) to the nearest F.Cu pad of
    another net it can reach without touching a third net's copper."""
    bg = geom.load_board(PD_PCB)
    a = next(p for p in bg.pads_of(ref="D5") if p.number == "2")
    nets = {p.net for p in bg.pads_of() if p.net}
    sliver = b = None
    for cand in sorted((p for p in bg.pads_of(layer="F.Cu")
                        if p.net and p.net != a.net),
                       key=lambda p: p.poly.distance(a.poly)):
        line = LineString(nearest_points(a.poly, cand.poly)).buffer(0.05)
        if not any(bg.net_copper(n, "F.Cu").intersects(line)
                   for n in nets - {a.net, cand.net}):
            sliver, b = line, cand
            break
    gdir = pd_copy(tmp_path)
    plant(gdir, "pd-trigger-F_Cu.gtl", region(sliver))
    res = pd_compare(gdir)
    assert len(res["shorts"]) == 1, res["shorts"]
    assert res["shorts"][0]["nets"] == sorted({a.net, b.net})
    assert res["opens"] == []


def test_removed_track_is_one_open(tmp_path):
    """/D5_A is one F.Cu segment D5-2 -> R12-2; pen it up."""
    bg = geom.load_board(PD_PCB)
    (t,) = bg.tracks_of("/D5_A")
    a, b = t.shape.coords[0], t.shape.coords[-1]
    gdir = pd_copy(tmp_path)
    assert pen_up(gdir, "pd-trigger-F_Cu.gtl", a, b) == 1
    res = pd_compare(gdir)
    assert res["shorts"] == []
    assert [o["net"] for o in res["opens"]] == ["/D5_A"]
    assert sorted(map(sorted, res["opens"][0]["fragments"])) == [
        ["D5-2"], ["R12-2"]]


def test_stale_fill_reaching_a_pad_is_a_short(tmp_path):
    """The #243 shape: a pour region (G36) that still reaches a pad the
    board has since moved next to it - the gerber GND fill touches D5-2."""
    bg = geom.load_board(PD_PCB)
    fill = bg.zone_fill("GND", "F.Cu")
    pad = min((p for p in bg.pads_of(layer="F.Cu")
               if p.net not in (None, "GND") and p.kind == "smd"),
              key=lambda p: p.poly.distance(fill))
    pa, pb = nearest_points(pad.poly, fill)
    gdir = pd_copy(tmp_path)
    plant(gdir, "pd-trigger-F_Cu.gtl",
          region(LineString([pa, pb]).buffer(0.05)))
    res = pd_compare(gdir)
    assert [s["nets"] for s in res["shorts"]] == [sorted({"GND", pad.net})]


def test_missing_pad_copper_is_an_open(tmp_path):
    """A test point with no copper under it (a pad the export dropped)."""
    fab = stack(tmp_path, {"F.Cu": [flash(1, 1)], "B.Cu": []}, {})
    d = ipcd356.parse(d356(rec("327", "A", "U1", "1", 1, 1, 1),
                           rec("327", "A", "U1", "2", 5, 1, 1)))
    res = ipcd356.compare(fab, d)
    (op,) = res["opens"]
    assert op["no_copper"]["pin"] == "2"


# ------------------------------------------------------------ layers

def _blind_case(tmp, span):
    """Net A on F.Cu, net B on B.Cu, stacked at (2, 2) on all four layers;
    the one hole there joins only its span."""
    layers = {n: [flash(2, 2)] for n in ALL4}
    return stack(tmp, layers, {"s.drl": drill([(2, 2)], span=span)})


def test_through_hole_joins_every_layer(tmp_path):
    d = ipcd356.parse(d356(rec("327", "A", "U1", "1", 2, 2, 1),
                           rec("327", "B", "U2", "1", 2, 2, 4)))
    res = ipcd356.compare(_blind_case(tmp_path, None), d)
    assert [s["nets"] for s in res["shorts"]] == [["A", "B"]]


def test_blind_via_joins_only_its_span(tmp_path):
    d = ipcd356.parse(d356(rec("327", "A", "U1", "1", 2, 2, 1),
                           rec("327", "B", "U2", "1", 2, 2, 4)))
    res = ipcd356.compare(_blind_case(tmp_path, (1, 2)), d)
    assert res["shorts"] == [] and res["opens"] == []


def test_zero_length_slot_is_a_plated_hole(tmp_path):
    """KiCad writes an oval drill (pinned core's USB-C J1, 0.3998 x 0.4) as a
    G85 slot; read_drill keeps flashes only, so the compare reads slots too."""
    layers = {"F.Cu": [flash(3, 3)], "B.Cu": [flash(3, 3)]}
    fab = stack(tmp_path, layers,
                {"s.drl": drill([], slots=[((3, 3), (3, 3))])})
    assert len(gerblib.read_slots(fab.drill_files[0])) == 1
    d = ipcd356.parse(d356(rec("327", "A", "U1", "1", 3, 3, 1),
                           rec("327", "A", "U2", "1", 3, 3, 2)))
    res = ipcd356.compare(fab, d)
    assert res["opens"] == [] and res["n_plated_holes"] == 1


# ------------------------------------------------------------ board side

class _Pad:
    def __init__(self, ref, number, net, center=(0, 0)):
        self.ref, self.number, self.net, self.center = ref, number, net, center


class _BG:
    def __init__(self, pads, vias=()):
        self._pads, self._vias = pads, list(vias)

    def pads_of(self, net=None, layer=None, ref=None):
        return [p for p in self._pads if ref is None or p.ref == ref]

    def vias_of(self, net=None, layer=None):
        return self._vias


def test_net_tie_short_is_the_design(tmp_path):
    pcb = ('(kicad_pcb (footprint "NetTie-2"\n'
           '  (property "Reference" "NT1")\n'
           '  (net_tie_pad_groups "1, 2")\n'
           '  (pad "1" smd) (pad "2" smd)))\n')
    bg = _BG([_Pad("NT1", "1", "GND_A"), _Pad("NT1", "2", "GND_B")])
    ties = ipcd356.net_tie_classes(pcb, bg)
    assert ties["GND_A"] == ties["GND_B"]
    fab = stack(tmp_path, {"F.Cu": [flash(1, 1), flash(1.4, 1)], "B.Cu": []},
                {})
    d = ipcd356.parse(d356(rec("327", "GND_A", "NT1", "1", 1, 1, 1),
                           rec("327", "GND_B", "NT1", "2", 1.4, 1, 1)))
    assert ipcd356.compare(fab, d)["shorts"] != []
    assert ipcd356.compare(fab, d, ties)["shorts"] == []


def test_truncated_names_are_told_apart_and_reported():
    """kicad-cli keeps the last 14 characters: two unconnected-(...-PAD5)
    nets can share one D-356 name."""
    d = ipcd356.parse(d356(rec("327", "D-(U1-NC-PAD5)", "U1", "5", 1, 1, 1),
                           rec("327", "D-(U1-NC-PAD5)", "U9", "5", 9, 1, 1)))
    bg = _BG([_Pad("U1", "5", "unconnected-(U1-NC-PAD5)"),
              _Pad("U9", "5", "unconnected-(U9A-NC-PAD5)")])
    (clash,) = ipcd356.resolve(d, bg)
    assert len(clash["board_nets"]) == 2
    assert {p.key for p in d.points} == set(clash["board_nets"])


# ------------------------------------------------------------ dfm_check

def test_dfm_check_reports_the_netlist_family(tmp_path):
    gdir = pd_copy(tmp_path)
    shutil.copy(PD_IPC, gdir / "pd-trigger.ipc")
    rep = dfm_check.run(PD_PCB, fab_dir=gdir, polarity=False)
    assert rep["netlist"]["source"] == "package"
    assert rep["netlist"]["shorts"] == 0 and rep["netlist"]["opens"] == 0
    assert "netlist" in rep["coverage"]["passed"]

    bg = geom.load_board(PD_PCB)
    (t,) = bg.tracks_of("/D5_A")
    pen_up(gdir, "pd-trigger-F_Cu.gtl", t.shape.coords[0], t.shape.coords[-1])
    rep = dfm_check.run(PD_PCB, fab_dir=gdir, polarity=False)
    opens = [v for v in rep["violations"] if v["kind"] == "dfm_net_open"]
    assert [v["net"] for v in opens] == ["/D5_A"]
    assert opens[0]["severity"] == "error"
    assert "netlist" in rep["coverage"]["failed"]


def test_dfm_check_without_a_netlist_or_kicad_is_a_coverage_hole(
        tmp_path, monkeypatch):
    import kc
    monkeypatch.setattr(kc, "resolve_cli",
                        lambda: (_ for _ in ()).throw(RuntimeError("no cli")))
    rep = dfm_check.run(PD_PCB, fab_dir=pd_copy(tmp_path), polarity=False,
                        skip=("zone_fill",))
    assert "netlist" in rep["coverage"]["skipped_error"]


def test_order_submit_names_the_zipped_netlist(tmp_path):
    import order_submit
    z = tmp_path / "b_gerbers.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("b-F_Cu.gtl", "")
        zf.writestr("b.ipc", "")
    assert order_submit._zip_netlist(z) == "b.ipc"
    assert order_submit._zip_netlist(tmp_path / "none.zip") is None


# ------------------------------------------------------------ live (slow)

def _netconn_pad_components(bg, net) -> int:
    import netconn
    g = netconn.build(bg, net, include_zones=True)
    seen, n = set(), 0
    for p in (nd for nd in g.adj if nd[0] == "pad"):
        if p in seen:
            continue
        n += 1
        stack_, seen_ = [p], seen
        seen_.add(p)
        while stack_:
            for v, _w, _e in g.adj[stack_.pop()]:
                if v not in seen_:
                    seen_.add(v)
                    stack_.append(v)
    return n


@pytest.mark.parametrize("board", ["lite-main", "core"])
def test_pinned_board_ships_a_netlist_its_gerbers_match(board, tmp_path):
    """Known answer: the pinned boards are DRC-clean (0 unconnected), so the
    shipped copper must give 0 shorts / 0 opens against the shipped .ipc.
    netconn cross-check: it joins on true contacts only (no pad-to-fill,
    no T into a track body), so it may split a net the copper joins but
    never join what the copper splits: gerber fragments <= netconn's."""
    import fab_export
    # on a copy: kicad-cli leaves a .kicad_prl beside the board, and any
    # file in pinned/ moves bin/score.py's digest
    shutil.copytree(REPO / "pinned" / board / "kicad", tmp_path / "kicad")
    pcb = tmp_path / "kicad" / f"{board}.kicad_pcb"
    man = fab_export.run(pcb, tmp_path / "fab")
    ipc = Path(man["ipc_netlist"])
    assert ipc.is_file() and ipc.parent == Path(man["gerber_dir"])
    with zipfile.ZipFile(man["gerber_zip"]) as zf:
        assert ipc.name in zf.namelist()

    bg = geom.load_board(pcb)
    d = ipcd356.parse(ipc.read_text())
    assert ipcd356.resolve(d, bg) == []
    res = ipcd356.compare(gerblib.open_fab(man["gerber_dir"]), d,
                          ipcd356.net_tie_classes(pcb.read_text(), bg))
    assert res["shorts"] == [] and res["opens"] == []
    for net, frags in res["fragments_per_net"].items():
        assert frags <= _netconn_pad_components(bg, net), net

    rep = dfm_check.run(pcb, fab_dir=tmp_path / "fab", polarity=False,
                        skip=("zone_fill", "placement"))
    assert rep["netlist"]["source"] == "package"
    assert not [v for v in rep["violations"]
                if v["kind"] in ("dfm_net_short", "dfm_net_open")]


def test_layers_named_by_the_board_are_found_by_protel_extension(tmp_path):
    """An Altium import keeps its layer names, so kicad-cli writes
    'b-Top Solder.gts', 'b-Bottom Overlay.gbo': mask/silk/paste by extension."""
    for n in ("b-Top Layer.gtl", "b-Bottom Layer.gbl", "b-Top Solder.gts",
              "b-Bottom Solder.gbs", "b-Top Overlay.gto", "b-Bottom Overlay.gbo",
              "b-Top Paste.gtp", "b-Edge_Cuts.gm1"):
        (tmp_path / n).write_text("G04 empty*\nM02*\n")
    fab = gerblib.FabStack(tmp_path)
    assert set(fab.copper_files) == {"F.Cu", "B.Cu"}
    assert set(fab.mask_files) == {"F", "B"} and set(fab.silk_files) == {"F", "B"}
    assert set(fab.paste_files) == {"F"} and fab.edge_file is not None


def test_ipc_netlist_of_a_board_with_an_aux_origin_is_absolute(tmp_path):
    """kicad-cli writes IPC-D-356 from the aux origin; the gerbers are
    absolute. fab_export's netlist must land where the gerbers' copper is."""
    import fab_export
    import kc
    try:
        cli = kc.resolve_cli()
    except Exception:  # noqa: BLE001
        pytest.skip("no kicad-cli")
    moved = tmp_path / "pd-trigger.kicad_pcb"
    text = PD_PCB.read_text(encoding="utf-8")
    text = re.sub(r"\(aux_axis_origin [-\d.]+ [-\d.]+\)\s*", "", text)
    moved.write_text(text.replace("(setup\n", "(setup\n\t\t(aux_axis_origin 50 80)\n", 1),
                     encoding="utf-8")
    assert fab_export.aux_origin(moved) == (50.0, 80.0)
    plain = tmp_path / "plain.ipc"
    kc.export_ipcd356(cli, PD_PCB, plain)
    fixed = tmp_path / "fixed.ipc"
    assert fab_export.export_ipc_absolute(cli, moved, fixed)["status"] == "pass"

    def coords(p):
        return sorted(ln[: 80] for ln in p.read_text().splitlines() if ln[:3] in ("317", "327"))
    assert coords(fixed) == coords(plain)
    shifted = tmp_path / "shifted.ipc"
    kc.export_ipcd356(cli, moved, shifted)
    assert coords(shifted) != coords(plain)  # what kicad-cli alone writes
