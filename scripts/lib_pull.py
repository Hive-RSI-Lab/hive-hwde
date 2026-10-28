#!/usr/bin/env python
"""lib_pull.py - pull a part's KiCad symbol/footprint/3D via easyeda2kicad and
register it in the project library tables (SPEC.md P3, 6.1).

Wraps `easyeda2kicad --full --lcsc_id=Cxxxx --output <base>`, which writes
  <base>.kicad_sym            (symbol library)
  <base>.pretty/<fp>.kicad_mod (footprints, LEGACY (module ...) format)
  <base>.3dshapes/<name>.{wrl,step}
then appends the libraries to <project>/fp-lib-table + sym-lib-table (idempotent).
Footprints load in KiCad 10 despite the legacy format (LEARNINGS 2026-07-22
[easyeda2kicad]); --verify-load confirms it with `kicad-cli fp export svg`.

A raw pull is NOT fabricable as delivered, so two repairs run by default before
the pull is reported (both idempotent, both text surgery):
  fpfix           silk artifacts on pad copper, outlines under the silk-to-copper
                  bar, plated locating pegs (zero annular ring), legend text
                  hidden under the body      -> --no-autofix to skip
  lib_refdes_norm every reference text sits at a blanket (0,-4.0) mm whatever the
                  part size                  -> --no-refdes-norm to skip
Every footprint's (model ...) path is then rewritten from the absolute path
easyeda2kicad writes to ${KIPRJMOD}/../lib/<lib>.3dshapes/<name> (relative to
--project, else <out_dir>/../kicad), so the board survives its checkout moving.
`--verify-drc` measures the result the only way that counts: one instance of each
pulled footprint alone on a scratch board, real DRC (LEARNINGS 2026-07-28
[easyeda2kicad][drc] - geometry checkers and DRC disagree).

Per-part success is judged from the SYMBOL LIBRARY, the only ground truth
(LEARNINGS 2026-07-28 [easyeda2kicad][parts]): a part is present iff
aiee.kicad_sym holds a top-level (symbol ...) block carrying
(property "LCSC Part" "<id>") whose Footprint property resolves to a file in
aiee.pretty. Footprints are SHARED (one C0603 served 5 parts) and record only
the first puller's id, so footprint grep is NOT a per-part signal.

`--parts parts/parts.json` pulls every part in ONE paced batch: parts are
spaced --pace-s apart (auto: 0 for <=10 parts, 15 s for more - EasyEDA's
CloudFront WAF tripped at 4/35s after ~13 parts while 1/15s ran 31 clean), a
403/Forbidden failure backs off 90 s and retries once, and duplicate symbol
blocks (easyeda2kicad re-appends any name with a space or '/') are de-duped
after the batch.

Contract (SPEC section 6): argparse, JSON to stdout or --out, exit 0/1/2.
  0 = every requested part pulled (or already present)
  1 = one or more parts failed to pull
  2 = internal error / bad arguments

Examples:
  lib_pull.py --lcsc C1525
  lib_pull.py --parts parts/parts.json --project board/kicad --verify-load
  lib_pull.py --lcsc C8734 C25804 --out-dir board/lib --project board/kicad --verify-load
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
import fplib  # noqa: E402
import fpfix  # noqa: E402
import kc  # noqa: E402
import lib_refdes_norm  # noqa: E402
from lib import env  # noqa: E402


def _run_easyeda(lcsc: str, base: Path, no_3d: bool, overwrite: bool) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-m", "easyeda2kicad",
           f"--lcsc_id={lcsc}", "--output", str(base)]
    cmd += ["--symbol", "--footprint"] if no_3d else ["--full"]
    if overwrite:
        cmd.append("--overwrite")
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120)


def _symbol_entry(sym_lib: Path, lcsc: str) -> dict | None:
    """The symbol-index entry for this exact LCSC id, or None."""
    for entry in fplib.symbol_index(sym_lib):
        if entry["lcsc"] == lcsc:
            return entry
    return None


def _pull_one(lcsc: str, base: Path, no_3d: bool, overwrite: bool) -> dict:
    sym_lib = base.with_suffix(".kicad_sym")
    pretty = Path(str(base) + ".pretty")
    try:
        cp = _run_easyeda(lcsc, base, no_3d, overwrite)
    except subprocess.TimeoutExpired:
        return {"lcsc": lcsc, "status": "error", "detail": "easyeda2kicad timed out (network?)"}
    log = (cp.stdout or "") + (cp.stderr or "")

    already = "already exists" in log
    created = "Created Kicad footprint" in log or "Created Kicad symbol" in log
    # Success must be judged PER PART, from the filesystem, through the SYMBOL
    # library (LEARNINGS 2026-07-28 [easyeda2kicad][parts]): a part is present
    # iff aiee.kicad_sym holds a top-level (symbol ...) block with
    # (property "LCSC Part" "<id>") whose Footprint property resolves in
    # aiee.pretty. The previous footprint-grep gate failed BOTH ways: a
    # successful pull sharing an existing footprint (the file records only the
    # first puller's id) reported "error", and a 403'd symbol pull whose id
    # happened to sit in some footprint file reported "pulled" (and C2580
    # substring-matched C25804's file).
    entry = _symbol_entry(sym_lib, lcsc)
    fp_file = None
    if entry is not None and entry["footprint"]:
        cand = pretty / (entry["footprint"].split(":")[-1] + ".kicad_mod")
        fp_file = cand if cand.exists() else None
    if entry is None or fp_file is None:
        if entry is None:
            why = (f'no symbol carrying (property "LCSC Part" "{lcsc}") '
                   f"in {sym_lib.name}")
        else:
            why = (f"symbol '{entry['name']}' names footprint "
                   f"'{entry['footprint']}' but no such file exists in {pretty.name}")
        return {"lcsc": lcsc, "status": "error",
                "symbol_verified": entry is not None,
                "footprint_verified": False,
                "detail": why + (" | log: " + log.strip()[-300:] if log.strip()
                                 else f" (easyeda2kicad rc={cp.returncode})")}

    warnings = []
    fp_reports = []
    try:
        f = fplib.parse_footprint(fp_file)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"could not parse {fp_file.name}: {exc}")
    else:
        if not f.has_courtyard:
            warnings.append(f"{f.name}: no courtyard layer (easyeda2kicad "
                            "footprints sometimes omit it; DRC courtyard checks degrade)")
        fp_reports.append({
            "name": f.name,
            "file": str(fp_file),
            "copper_pads": len(f.copper_pads),
            "courtyard": f.has_courtyard,
            "silk": f.has_layer_kind("SilkS"),
        })
    return {
        "lcsc": lcsc,
        "status": "exists" if (already and not created) else "pulled",
        "symbol": entry["name"],
        "symbol_verified": True,
        "footprint_verified": True,
        "symbol_lib": str(sym_lib) if sym_lib.exists() else None,
        "footprint_lib": str(pretty) if pretty.is_dir() else None,
        "footprints": fp_reports,
        "warnings": warnings,
    }


# ------------------------------------------------------- batch pacing + dedupe

def _auto_pace(n_parts: int) -> float:
    """0 s for small pulls, 15 s otherwise (the measured WAF budget: 4/35s
    tripped CloudFront after ~13 parts; 1/15s ran 31 parts clean)."""
    return 0.0 if n_parts <= 10 else 15.0


def _is_rate_limited(detail: str) -> bool:
    return "403" in detail or "Forbidden" in detail


def _pull_all(ids: list[str], base: Path, no_3d: bool, overwrite: bool,
              pace_s: float) -> tuple[list[dict], int]:
    """Pull every id, paced; on a 403 back off 90 s and retry that part once.

    The retried part is re-verified on disk by _pull_one's symbol-index gate
    before it can report success. Returns (results, n_retried).
    """
    results: list[dict] = []
    retried = 0
    for i, lcsc in enumerate(ids):
        if i and pace_s > 0:
            time.sleep(pace_s)
        r = _pull_one(lcsc, base, no_3d, overwrite)
        if r["status"] == "error" and _is_rate_limited(r.get("detail", "")):
            time.sleep(90.0)      # CloudFront WAF block clears in ~60-120 s
            r = _pull_one(lcsc, base, no_3d, overwrite)
            r["retried"] = True
            retried += 1
        results.append(r)
    return results, retried


_SYM_NAME = re.compile(r'\(\s*symbol\s+"((?:[^"\\]|\\.)*)"')


def _dedupe_symbols(sym_lib: Path) -> dict:
    """Drop duplicate top-level (symbol "NAME") blocks, keeping the first.

    easyeda2kicad's dedup guard checks the RAW EasyEDA name but writes the
    sanitized one, so any part named with a space or '/' is APPENDED AGAIN on
    every re-pull (LEARNINGS 2026-07-28: an 18-symbol lib became 21). Paren
    scanner (fpfix.top_level_nodes), not a parse: only duplicate blocks are
    removed, nothing else is reformatted.
    """
    if not sym_lib.exists():
        return {"removed": 0}
    try:
        text = sym_lib.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        # never round-trip a lib we could not decode losslessly
        return {"removed": 0, "skipped": f"{type(exc).__name__}: {exc}"}
    seen: set[str] = set()
    drops: list[tuple[int, int]] = []
    names: list[str] = []
    for head, s, e in fpfix.top_level_nodes(text):
        if head != "symbol":
            continue
        m = _SYM_NAME.match(text[s:e])
        if not m:
            continue
        name = m.group(1)
        if name in seen:
            drops.append((s, e))
            names.append(name)
        else:
            seen.add(name)
    if drops:
        out = text
        for s, e in sorted(drops, key=lambda t: -t[0]):
            ls = out.rfind("\n", 0, s) + 1
            le = e
            if out[le:le + 1] == "\n":
                le += 1
            if out[ls:s].strip():
                ls, le = s, e
            out = out[:ls] + out[le:]
        sym_lib.write_text(out, encoding="utf-8")
    return {"removed": len(drops), "names": sorted(set(names))}


# ---------------------------------------------------------- lib-table registration

def _uri_for(target: Path, project: Path | None) -> str:
    """A KiCad lib-table URI: ${KIPRJMOD}-relative (portable) when possible.

    Uses os.path.relpath so a lib SIBLING of the project dir resolves to
    ${KIPRJMOD}/../lib/... (KiCad expands the .. fine). Falls back to an absolute
    path only across drives (relpath raises ValueError on win32).
    """
    import os
    t = target.resolve()
    if project is not None:
        try:
            rel = os.path.relpath(t, project.resolve())
            return "${KIPRJMOD}/" + Path(rel).as_posix()
        except ValueError:
            pass
    return t.as_posix()


def _register(table_path: Path, table_head: str, nickname: str, uri: str) -> str:
    """Append a lib entry to an fp-/sym-lib-table (create if absent, idempotent).

    Returns "added" | "present" | "created".
    """
    line = (f'  (lib (name "{nickname}")(type "KiCad")(uri "{uri}")'
            f'(options "")(descr ""))')
    if not table_path.exists():
        table_path.parent.mkdir(parents=True, exist_ok=True)
        table_path.write_text(f"({table_head}\n  (version 7)\n{line}\n)\n",
                              encoding="utf-8")
        return "created"
    text = table_path.read_text(encoding="utf-8", errors="replace")
    if f'(name "{nickname}")' in text:
        return "present"
    idx = text.rstrip().rfind(")")
    if idx < 0:  # malformed - rewrite fresh
        table_path.write_text(f"({table_head}\n  (version 7)\n{line}\n)\n",
                              encoding="utf-8")
        return "created"
    new = text[:idx].rstrip("\n") + "\n" + line + "\n" + text[idx:]
    table_path.write_text(new, encoding="utf-8")
    return "added"


def _register_project(project: Path, nickname: str, sym_lib: Path | None,
                      pretty: Path | None) -> dict:
    project.mkdir(parents=True, exist_ok=True)
    out: dict = {}
    if pretty is not None and pretty.is_dir():
        out["fp"] = _register(project / "fp-lib-table", "fp_lib_table",
                              nickname, _uri_for(pretty, project))
    if sym_lib is not None and sym_lib.exists():
        out["sym"] = _register(project / "sym-lib-table", "sym_lib_table",
                               nickname, _uri_for(sym_lib, project))
    return out


# ------------------------------------------------------------ post-pull hygiene

def _autofix(pretty: Path, names: list[str], dry_run: bool) -> dict:
    """Sanitise the footprints this pull produced (fpfix rules A-D).

    Every pull ships the same library-inherent defects - silk artifacts on pad
    copper, outlines under the silk-to-copper bar, plated locating pegs with no
    annular ring, legend text hidden under the body. They were repaired by hand
    on three shipped boards (boards/*/lib/EDITS.md) before this ran at pull time.
    """
    rep = fpfix.fix_lib(pretty, dry_run=dry_run, names=names)
    return {"footprints": rep["footprints"], "changed": rep["changed"],
            "actions": rep["actions"], "residue": rep["residue"],
            "results": [r for r in rep["results"] if r["changed"] or r["residue"]]}


def _refdes_norm(pretty: Path, dry_run: bool) -> dict:
    """Re-derive every refdes offset from the footprint's own geometry.

    easyeda2kicad parks EVERY reference at a blanket (0, -4.0) mm whatever the
    part size, so a populated board cannot be read (LEARNINGS 2026-07-29
    [parts][silk]). Must run AFTER the silk fix - it measures against the silk
    that survives - and BEFORE board_init, because once footprints are placed
    the offsets are copied into the .kicad_pcb.
    """
    rows = [lib_refdes_norm.normalize(f, 0.25, 1.0, dry_run)
            for f in sorted(Path(pretty).glob("*.kicad_mod"))]
    return {"footprints": len(rows),
            "changed": sum(1 for r in rows if r["status"] == "changed"),
            "unchanged": sum(1 for r in rows if r["status"] == "unchanged"),
            "skipped": sum(1 for r in rows if r["status"] == "skipped"),
            "results": [r for r in rows if r["status"] == "changed"]}


def _portable_models(pretty: Path, models: Path, project: Path | None,
                     explicit: bool = False) -> dict:
    """Point every footprint's (model ...) at ${KIPRJMOD}/.. instead of the
    absolute path easyeda2kicad writes (out_dir is resolved before the pull,
    LEARNINGS 2026-07-28). An absolute path names the checkout that ran the
    pull and dies with it - eleven boards' models pointed into removed track
    worktrees. The project dir is --project, else <out_dir>/../kicad, the
    workspace layout; with neither the paths stay absolute and `skipped` says
    so, and check_model_paths.py flags the board later. An explicit
    --project need not exist yet: _register_project creates it after this,
    and the rewrite only resolves paths."""
    if project is None or not (explicit or project.is_dir()):
        return {"rewritten": 0, "skipped": "no --project and no kicad/ dir "
                f"beside {pretty.parent} - model paths left absolute"}
    if not models.is_dir():
        return {"rewritten": 0, "skipped": f"no {models.name} (--no-3d?)"}
    proj = project.resolve()
    changed = {}
    for f in sorted(pretty.glob("*.kicad_mod")):
        text = f.read_text(encoding="utf-8")
        new, done = kc.model_paths_portable(text, proj, pretty.parent.parent,
                                            [models])
        if done:
            f.write_text(new, encoding="utf-8")
            changed[f.name] = sorted(set(done.values()))
    return {"rewritten": len(changed), "footprints": changed}


# ---------------------------------------------------------- optional load check

def _verify_load(pretty: Path) -> dict:
    """Confirm KiCad itself parses the pulled footprints (fp export svg)."""
    try:
        cli = env.find_kicad_cli()
    except env.EnvError as exc:
        return {"ok": False, "detail": str(exc)}
    if cli is None:
        return {"ok": False, "detail": "no kicad-cli found"}
    with tempfile.TemporaryDirectory() as td:
        outdir = Path(td) / "svg"
        outdir.mkdir(parents=True, exist_ok=True)  # dir must pre-exist (LEARNINGS)
        try:
            cp = subprocess.run([str(cli), "fp", "export", "svg", str(pretty),
                                 "--output", str(outdir)],
                                capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=120)
        except subprocess.TimeoutExpired:
            return {"ok": False, "detail": "kicad-cli fp export svg timed out"}
        svgs = list(outdir.glob("*.svg"))
        err = "Error creating svg" in (cp.stdout + cp.stderr)
        return {"ok": bool(svgs) and not err,
                "svgs": len(svgs),
                "detail": (cp.stdout + cp.stderr).strip()[-300:] if not svgs else "ok"}


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--lcsc", nargs="+", help="LCSC id(s), e.g. C1525")
    ap.add_argument("--parts", help="parts/parts.json - pull every parts[].lcsc "
                    "in one paced batch (alternative to --lcsc)")
    ap.add_argument("--pace-s", type=float, default=None,
                    help="seconds between parts (default auto: 0 for <=10 "
                         "parts, 15 for more - the EasyEDA WAF budget)")
    ap.add_argument("--out-dir", default=None,
                    help="library directory. Default: <project>/../lib when "
                         "--project is given, else ./lib")
    ap.add_argument("--lib-name", default="aiee", help="library nickname/base (default aiee)")
    ap.add_argument("--project", help="project dir to register the libs into")
    ap.add_argument("--no-3d", action="store_true", help="skip 3D models (faster)")
    ap.add_argument("--overwrite", action="store_true", help="re-pull existing parts")
    ap.add_argument("--verify-load", action="store_true",
                    help="confirm footprints load via kicad-cli fp export svg")
    ap.add_argument("--verify-drc", action="store_true",
                    help="DRC the pulled footprints alone on a scratch board")
    ap.add_argument("--no-autofix", action="store_true",
                    help="skip the fpfix silk/peg/legend sanitiser (NOT advised: "
                         "every pull ships those defects)")
    ap.add_argument("--no-refdes-norm", action="store_true",
                    help="skip the refdes-offset normalisation")
    ap.add_argument("--out", help="write JSON here instead of stdout")
    args = ap.parse_args(argv)
    if bool(args.lcsc) == bool(args.parts):
        ap.error("exactly one of --lcsc / --parts is required")

    try:
        # A bare relative default resolves against the CWD, and orchestrators run
        # from the repo root - so "lib" silently became <repo>/lib, shared by
        # every concurrent board run, while the board's own lib/ stayed empty
        # and the script still reported pass (LEARNINGS, lumina-strobe
        # BLOCKING-06). Derive from --project instead, and refuse the repo root.
        if args.out_dir:
            out_dir = Path(args.out_dir)
        elif args.project:
            out_dir = Path(args.project).resolve().parent / "lib"
        else:
            out_dir = Path("lib")
        try:
            if out_dir.resolve() == (env.repo_root() / "lib").resolve():
                raise RuntimeError(
                    f"refusing to use the repo-root library {out_dir.resolve()} - "
                    f"it is shared by every board run. Pass --out-dir "
                    f"boards/<board>/lib, or pass --project so the default "
                    f"derives from it.")
        except (OSError, ValueError):
            pass
        out_dir.mkdir(parents=True, exist_ok=True)
        # easyeda2kicad copies the --output string VERBATIM into every
        # footprint's (model ...) path, and KiCad resolves a relative one
        # against the PROJECT dir, where it does not exist - STEP export and 3D
        # render then silently lose every model (LEARNINGS 2026-07-28
        # [easyeda2kicad][parts]). Resolve before handing it over.
        out_dir = out_dir.resolve()
        base = out_dir / args.lib_name

        if args.parts:
            pdata = json.loads(Path(args.parts).read_text(encoding="utf-8"))
            ids: list[str] = []
            for p in pdata.get("parts", []):
                l = (p.get("lcsc") or "").strip()
                if l and l not in ids:
                    ids.append(l)
            if not ids:
                raise RuntimeError(f"{args.parts} has no parts[].lcsc ids")
        else:
            ids = list(args.lcsc)
        pace_s = _auto_pace(len(ids)) if args.pace_s is None else max(0.0, args.pace_s)
        results, n_retried = _pull_all(ids, base, args.no_3d, args.overwrite, pace_s)

        sym_lib = base.with_suffix(".kicad_sym")
        pretty = Path(str(base) + ".pretty")
        dedup = _dedupe_symbols(sym_lib)

        pulled_fps = sorted({fp["name"] for r in results
                             for fp in r.get("footprints", [])})
        autofix = refdes = None
        if pulled_fps and pretty.is_dir():
            if not args.no_autofix:
                autofix = _autofix(pretty, pulled_fps, dry_run=False)
            if not args.no_refdes_norm:
                refdes = _refdes_norm(pretty, dry_run=False)

        models = None
        if pretty.is_dir():
            project = (Path(args.project) if args.project
                       else out_dir.parent / "kicad")
            models = _portable_models(pretty, Path(str(base) + ".3dshapes"),
                                      project, explicit=bool(args.project))

        registered = None
        if args.project:
            registered = _register_project(
                Path(args.project), args.lib_name,
                sym_lib if sym_lib.exists() else None,
                pretty if pretty.is_dir() else None)

        load = _verify_load(pretty) if (args.verify_load and pretty.is_dir()) else None
        drc = None
        if args.verify_drc and pulled_fps and pretty.is_dir():
            d = fpfix.scratch_drc(pretty, pulled_fps)
            drc = {"status": d["status"], "counts": d["counts"],
                   "by_check": d["by_check"], "missing": d["missing"]}

        failed = [r for r in results if r["status"] == "error"]
        payload = {
            "script": "lib_pull",
            "status": "fail" if (failed or (drc and drc["counts"]["total"]))
                      else "pass",
            "lib_name": args.lib_name,
            "parts_file": args.parts,
            "paced_s": pace_s,
            "retried": n_retried,
            "symbol_lib": str(sym_lib) if sym_lib.exists() else None,
            "footprint_lib": str(pretty) if pretty.is_dir() else None,
            "results": results,
            "symbol_dedup": dedup,
            "registered": registered,
            "autofix": autofix,
            "refdes_norm": refdes,
            "model_paths": models,
            "load_check": load,
            "drc_check": drc,
        }
    except Exception as exc:  # noqa: BLE001 - contract: any error -> exit 2
        print(json.dumps({"script": "lib_pull", "status": "error",
                          "error": f"{type(exc).__name__}: {exc}"}))
        return 2

    text = json.dumps(payload, indent=1, ensure_ascii=True)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 1 if payload["status"] == "fail" else 0


if __name__ == "__main__":
    sys.exit(main())
