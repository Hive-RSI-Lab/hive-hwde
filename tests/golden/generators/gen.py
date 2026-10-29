"""Golden-board generator driver (venv python).

For a board <name> with design module design_<name>.py in this directory:
  1. sch_build   -> tests/golden/<name>/<name>.kicad_sch  (+ .kicad_pro)
  2. pcb_build   -> tests/golden/<name>/<name>.kicad_pcb  (bundled python, unfilled)
  3. kicad-cli pcb drc --refill-zones --save-board   (fills + persists zones)
  4. kicad-cli sch erc / pcb drc [--schematic-parity]  -> violation report

Prints a JSON summary; exit 0 = generated and clean, 1 = violations remain,
2 = toolchain/generation error.

Usage: python gen.py --board blinky2 [--parity] [--json report.json]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
GOLDEN = HERE.parent
REPO = GOLDEN.parents[1]
sys.path.insert(0, str(REPO / ".claude" / "skills" / "hwde" / "scripts" / "lib"))
sys.path.insert(0, str(HERE))

import env  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def run_kicad(cli: Path, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(cli), *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300,
    )


def parse_violations(report: Path, kind: str) -> list[dict]:
    """Flatten kicad-cli ERC/DRC json to [{type, severity, msg, pos}]."""
    data = json.loads(report.read_text(encoding="utf-8"))
    out = []
    if kind == "erc":
        groups = [(s.get("uuid_path", "/"), v)
                  for s in data.get("sheets", []) for v in s.get("violations", [])]
    else:
        groups = [("", v) for v in
                  data.get("violations", []) + data.get("unconnected_items", [])
                  + data.get("schematic_parity", [])]
    for _, v in groups:
        item = v.get("items", [{}])[0]
        pos = item.get("pos", {})
        out.append({
            "type": v.get("type"),
            "severity": v.get("severity"),
            "msg": v.get("description"),
            "pos": (pos.get("x"), pos.get("y")),
            "what": item.get("description"),
        })
    return out


def parse_netlist(path: Path) -> dict:
    """kicadsexpr netlist -> {"REF.PAD": netname}. Tiny s-expr walk, good
    enough for the (net (code..) (name "X") (node (ref U1) (pin 5)..)) shape."""
    import re
    text = path.read_text(encoding="utf-8")
    out: dict[str, str] = {}
    nets = re.findall(
        r'\(net\s+\(code\s+"[^"]*"\)\s+\(name\s+"([^"]*)"\)(.*?)(?=\(net\s|\Z)',
        text, re.S)
    for name, body in nets:
        for ref, pin in re.findall(
                r'\(node\s+\(ref\s+"([^"]*)"\)\s+\(pin\s+"([^"]*)"\)', body):
            out[f"{ref}.{pin}"] = name
    if not out:
        raise RuntimeError(f"netlist parse produced no nodes: {path}")
    return out


def erc(cli: Path, sch: Path) -> list[dict]:
    rep = sch.parent / "_erc.json"
    run_kicad(cli, ["sch", "erc", "--format", "json", "--severity-all",
                    "-o", str(rep), str(sch)])
    v = parse_violations(rep, "erc")
    rep.unlink(missing_ok=True)
    return v


def drc(cli: Path, pcb: Path, refill: bool = False, parity: bool = False) -> list[dict]:
    rep = pcb.parent / "_drc.json"
    args = ["pcb", "drc", "--format", "json", "--severity-all"]
    if refill:
        args += ["--refill-zones", "--save-board"]
    if parity:
        args += ["--schematic-parity"]
    args += ["-o", str(rep), str(pcb)]
    cp = run_kicad(cli, args)
    if not rep.exists():
        raise RuntimeError(f"drc produced no report: {cp.stdout} {cp.stderr}")
    v = parse_violations(rep, "drc")
    rep.unlink(missing_ok=True)
    return v


def generate(board: str, parity: bool, sch_only: bool = False,
             pcb_only: bool = False) -> dict:
    cli = env.find_kicad_cli()
    if cli is None:
        raise RuntimeError("kicad-cli not found")
    kipy = env.find_kicad_python(cli)
    design_path = HERE / f"design_{board}.py"
    if not design_path.exists():
        raise RuntimeError(f"no design module: {design_path}")
    out_dir = GOLDEN / board

    import importlib.util
    spec = importlib.util.spec_from_file_location(design_path.stem, design_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    design = mod.DESIGN
    assert design["name"] == board, "design name != board arg"

    import sch_build
    if not pcb_only:
        sch_build.build_schematic(design, out_dir)

    # net-per-pad map from the REAL netlist, so the board matches the
    # schematic exactly (incl. the unconnected-(REF-Pin-PadN) nets KiCad
    # assigns to no-connect pins). Requirement for --schematic-parity = 0.
    sch_path = out_dir / f"{board}.kicad_sch"
    netmap_path = out_dir / "_netmap.json"
    netlist_path = out_dir / "_netlist.net"
    cp = run_kicad(cli, ["sch", "export", "netlist", "--format", "kicadsexpr",
                         "-o", str(netlist_path), str(sch_path)])
    if not netlist_path.exists():
        raise RuntimeError(f"netlist export failed: {cp.stdout} {cp.stderr}")
    netmap_path.write_text(
        json.dumps(parse_netlist(netlist_path)), encoding="utf-8")
    netlist_path.unlink()

    pcb_path = out_dir / f"{board}.kicad_pcb"
    if not sch_only:
        cp = subprocess.run(
            [str(kipy), str(HERE / "pcb_build.py"),
             "--design", str(design_path), "--out", str(pcb_path),
             "--netmap", str(netmap_path)],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=300,
        )
        if cp.returncode != 0:
            raise RuntimeError(
                f"pcb_build failed rc={cp.returncode}: {cp.stdout} {cp.stderr}")
        pcb_notes = json.loads(cp.stdout.strip().splitlines()[-1]).get("notes", [])
    else:
        pcb_notes = []

    # AFTER pcb_build: board.Save() writes a default .kicad_pro next to the
    # board, clobbering ours - ours must win (hermetic severity overrides).
    sch_build.write_project(design, out_dir)

    result = {"script": "gen", "board": board, "notes": pcb_notes}
    if not sch_only:
        result["drc"] = drc(cli, pcb_path, refill=True, parity=parity)
    if not pcb_only:
        result["erc"] = erc(cli, out_dir / f"{board}.kicad_sch")
    n = len(result.get("erc", [])) + len(result.get("drc", []))
    result["violations"] = n
    result["status"] = "pass" if n == 0 else "violations"
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--board", required=True)
    ap.add_argument("--parity", action="store_true",
                    help="include schematic-parity in DRC")
    ap.add_argument("--sch-only", action="store_true")
    ap.add_argument("--pcb-only", action="store_true")
    ap.add_argument("--json", help="also write summary to this path")
    args = ap.parse_args()
    try:
        result = generate(args.board, args.parity, args.sch_only, args.pcb_only)
    except Exception as exc:
        print(json.dumps({"script": "gen", "status": "error", "error": str(exc)}))
        return 2
    text = json.dumps(result, indent=1)
    print(text)
    if args.json:
        Path(args.json).write_text(text, encoding="utf-8")
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
