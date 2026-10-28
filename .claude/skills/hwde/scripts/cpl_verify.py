#!/usr/bin/env python
"""cpl_verify.py - does each machine-placed part land with pin 1 on pad 1?

JLC places a part the way its LCSC footprint model is drawn in EasyEDA, turned
by the CPL rotation and put at the CPL position. A package table cannot know
that model (the '^SOT-23,180' row turned the owner's SOT-23-5/6 parts 180 deg),
so this check reads each `smt_placed` part's own model (lib/easyeda.py, cached
per LCSC number) and fits it onto the board footprint:

  rotation  The model's pads are matched to the board footprint's pads BY
            NUMBER. The correction c is the turn (a multiple of 90 deg, else
            45 deg) that puts every shared pad number on its board pad, after
            centring both pad sets (each model pad nearer its own board pad
            than any other). The CPL must say (board rotation + c) mod
            360; anything else is `wrong_rotation`, with the rotation it needs.
            A two-pad part that is not polarised may also sit at c + 180.
  polarity  Diodes, LEDs and polarised caps are also matched BY ROLE: the
            model's cathode / + pin (its symbol pin names) must land on the
            board's cathode / + pad (the pad's pinfunction, else the KiCad
            library convention: D_/LED_ pad 1 = K, CP_ pad 1 = +, else - for
            a footprint pulled from the same LCSC part - the model's own
            numbering). A role fit that disagrees with the number fit is
            `wrong_polarity`. With no K/A or +/- names on one side (LCSC
            symbols for polarised caps say "1"/"2"), polarity is checked by
            pad number, and a 180-degree turn is never accepted for it.
  no data   No LCSC number, no cached or fetchable model, fewer than two
            shared pad numbers, no turn that fits, or a bottom-side part
            (JLC's bottom convention is not modelled) is a FAILURE, never a
            pass - `no_model`, `no_fit` or `bottom_unverified`. A model
            fetch that errored (network, HTTP status) is `fetch_failed`,
            never `no_model`: it says nothing about the part or its pin 1.
            A 403/429 is EasyEDA's rate limit: later parts are not fetched
            (also `fetch_failed`) and the report's `rate_limited` says why.
  offset    The model's pad centre vs the board's, both placed, is reported
            (`offset_mm`); over OFFSET_WARN_MM it is a warning only.

`derive_rotation()` is what bom_cpl.py uses to WRITE the CPL; the package table
is only its fallback when this check has no model. `merge_visual()` puts the
fab step's image verdict (cpl_render.py + the playbook's vision agent, file
`cpl_visual.json`) beside the script verdict: a part the two disagree on, or
one the visual pass skipped, fails.

CLI:
  cpl_verify.py --pcb board.kicad_pcb --cpl fab/CPL.csv
                [--parts parts.json] [--cache-dir DIR] [--offline]
                [--visual fab/cpl_visual.json] [--out report.json]
LCSC numbers come from the board's footprint fields, overridden by parts.json.
The cache is $HWDE_EASYEDA_CACHE, else <parts.json dir>/easyeda when that
dir exists, else ~/.cache/hwde/easyeda (env.easyeda_cache); --offline (or HWDE_EASYEDA_OFFLINE=1) never fetches.
Exit 0 every part verified / 1 a part is wrong or unverified / 2 error.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import sexpdata  # noqa: E402

import easyeda  # noqa: E402

OFFSET_WARN_MM = 0.5
ROT_TOL_DEG = 1.0
FAIL_VERDICTS = ("wrong_rotation", "wrong_polarity", "no_model", "no_fit",
                 "bottom_unverified", "fetch_failed")

# ------------------------------------------------------------ board side


@dataclass
class KPad:
    number: str
    x: float
    y: float
    w: float
    h: float
    function: str = ""
    angle: float = 0.0  # absolute pad angle on the board (includes fp rot)


@dataclass
class Footprint:
    ref: str
    fpid: str
    x: float
    y: float
    rot: float
    side: str
    pads: list[KPad] = field(default_factory=list)
    silk: list[list[tuple[float, float]]] = field(default_factory=list)
    fields: dict = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.fpid.split(":", 1)[-1]


def _t(x):
    return x.value() if isinstance(x, sexpdata.Symbol) else x


def _kids(n, name):
    return [c for c in n[1:] if isinstance(c, list) and c and _t(c[0]) == name]


def _kid(n, name):
    k = _kids(n, name)
    return k[0] if k else None


def _nums(n):
    return [float(v) for v in n[1:] if isinstance(v, (int, float))] if n else []


def _silk_shape(node) -> list[tuple[float, float]] | None:
    lay = _kid(node, "layer")
    if not lay or not str(_t(lay[1])).endswith("SilkS"):
        return None
    head = _t(node[0])
    if head == "fp_line":
        return [tuple(_nums(_kid(node, "start"))), tuple(_nums(_kid(node, "end")))]
    if head == "fp_rect":
        (x0, y0), (x1, y1) = _nums(_kid(node, "start")), _nums(_kid(node, "end"))
        return [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    if head == "fp_poly":
        pts = _kid(node, "pts")
        out = [tuple(_nums(p)) for p in _kids(pts, "xy")] if pts else []
        return out + out[:1]
    if head == "fp_circle":
        (cx, cy), (ex, ey) = _nums(_kid(node, "center")), _nums(_kid(node, "end"))
        r = math.hypot(ex - cx, ey - cy)
        return [(cx + r * math.cos(a / 12 * math.pi),
                 cy + r * math.sin(a / 12 * math.pi)) for a in range(25)]
    if head == "fp_arc":
        return [tuple(_nums(_kid(node, k))) for k in ("start", "mid", "end")]
    return None


def board_footprints(pcb: Path) -> dict[str, Footprint]:
    """{ref: Footprint} with pads and silk in the footprint's LOCAL frame."""
    tree = sexpdata.loads(Path(pcb).read_text(encoding="utf-8"))
    out: dict[str, Footprint] = {}
    for fp in _kids(tree, "footprint"):
        at = _nums(_kid(fp, "at"))
        props = {str(_t(p[1])): str(_t(p[2])) for p in _kids(fp, "property")
                 if len(p) > 2}
        ref = props.get("Reference", "")
        layer = str(_t(_kid(fp, "layer")[1]))
        f = Footprint(ref=ref, fpid=str(_t(fp[1])), x=at[0], y=at[1],
                      rot=at[2] if len(at) > 2 else 0.0,
                      side="bottom" if layer.startswith("B.") else "top",
                      fields=props)
        for pad in _kids(fp, "pad"):
            pat = _nums(_kid(pad, "at"))
            size = _nums(_kid(pad, "size")) or [0.0, 0.0]
            pf = _kid(pad, "pinfunction")
            f.pads.append(KPad(number=str(_t(pad[1])), x=pat[0], y=pat[1],
                               w=size[0], h=size[1],
                               function=str(_t(pf[1])) if pf else "",
                               angle=pat[2] if len(pat) > 2 else 0.0))
        for node in fp[1:]:
            if isinstance(node, list) and node:
                s = _silk_shape(node)
                if s:
                    f.silk.append(s)
        if ref:
            out[ref] = f
    return out


