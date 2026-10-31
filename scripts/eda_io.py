#!/usr/bin/env python
"""eda_io.py - boards in and out of other EDA tools, with what's lost said.

hwde designs in KiCad; the person's boards, a contractor's, a reference
design or a fab's tools are often Altium, Allegro/OrCAD, Eagle/Fusion or
EasyEDA. This brings a board in through KiCad's own importers and hands a
board out in the formats those tools read, and in both directions writes
down, in numbers, what didn't make it (losses.md beside the files).

  import SOURCE [--out DIR] [--format F]
      kicad-cli pcb import, then the source file is read on its own
      (lib/edasrc.py) and compared with the imported board: copper per
      layer, parts by reference, nets by name, vias, the outline, layers
      KiCad skipped (which it says only on stderr: its JSON report keeps
      `warnings: []`). Writes DIR/<stem>.kicad_pcb, import.json, losses.md.
      Exit 0 nothing lost, 1 something lost (a copper layer, parts, the
      importer's own errors), 2 not imported at all (and why, with what to
      do instead: Allegro's binary .brd, EasyEDA Pro, OrCAD Capture).

  export BOARD.kicad_pcb --to TARGET[,TARGET...] [--out DIR] [--step]
      TARGET: altium allegro orcad pads eagle fusion easyeda neutral.
      Writes what that tool can read (the KiCad project, IPC-2581, ODB++,
      netlists in its format, STEP with --step), then reads the IPC-2581 and
      ODB++ back and checks every part, net and copper layer of the board is
      in them. losses.md says per target what doesn't carry and which steps
      weren't run here (no Altium, Allegro or Fusion on this machine).
      Exit 0 written and checked, 1 a check found the export short, 2 failed.

  formats
      The table of what goes in and out, and how far each was tested.

Everything is JSON on stdout with --json; the files are written either way.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import edasrc  # noqa: E402
from lib import env  # noqa: E402

_TIMEOUT = 600

# kicad-cli pcb import --format, by detected format
KICAD_FORMAT = {"altium": "altium", "eagle": "eagle", "fabmaster": "fabmaster",
                "pads": "pads", "cadstar": "cadstar", "pcad": "pcad",
                "easyeda": "auto"}

# What each import path never carries, whatever the counts say. Plain.
IMPORT_NEVER = {
    "*": [
        "Board only: kicad-cli has no schematic importer, so there's no "
        ".kicad_sch. ERC, schematic parity and the schematic-side checks can't "
        "run until the schematic comes in through the KiCad GUI (Eeschema: "
        "File > Import > Non-KiCad Schematic reads Altium, CADSTAR, Eagle, "
        "EasyEDA Std/Pro and LTspice) or is redrawn.",
        "Design rules: only what the importer maps onto KiCad's board setup "
        "and net classes comes in, and it isn't compared here. Derive the "
        "board's rules again (rules_gen) before trusting DRC.",
        "Footprints are embedded in the board, not linked to a library: "
        "lib_pull / fp_verify see them as unknown parts.",
    ],
    "altium": ["Altium's rooms, unions, component classes and its rule "
               "scopes (query language) have no KiCad equivalent beyond what "
               "the importer maps; polygon pours come in as zones and are "
               "refilled by KiCad's rules, not Altium's."],
    "eagle": ["Eagle's .dru design rules, its ULP-made items and "
              "autorouter settings don't carry; supply symbols' nets do."],
    "fabmaster": ["A Fabmaster extract carries geometry and nets, not "
                  "Allegro's constraint manager (spacing, physical, "
                  "electrical rules) or its padstack definitions as such."],
    "easyeda": ["EasyEDA's LCSC part numbers ride in the parts' fields only "
                "where the importer keeps them; check the BOM fields."],
    "pads": ["PADS design rules and the cluster/union data don't carry."],
}

TARGETS = ("altium", "allegro", "orcad", "pads", "eagle", "fusion", "easyeda", "neutral")

# Per target: what to hand over, and what doesn't carry. Each claim about
# the other tool is from its maker's docs, not run here: no Altium,
# Allegro, PADS, Fusion or EasyEDA runs on this machine.
EXPORT = {
    "altium": {
        "files": ["kicad", "ipc2581", "odb"],
        "how": "Altium Designer (21+): File > Import Wizard > KiCad Design Files, "
               "pointed at kicad/ (board and schematic together). Fallback: "
               "File > Import > ODB++ into CAMtastic, then Tools > Netlist "
               "Extract and File > Export to PCB, which rebuilds copper, pads "
               "and nets but no schematic and no rules.",
        "lost": ["KiCad's custom rules (.kicad_dru) don't carry; set Altium's "
                 "rules from the board's constraints.json by hand.",
                 "Zones come in as polygon pours and repour under Altium's "
                 "rules: check every pour's clearance and connection style.",
                 "Altium's importer may lag KiCad's file format: if it refuses "
                 "a KiCad 10 file, use the ODB++ fallback."],
    },
    "allegro": {
        "files": ["ipc2581", "odb", "netlist:allegro"],
        "how": "Allegro PCB Editor: File > Import > IPC 2581 (Allegro 17.2+) "
               "rebuilds the board's copper, padstacks, parts and nets. For a "
               "new layout from the schematic instead, File > Import > Logic "
               "with the Telesis netlist (allegro.net).",
        "lost": ["No schematic: KiCad writes nothing OrCAD Capture reads. The "
                 "netlist carries the connections only.",
                 "Allegro needs its own footprint (.psm/.dra) per package; the "
                 "netlist names KiCad's footprints, which Allegro must map to "
                 "its library by name.",
                 "Rules (Constraint Manager) don't carry: neither IPC-2581 nor "
                 "the netlist holds spacing or physical rules."],
    },
    "orcad": {
        "files": ["ipc2581", "odb", "netlist:allegro", "netlist:orcadpcb2"],
        "how": "OrCAD PCB Editor is Allegro: import the IPC-2581 the same way. "
               "The OrCAD PCB2 netlist is for older OrCAD Layout flows.",
        "lost": ["No schematic for OrCAD Capture: re-capture it, using the "
                 "netlist to check connectivity.",
                 "Footprints and rules as for Allegro."],
    },
    "pads": {
        "files": ["ipc2581", "odb", "netlist:pads"],
        "how": "PADS Layout: File > Import of the PADS netlist (pads.net) for "
               "a new layout; the ODB++ / IPC-2581 for the finished board in "
               "PADS' CAM and assembly tools.",
        "lost": ["No schematic for PADS Logic; PADS decals must exist by the "
                 "footprint names in the netlist.", "Rules don't carry."],
    },
    "eagle": {
        "files": ["ipc2581", "step"],
        "how": "No editable path: KiCad writes no Eagle format, and Eagle "
               "rebuilds no editable board from any format KiCad writes. What goes is "
               "manufacturing data (IPC-2581) and the 3D board (STEP).",
        "lost": ["The board can't be edited in Eagle: schematic, layout, "
                 "parts and rules all stay in KiCad. Say so to whoever asked "
                 "for Eagle; Fusion (below) is the same."],
    },
    "fusion": {
        "files": ["ipc2581", "step"],
        "how": "Fusion: upload the STEP as the board's 3D body for enclosure "
               "work; the electronics stay in KiCad. None of KiCad's writers "
               "gives Fusion Electronics an editable board.",
        "lost": ["The schematic and layout don't carry to Fusion Electronics.",
                 "STEP carries the parts' 3D models only where the board has "
                 "them (check_model_fit / render say which are missing)."],
    },
    "easyeda": {
        "files": ["kicad", "kicad-zip"],
        "how": "EasyEDA Pro: File > Import > KiCad, choosing kicad.zip "
               "(board and schematic). EasyEDA Std's KiCad import takes the "
               ".kicad_pcb and .kicad_sch one at a time.",
        "lost": ["EasyEDA's importer may lag KiCad's file format; if it "
                 "refuses a KiCad 10 file there is no fallback that keeps "
                 "the schematic.",
                 "3D models: EasyEDA uses its own; KiCad's paths don't carry.",
                 "Custom rules (.kicad_dru) don't carry."],
    },
    "neutral": {
        "files": ["ipc2581", "odb", "gencad", "ipcd356", "step"],
        "how": "Formats most tools and fabs read: IPC-2581C and ODB++ (whole "
               "board), GenCAD (test and assembly), IPC-D-356 (bare-board "
               "netlist test), STEP (mechanical).",
        "lost": ["None of these carries the schematic or the design rules."],
    },
}


# ----------------------------------------------------------------- running

def _cli() -> Path:
    cli = env.find_kicad_cli()
    if cli is None:
        raise SystemExit("eda_io: no kicad-cli (bin/hwde check)")
    return cli


def _run(cmd: list, timeout: int = _TIMEOUT) -> subprocess.CompletedProcess:
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True,
                          timeout=timeout, stdin=subprocess.DEVNULL)


def _write_losses(path: Path, title: str, head: list[str], losses: list[dict],
                  never: list[str]) -> None:
    lines = [f"# {title}", ""] + head + [""]
    label = {"lost": "Lost", "check": "Check", "note": "Note"}
    if losses:
        lines += ["## What the counts found", ""]
        lines += [f"- **{label[l['level']]}** ({l['what']}): {l['text']}" for l in losses]
        lines.append("")
    else:
        lines += ["## What the counts found", "", "Nothing: every count matched.", ""]
    if never:
        lines += ["## What this path never carries", ""] + [f"- {n}" for n in never] + [""]
    path.write_text("\n".join(lines), encoding="utf-8")


# ------------------------------------------------------------------ import

UNDEFINED = "UNDEFINED"


def _loads(pcb: Path) -> str | None:
    """None when kicad-cli loads the board, else what it said."""
    with tempfile.TemporaryDirectory() as td:
        proc = _run([_cli(), "pcb", "export", "stats", "-o", Path(td) / "s.json", pcb])
        if proc.returncode == 0 and (Path(td) / "s.json").exists():
            return None
        tail = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()
        return tail[-1] if tail else f"exit {proc.returncode}"


def refill_and_drc(pcb: Path) -> dict:
    """Fill the zones (the importers leave most unfilled, and the gates refuse
    a stale fill) and save, with KiCad's DRC at the imported rules."""
    with tempfile.TemporaryDirectory() as td:
        rep = Path(td) / "drc.json"
        proc = _run([_cli(), "pcb", "drc", "--refill-zones", "--save-board",
                     "--format", "json", "--severity-error", "--severity-warning",
                     "-o", rep, pcb])
        if not rep.exists():
            tail = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()
            return {"error": tail[-1] if tail else f"exit {proc.returncode}"}
        d = json.loads(rep.read_text())
    by = Counter(v.get("type", "?") for v in d.get("violations", []))
    return {"violations": sum(by.values()), "by_type": by.most_common(),
            "unconnected": len(d.get("unconnected_items", []))}


