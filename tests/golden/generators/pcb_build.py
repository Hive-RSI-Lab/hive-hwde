"""Board builder for golden boards. RUNS UNDER KiCad's BUNDLED python.exe
(the only interpreter with the SWIG pcbnew module) - launched by gen.py.

Consumes the same design module as sch_build.py and writes an UNFILLED
.kicad_pcb. Zones are filled afterwards by the driver via
`kicad-cli pcb drc --refill-zones --save-board`: pcbnew.ZONE_FILLER
segfaults headless on KiCad 10.0.3 Windows (see LEARNINGS [swig]).

Design-module pcb contract (all coordinates mm, board absolute):
  DESIGN["layers"]         2 or 4
  DESIGN["outline"]        (x1, y1, x2, y2) Edge.Cuts rectangle
  DESIGN["components"][i]["pcb"]   (x, y, rot_deg)
  DESIGN["components"][i]["side"]  "top" | "bottom" (default top)
  DESIGN["components"][i]["pins"]  {pad_number: net_name | "NC"}
  DESIGN["pcb"]["tracks"]  [{net, layer, width, pts: [(x,y), ...]}, ...]
  DESIGN["pcb"]["vias"]    [{net, at: (x,y), size, drill}, ...]
  DESIGN["pcb"]["zones"]   [{net, layer, poly: [(x,y),...] | rect: (x1,y1,x2,y2),
                             priority?, min_thickness?, clearance?}, ...]
  DESIGN["pcb"]["silk"]    [{text, at: (x,y), layer: "F.SilkS"|"B.SilkS", size?}, ...]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pcbnew  # noqa: E402  (bundled python only)

# bundled python lives at <kicad root>/bin/python.exe
FP_ROOT = Path(sys.executable).parents[1] / "share" / "kicad" / "footprints"
if not FP_ROOT.is_dir():
    raise RuntimeError(f"footprint root not found: {FP_ROOT}")

LAYER = {
    "F.Cu": pcbnew.F_Cu, "B.Cu": pcbnew.B_Cu,
    "In1.Cu": pcbnew.In1_Cu, "In2.Cu": pcbnew.In2_Cu,
    "F.SilkS": pcbnew.F_SilkS, "B.SilkS": pcbnew.B_SilkS,
}


def mm(x: float, y: float) -> "pcbnew.VECTOR2I":
    return pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y))


def load_design(path: Path) -> dict:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.DESIGN


def build(design: dict, out: Path, netmap: dict | None = None) -> dict:
    notes = []
    board = pcbnew.CreateEmptyBoard()
    ds = board.GetDesignSettings()
    ds.SetCopperLayerCount(design["layers"])
    # DRC minimums compatible with JLCPCB 2-4 layer capabilities.
    ds.m_TrackMinWidth = pcbnew.FromMM(0.127)
    ds.m_ViasMinSize = pcbnew.FromMM(0.4)
    ds.m_MinThroughDrill = pcbnew.FromMM(0.2)
    ds.m_MinClearance = pcbnew.FromMM(0.127)

    # physical stackup for 4-layer boards (S3 reads heights/epsilon from file)
    if design["layers"] == 4:
        try:
            stackup = ds.GetStackupDescriptor()
            stackup.BuildDefaultStackupList(ds, design["layers"])
            ds.m_HasStackup = True
        except Exception as exc:  # SWIG coverage varies; not fatal
            notes.append(f"stackup skipped: {exc}")

    # ---- nets --------------------------------------------------------
    # Design modules use bare names ("OSC_IN"); the netlist names local-label
    # nets "/OSC_IN". With a netmap present, translate design names to their
    # netlist form so the board matches the schematic exactly.
    alias: dict[str, str] = {}
    if netmap:
        netnames = set(netmap.values())
        alias = {n[1:]: n for n in netnames if n.startswith("/")}

    nets: dict[str, pcbnew.NETINFO_ITEM] = {}

    def net_of(name: str) -> "pcbnew.NETINFO_ITEM":
        name = alias.get(name, name)
        if name not in nets:
            item = pcbnew.NETINFO_ITEM(board, name)
            board.Add(item)
            nets[name] = item
        return nets[name]

    # ---- components ---------------------------------------------------
    for comp in design["components"]:
        lib, fpname = comp["fp"].split(":")
        fp = pcbnew.FootprintLoad(str(FP_ROOT / f"{lib}.pretty"), fpname)
        if fp is None:
            raise RuntimeError(f"footprint not found: {comp['fp']}")
        try:
            fp.SetFPID(pcbnew.LIB_ID(lib, fpname))
        except Exception as exc:
            notes.append(f"SetFPID failed for {comp['ref']}: {exc}")
        fp.SetReference(comp["ref"])
        fp.SetValue(comp["value"])
        board.Add(fp)
        x, y, rot = comp["pcb"]
        if comp.get("side", "top") == "bottom":
            try:
                fp.Flip(mm(x, y), pcbnew.FLIP_DIRECTION_LEFTRIGHT)
            except AttributeError:
                fp.Flip(mm(x, y), True)
        fp.SetPosition(mm(x, y))
        fp.SetOrientationDegrees(rot)
        for pad in fp.Pads():
            num = pad.GetNumber()
            if netmap is not None:
                want = netmap.get(f"{comp['ref']}.{num}")
            else:
                want = comp["pins"].get(num)
                if want == "NC":
                    want = None
            if want:
                pad.SetNet(net_of(want))
        if "ref_at" in comp:  # relative to footprint anchor, silk hygiene
            rx, ry = comp["ref_at"]
            fp.Reference().SetPosition(mm(x + rx, y + ry))

    # ---- tracks --------------------------------------------------------
    for tr in design["pcb"].get("tracks", []):
        pts = tr["pts"]
        for a, b in zip(pts, pts[1:]):
            seg = pcbnew.PCB_TRACK(board)
            seg.SetStart(mm(*a))
            seg.SetEnd(mm(*b))
            seg.SetWidth(pcbnew.FromMM(tr["width"]))
            seg.SetLayer(LAYER[tr["layer"]])
            seg.SetNet(net_of(tr["net"]))
            board.Add(seg)

    # ---- vias ----------------------------------------------------------
    for vd in design["pcb"].get("vias", []):
        v = pcbnew.PCB_VIA(board)
        v.SetPosition(mm(*vd["at"]))
        v.SetWidth(pcbnew.FromMM(vd.get("size", 0.6)))
        v.SetDrill(pcbnew.FromMM(vd.get("drill", 0.3)))
        v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
        v.SetNet(net_of(vd["net"]))
        board.Add(v)

    # ---- zones ----------------------------------------------------------
    for zd in design["pcb"].get("zones", []):
        zone = pcbnew.ZONE(board)
        zone.SetLayer(LAYER[zd["layer"]])
        zone.SetNet(net_of(zd["net"]))
        outline = zone.Outline()
        outline.NewOutline()
        pts = zd.get("poly")
        if pts is None:
            x1, y1, x2, y2 = zd["rect"]
            pts = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
        for px, py in pts:
            outline.Append(pcbnew.FromMM(px), pcbnew.FromMM(py))
        zone.SetMinThickness(pcbnew.FromMM(zd.get("min_thickness", 0.25)))
        if "clearance" in zd:
            zone.SetLocalClearance(pcbnew.FromMM(zd["clearance"]))
        zone.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL)
        zone.SetAssignedPriority(zd.get("priority", 0))
        board.Add(zone)

    # ---- silk text -------------------------------------------------------
    for sd in design["pcb"].get("silk", []):
        t = pcbnew.PCB_TEXT(board)
        t.SetText(sd["text"])
        t.SetPosition(mm(*sd["at"]))
        t.SetLayer(LAYER[sd.get("layer", "F.SilkS")])
        size = sd.get("size", 1.0)
        t.SetTextSize(pcbnew.VECTOR2I(pcbnew.FromMM(size), pcbnew.FromMM(size)))
        t.SetTextThickness(pcbnew.FromMM(0.15))
        board.Add(t)

    # ---- board outline ----------------------------------------------------
    x1, y1, x2, y2 = design["outline"]
    rect = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_RECT)
    rect.SetStart(mm(x1, y1))
    rect.SetEnd(mm(x2, y2))
    rect.SetLayer(pcbnew.Edge_Cuts)
    rect.SetWidth(pcbnew.FromMM(0.1))
    board.Add(rect)

    out.parent.mkdir(parents=True, exist_ok=True)
    if not board.Save(str(out)):
        raise RuntimeError(f"board.Save failed: {out}")
    return {
        "script": "pcb_build",
        "status": "pass",
        "out": str(out),
        "nets": len(nets),
        "notes": notes,
    }


def dump_pads(pcb: Path, out: Path) -> None:
    """Pad/edge coordinate dump for hand-authoring track coordinates."""
    board = pcbnew.LoadBoard(str(pcb))
    pads: dict = {}
    for fp in board.GetFootprints():
        ref = fp.GetReference()
        pads[ref] = {}
        for pad in fp.Pads():
            p = pad.GetPosition()
            pads[ref][pad.GetNumber()] = {
                "at": [pcbnew.ToMM(p.x), pcbnew.ToMM(p.y)],
                "net": pad.GetNetname(),
                "layer": "F.Cu" if pad.IsOnLayer(pcbnew.F_Cu) else "B.Cu",
                "size": [pcbnew.ToMM(pad.GetSizeX()), pcbnew.ToMM(pad.GetSizeY())],
            }
    out.write_text(json.dumps(pads, indent=1), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--design", required=True, help="path to design module")
    ap.add_argument("--out", required=True, help="output .kicad_pcb path")
    ap.add_argument("--netmap", help="json {REF.PAD: net} from the netlist; "
                    "when given, pad nets come from here (parity source)")
    ap.add_argument("--pads-out", help="also dump pad coordinates to this json")
    args = ap.parse_args()
    try:
        netmap = None
        if args.netmap:
            netmap = json.loads(Path(args.netmap).read_text(encoding="utf-8"))
        result = build(load_design(Path(args.design)), Path(args.out), netmap)
        if args.pads_out:
            dump_pads(Path(args.out), Path(args.pads_out))
    except Exception as exc:
        print(json.dumps({"script": "pcb_build", "status": "error",
                          "error": str(exc)}))
        return 2
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