# ------------------------------------------------------------- geometry

def rot(x: float, y: float, deg: float) -> tuple[float, float]:
    """Turn (x, y) by deg COUNTER-CLOCKWISE as seen on screen, in a y-down
    frame (KiCad's board frame and EasyEDA's canvas)."""
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return (x * c + y * s, -x * s + y * c)


def _centres(pads, key) -> dict[str, tuple[float, float]]:
    acc: dict[str, list] = {}
    for p in pads:
        k = key(p)
        if k:
            acc.setdefault(k, []).append((p.x, p.y))
    return {k: (sum(x for x, _ in v) / len(v), sum(y for _, y in v) / len(v))
            for k, v in acc.items()}


def _fit(emap, kmap, tol) -> list[tuple[float, float]]:
    """[(correction, max error mm)] for every turn under which each shared
    key of the model lands on the same key of the board footprint."""
    common = sorted(set(emap) & set(kmap))
    if len(common) < 2:
        return []
    kc = (sum(kmap[k][0] for k in common) / len(common),
          sum(kmap[k][1] for k in common) / len(common))
    good = []
    for c in list(range(0, 360, 90)) + list(range(45, 360, 90)):
        pts = {k: rot(*emap[k], c) for k in common}
        ec = (sum(p[0] for p in pts.values()) / len(common),
              sum(p[1] for p in pts.values()) / len(common))
        err = max(math.hypot(pts[k][0] - ec[0] - (kmap[k][0] - kc[0]),
                             pts[k][1] - ec[1] - (kmap[k][1] - kc[1]))
                  for k in common)
        if err <= tol:
            good.append((float(c), err))
    if good and any(c % 90 == 0 for c, _ in good):
        good = [g for g in good if g[0] % 90 == 0]
    return good