def repair_and_load(pcb: Path) -> dict:
    """Check the imported board loads; KiCad 10.0.6's Eagle importer writes
    texts on a layer called UNDEFINED, which kicad-cli then refuses to load
    ("One or more items were found on undefined layers"). Move them to a
    comments layer, said in the losses, and check again."""
    err = _loads(pcb)
    if err is None:
        return {}
    text = pcb.read_text(encoding="utf-8")
    tag = f'(layer "{UNDEFINED}")'
    if "undefined layers" not in err or tag not in text:
        return {"error": f"KiCad can't load the board its importer wrote: {err}"}
    to = "Cmts.User" if '"Cmts.User"' in text.split("(setup", 1)[0] else "Dwgs.User"
    moved = text.count(tag)
    pcb.write_text(text.replace(tag, f'(layer "{to}")'), encoding="utf-8")
    err = _loads(pcb)
    if err is not None:
        return {"error": f"KiCad can't load the board its importer wrote, even with "
                         f"the {moved} {UNDEFINED}-layer items moved: {err}"}
    return {"moved": moved, "to": to}


def do_import(source: Path, out: Path, fmt: str | None = None) -> tuple[int, dict]:
    out.mkdir(parents=True, exist_ok=True)
    detected = fmt or edasrc.detect(source)
    result: dict = {"source": str(source), "format": detected, "ok": False}
    if detected in edasrc.NOT_IMPORTABLE:
        result["error"] = edasrc.NOT_IMPORTABLE[detected]
        return 2, result
    if detected == "kicad":
        result["error"] = "already a KiCad board"
        return 2, result
    if detected not in KICAD_FORMAT:
        result["error"] = (f"not a board file eda_io knows ({detected}): it reads "
                           "Altium .PcbDoc, Eagle .brd, an Allegro/OrCAD Fabmaster "
                           "extract .txt, EasyEDA Std .json, PADS .asc, CADSTAR .cpa "
                           "and P-CAD .pcb")
        return 2, result
    pcb = out / (re.sub(r"[^\w.-]+", "_", source.stem) + ".kicad_pcb")
    rep = out / "kicad-import-report.json"
    proc = _run([_cli(), "pcb", "import", "--format", KICAD_FORMAT[detected],
                 "--report-format", "json", "--report-file", rep, "-o", pcb, source])
    log = (proc.stdout or "") + (proc.stderr or "")
    (out / "kicad-import.log").write_text(log, encoding="utf-8")
    if not pcb.exists() or "Successfully saved" not in log:
        result["error"] = "kicad-cli pcb import failed: " + (log.strip().splitlines() or ["no output"])[-1]
        return 2, result
    report = json.loads(rep.read_text()) if rep.exists() else {}
    repairs = repair_and_load(pcb)
    if repairs.get("error"):
        result["error"] = repairs["error"]
        return 2, result
    drc = refill_and_drc(pcb)
    try:
        src = edasrc.read_source(source, detected)
    except Exception as exc:  # a reader's failure mustn't hide the import
        src = None
        result["source_read_error"] = f"{type(exc).__name__}: {exc}"
    board = edasrc.read_board(pcb)
    losses = [l.to_json() for l in edasrc.compare(src, board, report, log)]
    if drc.get("error"):
        losses.append({"level": "check", "what": "zones",
                       "text": f"zones not refilled (the gates will refuse a stale "
                               f"fill): {drc['error']}"})
    elif drc.get("unconnected"):
        losses.append({"level": "check", "what": "drc",
                       "text": f"{drc['unconnected']} connections are unrouted in the "
                               "import: unrouted in the source too, or copper the "
                               "import dropped. Compare with the source's own DRC"})
    if drc.get("violations"):
        top = ", ".join(f"{k} {v}" for k, v in drc["by_type"][:6])
        losses.append({"level": "note", "what": "drc",
                       "text": f"KiCad DRC on the import, at the rules the importer set: "
                               f"{drc['violations']} violations ({top}). Derive the rules "
                               "(rules_gen) before reading much into them"})
    if repairs.get("moved"):
        losses.insert(0, {"level": "check", "what": "importer",
                          "text": f"{repairs['moved']} items KiCad's importer put on a layer "
                                  f"named {UNDEFINED!r} (Eagle texts, so far) were moved to "
                                  f"{repairs['to']}: as written, KiCad itself refuses to load "
                                  "the board ('items found on undefined layers')"})
    if src is None:
        losses.append({"level": "check", "what": "source",
                       "text": "the source file wasn't counted (no reader for "
                               f"{detected} here, or it failed): nothing below "
                               "is checked against the source"})
    lost = any(l["level"] == "lost" for l in losses)
    never = IMPORT_NEVER["*"] + IMPORT_NEVER.get(detected, [])
    result.update(ok=True, board=str(pcb), kicad_format=report.get("source_format"), drc=drc,
                  source_counts=src.to_json() if src else None,
                  board_counts=board.to_json(), losses=losses, never=never,
                  lost=lost)
    (out / "import.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    bc = board.to_json()
    head = [f"Source: `{source.name}` ({report.get('source_format') or detected}), "
            f"imported by KiCad {env.kicad_cli_version_line(_cli()) or ''}".rstrip(),
            "",
            f"Imported: {bc['parts']} parts ({bc['parts_bottom']} on the bottom), "
            f"{bc['nets']} nets, {len(bc['copper_layers'])} copper layers, "
            f"{bc['vias']} vias, {bc['zones']} zones."]
    if src:
        sc = src.to_json()
        head.append(f"Source: {sc['parts']} parts, {sc['nets']} nets, copper "
                    f"objects per layer {sc['copper']}.")
    head += ["", f"Next: `bin/hwde intake --source {out}` makes it an hwde workspace "
             "(board only, so it lands at P6/P8 with no schematic)."]
    head += ["", "**Verdict:** " + ("something was LOST; read below before using this board."
                                     if lost else "nothing lost by count; the checks and "
                                     "notes below still need a look.")]
    _write_losses(out / "losses.md", f"Import of {source.name}", head, losses, never)
    return (1 if lost else 0), result


# ------------------------------------------------------------------ export

def _ns(tag: str) -> str:
    return "{http://webstds.ipc.org/2581}" + tag


def check_ipc2581(xml: Path, board: edasrc.Board) -> list[dict]:
    """The board's parts, nets and copper layers, each found in the XML."""
    root = ET.parse(xml).getroot()
    refs = {c.get("refDes") for c in root.iter(_ns("Component"))}
    nets = {s.get("net") for s in root.iter(_ns("Set")) if s.get("net")}
    conductors = [l.get("name") for l in root.iter(_ns("Layer"))
                  if l.get("layerFunction") in ("CONDUCTOR", "PLANE", "SIGNAL", "MIXED")]
    out = []
    want = {r for r in board.refs if r}
    if want - refs:
        out.append({"level": "lost", "what": "ipc2581",
                    "text": f"{len(want - refs)} parts missing from the IPC-2581: "
                            f"{edasrc._sample(want - refs)}"})
    if board.nets - nets:
        out.append({"level": "lost", "what": "ipc2581",
                    "text": f"{len(board.nets - nets)} nets missing from the IPC-2581: "
                            f"{edasrc._sample(board.nets - nets)}"})
    if len(conductors) != len(board.copper_layers):
        out.append({"level": "lost", "what": "ipc2581",
                    "text": f"the IPC-2581 has {len(conductors)} copper layers; "
                            f"the board {len(board.copper_layers)}"})
    return out


def check_odb(zpath: Path, board: edasrc.Board) -> list[dict]:
    """Every part of the board in ODB++'s component layers."""
    refs = set()
    with zipfile.ZipFile(zpath) as z:
        for name in z.namelist():
            if re.search(r"layers/comp_\+_(top|bot)/components$", name):
                for line in z.read(name).decode("latin1").splitlines():
                    f = line.split()
                    if f[:1] == ["CMP"] and len(f) > 7:
                        refs.add(f[6])
    want = {r for r in board.refs if r}
    if want - refs:
        return [{"level": "lost", "what": "odb",
                 "text": f"{len(want - refs)} parts missing from the ODB++: "
                         f"{edasrc._sample(want - refs)}"}]
    return []


def _netlist_nets(path: Path, fmt: str) -> set[str] | None:
    text = path.read_text(encoding="latin1")
    if fmt == "allegro":
        sec = text.split("$NETS", 1)[-1].split("$END", 1)[0]
        return {m.group(1).strip("'") for m in re.finditer(r"^(\S+)\s*;", sec, re.M)}
    if fmt == "pads":
        return set(re.findall(r"^\*SIGNAL\*\s+(\S+)", text, re.M))
    return None


def do_export(pcb: Path, targets: list[str], out: Path, step: bool = False) -> tuple[int, dict]:
    out.mkdir(parents=True, exist_ok=True)
    cli = _cli()
    name = pcb.stem
    board = edasrc.read_board(pcb)
    schs = sorted(pcb.parent.glob("*.kicad_sch"))
    root_sch = pcb.with_suffix(".kicad_sch")
    want: list[str] = []
    for t in targets:
        for f in EXPORT[t]["files"]:
            if f == "step" and not step and t not in ("eagle", "fusion"):
                continue
            if f not in want:
                want.append(f)
    made: dict[str, str] = {}
    findings: list[dict] = []

    def fail(what: str, proc: subprocess.CompletedProcess) -> None:
        tail = ((proc.stderr or "") + (proc.stdout or "")).strip().splitlines()
        findings.append({"level": "lost", "what": what,
                         "text": f"kicad-cli failed: {tail[-1] if tail else proc.returncode}"})

    for f in want:
        if f == "kicad" or f == "kicad-zip":
            dest = out / "kicad"
            if not dest.exists():
                dest.mkdir()
                for p in [pcb, *schs, pcb.with_suffix(".kicad_pro"),
                          pcb.with_suffix(".kicad_dru"), pcb.parent / "fp-lib-table",
                          pcb.parent / "sym-lib-table"]:
                    if p.exists():
                        shutil.copy2(p, dest / p.name)
            made["kicad"] = str(dest)
            if f == "kicad-zip":
                zp = out / "kicad.zip"
                with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
                    for p in sorted(dest.iterdir()):
                        z.write(p, p.name)
                made["kicad-zip"] = str(zp)
        elif f == "ipc2581":
            x = out / f"{name}.ipc2581.xml"
            proc = _run([cli, "pcb", "export", "ipc2581", "-o", x, pcb])
            if proc.returncode or not x.exists():
                fail("ipc2581", proc)
            else:
                made[f] = str(x)
                findings += check_ipc2581(x, board)
        elif f == "odb":
            z = out / f"{name}-odb.zip"
            proc = _run([cli, "pcb", "export", "odb", "--compression", "zip", "-o", z, pcb])
            if proc.returncode or not z.exists():
                fail("odb", proc)
            else:
                made[f] = str(z)
                findings += check_odb(z, board)
        elif f in ("gencad", "ipcd356"):
            p = out / f"{name}.{'cad' if f == 'gencad' else 'ipc'}"
            proc = _run([cli, "pcb", "export", f, "-o", p, pcb])
            if proc.returncode or not p.exists():
                fail(f, proc)
            else:
                made[f] = str(p)
        elif f == "step":
            p = out / f"{name}.step"
            proc = _run([cli, "pcb", "export", "step", "--subst-models", "--force",
                         "-o", p, pcb])
            if proc.returncode or not p.exists():
                fail("step", proc)
            else:
                made[f] = str(p)
        elif f.startswith("netlist:"):
            fmt = f.split(":", 1)[1]
            if not root_sch.exists():
                findings.append({"level": "lost", "what": "netlist",
                                 "text": f"no {root_sch.name} beside the board: the "
                                         f"{fmt} netlist needs the schematic"})
                continue
            ext = {"allegro": "allegro.net", "orcadpcb2": "orcad.net", "pads": "pads.net"}[fmt]
            p = out / ext
            proc = _run([cli, "sch", "export", "netlist", "--format", fmt, "-o", p, root_sch])
            if proc.returncode or not p.exists():
                fail(f, proc)
                continue
            made[f] = str(p)
            if fmt == "allegro" and (out / "devices").is_dir():
                made["allegro-devices"] = str(out / "devices")
            nets = _netlist_nets(p, fmt)
            # single-pin nets have no copper to check against: compare the
            # board's nets (pads and tracks) with the netlist's
            if nets is not None:
                # Allegro's netlist upper-cases names; PADS' leaves out
                # single-pin (unconnected-*) nets
                have = {n.upper().lstrip("/") for n in nets}
                gone = {n for n in board.nets if n.upper().lstrip("/") not in have
                        and not n.startswith("unconnected-")}
                if gone:
                    findings.append({"level": "check", "what": "netlist",
                                     "text": f"{len(gone)} board nets aren't in the {fmt} "
                                             f"netlist (board and schematic out of step, or "
                                             f"renamed by the writer): {edasrc._sample(gone)}"})
    lost = any(x["level"] == "lost" for x in findings)
    result = {"board": str(pcb), "targets": targets, "files": made,
              "findings": findings, "lost": lost,
              "per_target": {t: {"how": EXPORT[t]["how"], "lost": EXPORT[t]["lost"]}
                             for t in targets}}
    (out / "export.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    head = [f"Board: `{pcb.name}`: {board.footprints} parts, {len(board.nets)} nets, "
            f"{len(board.copper_layers)} copper layers.", "",
            "Files: " + ", ".join(f"`{Path(v).name}`" for v in made.values()), "",
            "The IPC-2581 and ODB++ were read back here and every part, net and "
            "copper layer of the board looked for in them. The steps in the other "
            "tool below were NOT run here (none of them is on this machine): they "
            "are its maker's documented import paths.", ""]
    for t in targets:
        head += [f"### {t}", "", EXPORT[t]["how"], ""] + [f"- {x}" for x in EXPORT[t]["lost"]] + [""]
    _write_losses(out / "losses.md", f"Export of {pcb.name}", head, findings,
                  ["The design's reasoning (docs, constraints.json, ledger, checklist "
                   "sign-offs) stays in hwde's board folder; send the board's docs/ "
                   "alongside."])
    return (1 if lost else 0), result


# ----------------------------------------------------------------- formats

FORMATS = [
    # tool, in (path, tested here on), out (path)
    ("Altium Designer", "in: .PcbDoc via kicad-cli (tested: KiCad QA boards, 4 and 6 layers)",
     "out: KiCad project for its Import Wizard; ODB++ for CAMtastic (not run here)"),
    ("Allegro / OrCAD PCB Editor", "in: Fabmaster extract .txt (tested: KiCad QA, 2 and 4 layers); "
     "binary .brd: no", "out: IPC-2581 + Telesis netlist (not run here)"),
    ("OrCAD Capture", "in: no importer", "out: netlists only; no schematic"),
    ("Eagle / Fusion Electronics", "in: Eagle 6+ XML .brd (tested: KiCad QA boards)",
     "out: no editable path; IPC-2581 and STEP only"),
    ("EasyEDA Std", "in: .json (tested: KiCad QA, 4 layers)", "out: KiCad files (not run here)"),
    ("EasyEDA Pro", "in: not headless in KiCad 10.0.6 (crashes); export Altium from EasyEDA Pro",
     "out: kicad.zip for its KiCad import (not run here)"),
    ("PADS", "in: .asc (tested: KiCad QA)", "out: PADS netlist + ODB++/IPC-2581"),
    ("CADSTAR, P-CAD", "in: via kicad-cli, not counted against the source", "out: -"),
]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    pi = sub.add_parser("import")
    pi.add_argument("source", type=Path)
    pi.add_argument("--out", type=Path)
    pi.add_argument("--format", choices=sorted(set(KICAD_FORMAT)))
    pi.add_argument("--json", action="store_true")
    pe = sub.add_parser("export")
    pe.add_argument("board", type=Path)
    pe.add_argument("--to", required=True,
                    help="comma list of: " + " ".join(TARGETS))
    pe.add_argument("--out", type=Path)
    pe.add_argument("--step", action="store_true", help="also write STEP (slow on big boards)")
    pe.add_argument("--json", action="store_true")
    pf = sub.add_parser("formats")
    pf.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    if a.cmd == "formats":
        if a.json:
            print(json.dumps([dict(zip(("tool", "in", "out"), r)) for r in FORMATS], indent=2))
        else:
            for tool, i, o in FORMATS:
                print(f"{tool}\n  {i}\n  {o}")
        return 0
    if a.cmd == "import":
        src = a.source.resolve()
        out = (a.out or Path(src.stem + "-kicad")).resolve()
        try:
            rc, res = do_import(src, out, a.format)
        except edasrc.Unreadable as exc:
            rc, res = 2, {"source": str(src), "ok": False, "error": str(exc)}
    else:
        targets = [t.strip() for t in a.to.split(",") if t.strip()]
        bad = [t for t in targets if t not in TARGETS]
        if bad:
            ap.error(f"unknown target {bad}: one of {' '.join(TARGETS)}")
        pcb = a.board.resolve()
        out = (a.out or pcb.parent / "export").resolve()
        rc, res = do_export(pcb, targets, out, a.step)
    if a.json:
        print(json.dumps(res, indent=2))
    elif res.get("error"):
        print(f"eda_io: {res['error']}", file=sys.stderr)
    else:
        print((out / "losses.md").read_text(encoding="utf-8"))
    return rc


if __name__ == "__main__":
    sys.exit(main())
