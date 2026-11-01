"""schimport.py - a non-KiCad schematic into .kicad_sch, headless, checked.

kicad-cli has no schematic importer (`sch upgrade` reads KiCad files only),
and eeschema's File > Import > Non-KiCad Schematic is GUI-only. So: our own
Xvfb, eeschema with a throwaway config (empty library tables so the first-run
wizard doesn't start, and a user.hotkeys that binds the import action to a
key), keys only (xdrive.py): hotkey, type the path into the file dialog,
Ctrl+S, Ctrl+Q. Then the result is read back through kicad-cli (netlist,
ERC) and compared with the source by connectivity: every source net as a set
of (reference, pad), against the same from KiCad's netlist.

Readers here: Eagle .sch. Other formats (Altium .SchDoc, EasyEDA, CADSTAR)
import the same way but are only counted, not compared.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

from . import edasrc, xdrive

HOTKEY = "Ctrl+F11"  # KiCad 10.0.6 did not take Ctrl+Shift+Alt+F11 from user.hotkeys
HOTKEY_X = "Control_L+F11"
_TABLES = {"sym-lib-table": "(sym_lib_table\n  (version 7)\n)\n",
           "fp-lib-table": "(fp_lib_table\n  (version 7)\n)\n",
           "design-block-lib-table": "(design_block_lib_table\n  (version 7)\n)\n"}
# the source formats eeschema's importer reads, by suffix (all of them need
# the GUI path; only Eagle has a reader below)
SCH_SUFFIX = {".sch": "eagle", ".schdoc": "altium", ".json": "easyeda",
              ".csa": "cadstar", ".asc": "ltspice", ".epro": "easyedapro",
              ".zip": "easyedapro", ".prjpcb": "altium"}


class ImportError_(RuntimeError):
    pass


def _eeschema(cli: Path) -> Path:
    for p in (cli.parent / "eeschema", cli.parent.parent / "bin" / "eeschema"):
        if p.exists():
            return p
    w = shutil.which("eeschema")
    if not w:
        raise ImportError_(f"no eeschema beside {cli}")
    return Path(w)


def _config(dirp: Path, version: str) -> dict:
    """A throwaway KICAD_CONFIG_HOME: no wizard, no update checks, our hotkey."""
    v = dirp / version
    v.mkdir(parents=True, exist_ok=True)
    for name, text in _TABLES.items():
        (v / name).write_text(text)
    (v / "user.hotkeys").write_text(
        f"eeschema.EditorControl.importNonKicadSchematic\t{HOTKEY}\n")
    # the first-run wizard starts unless the three tables exist and both of
    # its prompts are marked shown (KiCad 10.0.6)
    (v / "kicad_common.json").write_text(json.dumps({
        "meta": {"version": 6},
        "do_not_show_again": {"data_collection_prompt": True, "update_check_prompt": True,
                              "env_var_overwrite_warning": False,
                              "zone_fill_warning": False},
        "api": {"enable_server": False},
        "session": {"remember_open_files": False},
    }))
    return {"KICAD_CONFIG_HOME": str(dirp)}


def gui_import(source: Path, out: Path, cli: Path, timeout: float = 300) -> tuple[Path, list[str]]:
    """Run eeschema's importer on SOURCE; returns (the .kicad_sch, steps log)."""
    out.mkdir(parents=True, exist_ok=True)
    work = out / source.name
    if work.resolve() != source.resolve():
        shutil.copy2(source, work)
    ver = (subprocess.run([str(cli), "version"], capture_output=True, text=True).stdout
           .strip().split(".")[:2])
    steps: list[str] = []
    xproc, disp = xdrive.start_xvfb()
    cfg = Path(tempfile.mkdtemp(prefix="hwde-kicfg-"))
    ee = None
    try:
        envv = dict(__import__("os").environ, DISPLAY=disp, **_config(cfg, ".".join(ver)))
        ee = subprocess.Popen([str(_eeschema(cli))], cwd=out, env=envv,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        d = xdrive.Display(disp)

        def need(pred, what, t=120):
            w = d.wait_window(pred, t)
            if w is None:
                names = sorted({n for _, n in d.windows()})
                raise ImportError_(f"eeschema: no {what} window after {t}s (open: {names})")
            return w

        main = need(lambda n: "Schematic Editor" in n, "main")
        steps.append("eeschema up")
        time.sleep(2)
        # tool hotkeys (ours) only fire once the canvas has the keyboard;
        # a menu accelerator (Ctrl+O) works from the frame, and closing its
        # dialog hands focus back to the canvas
        dlg = None
        for _ in range(5):
            d.focus(main)
            d.key("Control_L+o")
            op = d.wait_window(lambda n: n == "Open Schematic", 8)
            if op:
                d.focus(op)
                d.key("Escape")
                d.wait_window(lambda n: n == "Open Schematic", 8, gone=True)
                time.sleep(0.5)
            d.key(HOTKEY_X)
            dlg = d.wait_window(lambda n: n == "Import Schematic", 8)
            if dlg:
                break
        if not dlg:
            raise ImportError_(f"the import hotkey ({HOTKEY}) opened no file dialog")
        d.focus(dlg)
        d.key("Control_L+l")
        d.type(str(work))
        time.sleep(0.5)
        d.key("Return")
        steps.append("file dialog: " + str(work))
        if d.wait_window(lambda n: n == "Import Schematic", 30, gone=True) is None:
            raise ImportError_("the file dialog didn't take the path")
        # the import runs on the GUI thread: the main title takes the source's
        # name when it's done. Dialogs it raises on the way are answered OK.
        stem = work.stem
        end = time.time() + timeout
        while time.time() < end:
            names = d.windows()
            if any(stem in n and "Schematic Editor" in n for _, n in names):
                break
            for w, n in names:
                if n not in ("", "Messages") and "Schematic Editor" not in n and "Xvfb" not in n \
                        and n.lower().startswith(("kicad", "import", "warning", "error", "confirm")):
                    steps.append(f"dialog {n!r}: Return")
                    d.focus(w)
                    d.key("Return")
            time.sleep(1)
        else:
            raise ImportError_(f"import didn't finish in {timeout}s")
        steps.append("imported")
        time.sleep(2)
        main = need(lambda n: stem in n and "Schematic Editor" in n, "main")
        d.focus(main)
        d.key("Control_L+s")
        sch = out / (stem + ".kicad_sch")
        end = time.time() + 60
        while time.time() < end and not sch.exists():
            time.sleep(0.5)
        if not sch.exists():
            got = sorted(p.name for p in out.glob("*.kicad_sch"))
            if len(got) == 1:
                sch = out / got[0]
            else:
                raise ImportError_(f"Ctrl+S wrote no .kicad_sch (have {got})")
        size = -1
        while sch.stat().st_size != size:  # wait for the write to finish
            size = sch.stat().st_size
            time.sleep(1)
        steps.append("saved " + sch.name)
        d.focus(main)
        d.key("Control_L+q")
        try:
            ee.wait(20)
        except subprocess.TimeoutExpired:
            pass
        d.close()
        return sch, steps
    finally:
        if ee and ee.poll() is None:
            ee.kill()
        xproc.kill()
        shutil.rmtree(cfg, ignore_errors=True)
        for lck in out.glob("~*.lck"):
            lck.unlink(missing_ok=True)


# ------------------------------------------------------- source and result

def eagle_sch_nets(path: Path) -> tuple[set[str], dict[str, set[tuple[str, str]]]]:
    """An Eagle .sch: (references of parts with a package, net -> {(ref, pad)})."""
    root = ET.parse(path).getroot()
    connects: dict[tuple, dict[tuple[str, str], list[str]]] = {}
    packaged: set[tuple] = set()
    for lib in root.iter("library"):
        lname = lib.get("name")
        for ds in lib.iter("deviceset"):
            for dev in ds.iter("device"):
                key = (lname, lib.get("urn"), ds.get("name"), dev.get("name"))
                if dev.get("package"):
                    packaged.add(key)
                connects[key] = {(c.get("gate"), c.get("pin")): c.get("pad").split()
                                 for c in dev.iter("connect")}
    parts, part_key = set(), {}
    for p in root.iter("part"):
        key = (p.get("library"), p.get("library_urn"), p.get("deviceset"), p.get("device"))
        if key not in connects:  # library_urn missing on one side
            key = next((k for k in connects if (k[0], k[2], k[3]) == (key[0], key[2], key[3])), key)
        part_key[p.get("name")] = key
        if key in packaged:
            parts.add(p.get("name"))
    nets: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for net in root.iter("net"):
        name = edasrc.eagle_overbar(net.get("name"))
        for pr in net.iter("pinref"):
            ref = pr.get("part")
            pads = connects.get(part_key.get(ref), {}).get((pr.get("gate"), pr.get("pin")))
            for pad in pads or ():
                nets[name].add((ref, pad))
    return parts, dict(nets)


def kicad_netlist(cli: Path, sch: Path) -> tuple[set[str], dict[str, set[tuple[str, str]]]]:
    with tempfile.TemporaryDirectory() as td:
        xml = Path(td) / "n.xml"
        proc = subprocess.run([str(cli), "sch", "export", "netlist", "--format", "kicadxml",
                               "-o", str(xml), str(sch)], capture_output=True, text=True,
                              timeout=600)
        if not xml.exists():
            raise ImportError_("kicad-cli netlist export failed: " + proc.stderr.strip()[-300:])
        root = ET.parse(xml).getroot()
    refs = {c.get("ref") for c in root.iter("comp")}
    nets = {}
    for n in root.iter("net"):
        nets[n.get("name")] = {(nd.get("ref"), nd.get("pin")) for nd in n.iter("node")}
    return refs, nets


def compare(src_parts, src_nets, refs, nets) -> list[dict]:
    """Losses, worst first. Nets are matched by their pins, not their names
    (Eagle's N$12 has no meaning to keep); a name change is a note."""
    out = []
    missing = src_parts - refs
    if missing:
        out.append({"level": "lost", "what": "parts",
                    "text": f"{len(missing)} of {len(src_parts)} source parts aren't in "
                            f"KiCad's netlist: {edasrc._sample(missing)}"})
    extra = {r for r in refs - src_parts if not r.startswith("#")}
    if extra:
        out.append({"level": "check", "what": "parts",
                    "text": f"{len(extra)} parts in the import the source has no package "
                            f"for: {edasrc._sample(extra)}"})
    pin_net = {pin: name for name, pins in nets.items() for pin in pins}
    split, merged, renamed = [], Counter(), []
    for name, pins in src_nets.items():
        if len(pins) < 2:
            continue
        got = {pin_net.get(p) for p in pins}
        if len(got) > 1:
            split.append(f"{name} -> {sorted(g or '(none)' for g in got)[:4]}")
            continue
        kname = next(iter(got))
        if kname is None:
            split.append(f"{name} -> (no net)")
            continue
        merged[kname] += 1
        bare = kname.lstrip("/")
        if not name.startswith("N$") and bare != name and not bare.endswith("/" + name):
            renamed.append(f"{name} -> {kname}")
    if split:
        out.append({"level": "lost", "what": "nets",
                    "text": f"{len(split)} source nets are split (or lost pins) in the "
                            f"import: {edasrc._sample(split)}"})
    joined = [k for k, n in merged.items() if n > 1]
    if joined:
        out.append({"level": "lost", "what": "nets",
                    "text": f"{len(joined)} import nets join two or more source nets: "
                            f"{edasrc._sample(joined)}"})
    if renamed:
        out.append({"level": "check", "what": "nets",
                    "text": f"{len(renamed)} nets renamed (a name the source kept on the "
                            "net with no label drawn; the board import keeps it, so "
                            "schematic/board parity by name will differ until a label "
                            f"is added): {edasrc._sample(renamed)}"})
    return out


def erc_summary(cli: Path, sch: Path) -> dict:
    with tempfile.TemporaryDirectory() as td:
        rpt = Path(td) / "erc.json"
        subprocess.run([str(cli), "sch", "erc", "--format", "json", "-o", str(rpt), str(sch)],
                       capture_output=True, text=True, timeout=600)
        if not rpt.exists():
            return {"error": "kicad-cli sch erc wrote no report"}
        d = json.loads(rpt.read_text())
    c = Counter((v["severity"], v["type"]) for s in d.get("sheets", []) for v in s["violations"])
    return {"errors": sum(n for (s, _), n in c.items() if s == "error"),
            "warnings": sum(n for (s, _), n in c.items() if s == "warning"),
            "by_type": [[f"{s}:{t}", n] for (s, t), n in c.most_common()]}


def detect(path: Path) -> str | None:
    fmt = SCH_SUFFIX.get(path.suffix.lower())
    if fmt == "eagle":
        head = path.read_bytes()[:400]
        if b"<eagle" not in head:
            return "kicad-legacy" if head.startswith(b"EESchema") else None
    return fmt