# --------------------------------------------------------------- polarity

_ROLES = {"K": "K", "KA": "K", "CATHODE": "K", "A": "A", "ANODE": "A",
          "+": "+", "POS": "+", "PLUS": "+", "-": "-", "NEG": "-",
          "MINUS": "-"}
_POLAR_FP = re.compile(r"^(D_|LED|CP_|C_Elec)|LED|Diode|SOD-|Tantal|Elec|"
                       r"CAP-TH|CAP-SMD_BD|^CASE-[A-E]", re.I)
_POLAR_REF = re.compile(r"^(D|LED|CR)\d")


def role(name: str) -> str:
    return _ROLES.get((name or "").strip().upper(), "")


def is_polar(fp: Footprint, model=None) -> bool:
    """Diodes, LEDs and polarised caps: by pad function, refdes, the board
    footprint's name or the LCSC model's package name."""
    if any(role(p.function) for p in fp.pads) or (model and model_roles(model)):
        return True
    return bool(_POLAR_REF.match(fp.ref) or _POLAR_FP.search(fp.name)
                or (model and _POLAR_FP.search(model.package or "")))


def board_roles(fp: Footprint, model) -> dict[str, str]:
    """pad number -> role on the board side (see the module docstring)."""
    got = {p.number: role(p.function) for p in fp.pads if role(p.function)}
    if got:
        return got
    n = fp.name
    if re.match(r"^(D_|LED_)", n):
        return {"1": "K", "2": "A"}
    if re.match(r"^(CP_|C_Elec)", n):
        return {"1": "+", "2": "-"}
    if model is not None and model.package and model.package in n:
        return model_roles(model)
    return {}


def model_roles(model) -> dict[str, str]:
    return {num: role(nm) for num, nm in model.pin_names.items() if role(nm)}


# -------------------------------------------------------------- verdicts

def _ang_eq(a: float, b: float, tol: float = ROT_TOL_DEG) -> bool:
    d = (a - b) % 360.0
    return min(d, 360.0 - d) <= tol


def analyse(fp: Footprint, model) -> dict:
    """The corrections that put this model onto this footprint, with how
    they were found. No CPL involved, so bom_cpl can derive from it."""
    if fp.side != "top":
        return {"verdict": "bottom_unverified",
                "why": "bottom-side part: JLC's bottom placement convention is "
                       "not modelled - confirm it in JLC's preview"}
    if model is None:
        return {"verdict": "no_model",
                "why": "no LCSC footprint model (no LCSC number, or EasyEDA "
                       "had no data and nothing is cached)"}
    kpads = [p for p in fp.pads if p.w > 0]
    # A fit holds when every model pad is nearer its own board pad than any
    # other: half the smallest gap between two numbered board pad centres.
    # Land patterns of one package differ in toe length (an SO-8 model's
    # rows 6.0 mm apart vs a KiCad 5.12), so pad size is no measure.
    kc = list(_centres(kpads, lambda p: p.number).values())
    gaps = [math.hypot(a[0] - b[0], a[1] - b[1])
            for i, a in enumerate(kc) for b in kc[i + 1:]]
    tol = max(0.2, 0.5 * min(gaps, default=0.5))
    num_fit = _fit(_centres(model.pads, lambda p: p.number),
                   _centres(kpads, lambda p: p.number), tol)
    polar = is_polar(fp, model)
    out = {"polar": polar, "tol_mm": round(tol, 3), "basis": "pad number"}
    corr = [c for c, _ in num_fit]
    if polar:
        mr, br = model_roles(model), board_roles(fp, model)
        shared = set(mr.values()) & set(br.values())
        if shared:
            rkey_m = {n: r for n, r in mr.items()}
            role_fit = _fit(_centres([p for p in model.pads if p.number in rkey_m],
                                     lambda p: rkey_m.get(p.number)),
                            _centres([p for p in kpads if p.number in br],
                                     lambda p: br.get(p.number)), tol)
            rc = [c for c, _ in role_fit]
            if rc:
                out["basis"] = "pin role (K/A, +/-)"
                if corr and not set(rc) & set(corr):
                    out.update(verdict="wrong_polarity", corrections=rc,
                               number_corrections=corr,
                               why="the LCSC model's numbering puts its "
                                   "cathode/+ pin on the board's anode/- pad")
                    return out
                corr = rc
        else:
            # LCSC symbols for polarised caps are usually pins "1"/"2" with
            # pad 1 the + (and K for diodes), which is KiCad's numbering too.
            out["basis"] = "pad number (no K/A or +/- names on both sides)"
    elif len(model.pads) == 2 and len(kpads) == 2 and corr:
        corr = sorted({c % 360 for c in corr} | {(c + 180) % 360 for c in corr})
    if not corr:
        out.update(verdict="no_fit",
                   why="the LCSC model's pads do not land on the board "
                       "footprint's pads by number at any 45-degree turn")
        return out
    out.update(verdict="ok", corrections=corr)
    return out


def derive_rotation(fp: Footprint, model) -> float | None:
    """The CPL rotation the model says this part needs, or None."""
    a = analyse(fp, model)
    if a["verdict"] != "ok":
        return None
    return (fp.rot + a["corrections"][0]) % 360.0


def check_part(fp: Footprint, model, cpl: dict) -> dict:
    a = analyse(fp, model)
    row = {"ref": fp.ref, "footprint": fp.fpid,
           "lcsc": model.lcsc if model else None,
           "model_package": model.package if model else None,
           "board_rot": round(fp.rot % 360.0, 3),
           "cpl_rot": cpl["rot"], **a}
    if a["verdict"] != "ok":
        if "corrections" in a:
            row["expected_rot"] = [(fp.rot + c) % 360.0 for c in a["corrections"]]
        return row
    want = [(fp.rot + c) % 360.0 for c in a["corrections"]]
    row["expected_rot"] = want
    if not any(_ang_eq(cpl["rot"], w) for w in want):
        row["verdict"] = "wrong_rotation"
        row["why"] = (f"CPL rotation {cpl['rot']:g} puts pin 1 off pad 1; the "
                      f"LCSC model needs {want[0]:g}")
    # Offset: model pad centre vs board pad centre, both placed.
    em = [(p.x, p.y) for p in model.pads]
    km = [(p.x, p.y) for p in fp.pads]
    ec = rot(sum(x for x, _ in em) / len(em), sum(y for _, y in em) / len(em),
             cpl["rot"])
    kcx, kcy = rot(sum(x for x, _ in km) / len(km),
                   sum(y for _, y in km) / len(km), fp.rot)
    row["offset_mm"] = round(math.hypot(cpl["x"] + ec[0] - (fp.x + kcx),
                                        cpl["y"] + ec[1] - (fp.y + kcy)), 3)
    return row


# ------------------------------------------------------------------- run

def read_cpl(path: Path) -> dict[str, dict]:
    """CPL.csv -> {ref: {x, y (board frame, y down), rot, layer}}."""
    out = {}
    with Path(path).open(newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            out[r["Designator"].strip()] = {
                "x": float(r["Mid X"]), "y": -float(r["Mid Y"]),
                "rot": float(r["Rotation"]) % 360.0,
                "layer": r.get("Layer", "Top").strip()}
    return out


def offline() -> bool:
    return os.environ.get("HWDE_EASYEDA_OFFLINE", "") not in ("", "0")


def default_cache(parts_json: Path | None) -> Path:
    import env
    return env.easyeda_cache(
        Path(parts_json).parent if parts_json is not None else None)


def lcsc_map(pcb: Path, parts_json: Path | None) -> dict[str, str]:
    import bom_cpl
    m = {r: f["lcsc"] for r, f in bom_cpl.board_part_fields(pcb).items()
         if f.get("lcsc")}
    m.update({r: e["lcsc"] for r, e in bom_cpl.load_parts_map(parts_json).items()
              if e.get("lcsc")})
    return m


def verify(pcb: Path, cpl: dict[str, dict], lcsc: dict[str, str],
           cache_dir: Path, fetch: bool = True,
           fps: dict[str, Footprint] | None = None) -> dict:
    fps = fps if fps is not None else board_footprints(pcb)
    rows = []
    for ref in sorted(cpl, key=_natural):
        fp = fps.get(ref)
        if fp is None:
            rows.append({"ref": ref, "verdict": "no_fit",
                         "why": "CPL designator is not on the board"})
            continue
        code = lcsc.get(ref, "")
        errors: dict = {}
        model = easyeda.get(code, cache_dir, fetch=fetch, pace_s=1.0,
                            errors=errors)
        row = check_part(fp, model, cpl[ref])
        if row["verdict"] == "no_model" and errors:
            row["verdict"] = "fetch_failed"
            row["fetch_error"] = next(iter(errors.values()))
            row["why"] = (f"could not fetch the LCSC model for {code} "
                          f"({row['fetch_error']}); cache dir "
                          f"{cache_dir} - this is not a pin-1 result")
        rows.append(row)
    bad = [r for r in rows if r["verdict"] in FAIL_VERDICTS]
    return {"script": "cpl_verify", "board": Path(pcb).name,
            "status": "violations" if bad else "pass",
            "n_checked": len(rows), "n_failed": len(bad),
            "failed": [r["ref"] for r in bad],
            "offset_warnings": [r["ref"] for r in rows
                                if r.get("offset_mm", 0) > OFFSET_WARN_MM],
            "fetch_failed": [r["ref"] for r in rows
                             if r["verdict"] == "fetch_failed"],
            "rate_limited": easyeda.rate_limited(),
            "cache_dir": str(cache_dir),
            "parts": rows}


def _natural(ref: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", ref)]


# ------------------------------------------------------- visual verdicts

VISUAL_OK = "match"


def merge_visual(parts: list[dict], visual: dict | None) -> list[dict]:
    """Put the image verdict beside the script verdict, per designator.

    `visual` is cpl_visual.json: {"model": ..., "parts": {ref: {"pin1":
    "match"|"mismatch"|"unclear", "polarity": "match"|"mismatch"|"n/a"|
    "unclear", "note": str}}}. A ref is `agree` when both say right or both
    say wrong, `disagree` otherwise; `unclear` and `missing` (the visual pass
    has no line for a checked part) are not agreement either. Returns the
    rows with `visual` and `agreement` set."""
    vparts = (visual or {}).get("parts", {})
    out = []
    for r in parts:
        r = dict(r)
        v = vparts.get(r["ref"])
        r["visual"] = v
        if visual is None:
            r["agreement"] = "not_run"
        elif v is None:
            r["agreement"] = "missing"
        else:
            marks = [v.get("pin1"), v.get("polarity")]
            if "unclear" in marks:
                r["agreement"] = "unclear"
            else:
                vis_ok = all(m in (VISUAL_OK, "n/a", None) for m in marks)
                scr_ok = r["verdict"] == "ok"
                r["agreement"] = "agree" if vis_ok == scr_ok else "disagree"
        out.append(r)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pcb", required=True)
    ap.add_argument("--cpl", required=True, help="CPL.csv to verify")
    ap.add_argument("--parts", help="parts.json (LCSC numbers per ref)")
    ap.add_argument("--cache-dir", help="EasyEDA model cache directory")
    ap.add_argument("--offline", action="store_true",
                    help="never fetch: an uncached part is no_model")
    ap.add_argument("--visual", help="cpl_visual.json from the image pass")
    ap.add_argument("--out", help="write JSON here instead of stdout")
    args = ap.parse_args(argv)
    try:
        pcb = Path(args.pcb)
        pj = Path(args.parts) if args.parts else None
        rep = verify(pcb, read_cpl(Path(args.cpl)), lcsc_map(pcb, pj),
                     Path(args.cache_dir) if args.cache_dir
                     else default_cache(pj),
                     fetch=not (args.offline or offline()))
        if args.visual:
            vis = json.loads(Path(args.visual).read_text(encoding="utf-8"))
            rep["parts"] = merge_visual(rep["parts"], vis)
            dis = [r["ref"] for r in rep["parts"]
                   if r["agreement"] in ("disagree", "missing")]
            rep["visual_disagree"] = dis
            if dis:
                rep["status"] = "violations"
    except Exception as exc:  # noqa: BLE001 (SPEC: any error -> exit 2)
        rep = {"script": "cpl_verify", "status": "error",
               "error": f"{type(exc).__name__}: {exc}"}
    text = json.dumps(rep, indent=1)
    (Path(args.out).write_text(text, encoding="utf-8") if args.out
     else print(text))
    return {"pass": 0, "violations": 1}.get(rep["status"], 2)


if __name__ == "__main__":
    raise SystemExit(main())
