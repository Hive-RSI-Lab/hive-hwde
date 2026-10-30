"""boardreg.py - a board workspace's part number, read from the boards register.

Board part numbers live in one place: register.yaml at the root of the boards
repo (env.boards_root(), HWDE_BOARDS_ROOT, default ~/dev/boards). Numbering is
the document library's PCB-NNNN-R scheme - four digits and a revision letter
(A, B, C..., skipping I and O):

  products:
    PCB-0001:
      title: <what the board is>
      revs:
        A: {dir: pd-trigger-lite, date: 2026-09-24, bom: pd-trigger-lite/fab/BOM.csv}
        B: {dir: pd-trigger-lite-dip, date: ..., change: "..."}

A workspace's number is the product key plus the rev letter whose `dir` is
the workspace's directory name (PCB-0001-B). The register read is the one in
the directory that holds the workspace, so a scratch copy elsewhere never
borrows a real board's number. hwde only READS the register: it never
allocates a number, and a board that is not in it carries none.

A numbered workspace's directory (and the KiCad project inside it) is named
<PN>_<name>, e.g. PCB-0016-A_pd-trigger-lite; older ones are still the bare
<name>. resolve() finds a workspace given either spelling or the bare PN, so
nothing needs the directory name to equal the board name. split_dir() takes a
directory name apart.

A NEW board gets its number the same read-only way: whoever issues it (the
owner, or the boards repo's own tooling) first adds the rev to register.yaml
with `dir: <PN>_<name>`; new_dir() then names the workspace from that entry,
and a board the register does not list yet is created bare and renamed later.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

REGISTER = "register.yaml"
_PRODUCT = re.compile(r"^PCB-\d{4}$")
_REV = re.compile(r"^[A-HJ-NP-Z]$")
_PN = re.compile(r"^(PCB-\d{4})-([A-HJ-NP-Z])$")
_PN_DIR = re.compile(r"^(PCB-\d{4}-[A-HJ-NP-Z])_(.+)$")


def split_dir(dirname: str) -> tuple[str | None, str]:
    """(pn, human name) of a workspace directory name: ("PCB-0016-A",
    "pd-trigger-lite") for PCB-0016-A_pd-trigger-lite, (None, name) for a
    bare name."""
    m = _PN_DIR.match(dirname)
    return (m.group(1), m.group(2)) if m else (None, dirname)


def load(root: Path) -> dict:
    """{pn: rev entry} of the register at root/register.yaml, each entry
    carrying its "product", "rev", "title" and "dir"; {} when there is no
    readable register."""
    reg = Path(root) / REGISTER
    try:
        data = yaml.safe_load(reg.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    out: dict = {}
    for product, ent in ((data.get("products") or {}) if isinstance(
            data, dict) else {}).items():
        if not isinstance(ent, dict):
            continue
        for rev, r in (ent.get("revs") or {}).items():
            if isinstance(r, dict) and r.get("dir"):
                out[f"{product}-{rev}"] = {**r, "product": str(product),
                                           "rev": str(rev),
                                           "title": ent.get("title")}
    return out


def resolve(key: str, root: Path) -> Path | None:
    """The workspace under `root` that `key` names, or None. key is a
    directory name (old bare name or new <PN>_<name>), a part number
    (PCB-0016-A), or a bare human name whose directory now carries its PN.
    An existing directory named key wins; then the register; then a lone
    <PN>_<key> directory. Ambiguity (two matches) is None, never a guess."""
    root = Path(root)
    if key and (root / key).is_dir():
        return root / key
    reg = load(root)
    if _PN.match(key or ""):
        ent = reg.get(key)
        return root / ent["dir"] if ent and (root / ent["dir"]).is_dir() \
            else None
    hits = {root / e["dir"] for e in reg.values()
            if split_dir(e["dir"])[1] == key and (root / e["dir"]).is_dir()}
    if not hits and root.is_dir():
        hits = {d for d in root.iterdir() if d.is_dir()
                and split_dir(d.name)[0] and split_dir(d.name)[1] == key}
    return hits.pop() if len(hits) == 1 else None


def part_number(ws: Path) -> tuple[dict | None, str]:
    """(number, why) for the workspace `ws`.

    number is {"pn": "PCB-0001-B", "product": "PCB-0001", "rev": "B",
    "title": ..., "register": <path>} or None; why says in words why there
    is none (no register, not listed, unreadable) and is "" when found.
    """
    ws = Path(ws).resolve()
    reg = ws.parent / REGISTER
    if not reg.is_file():
        return None, f"no {REGISTER} beside the workspace ({ws.parent})"
    try:
        data = yaml.safe_load(reg.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        return None, f"{reg} unreadable: {type(exc).__name__}: {exc}"
    products = data.get("products") if isinstance(data, dict) else None
    if not isinstance(products, dict):
        return None, f"{reg} has no products map"
    hits = []
    for product, ent in products.items():
        revs = (ent or {}).get("revs") if isinstance(ent, dict) else None
        for rev, r in (revs or {}).items():
            if isinstance(r, dict) and r.get("dir") == ws.name:
                hits.append((str(product), str(rev), ent))
    if not hits:
        return None, f"{ws.name} is not in {reg}"
    if len(hits) > 1:
        return None, (f"{ws.name} is listed {len(hits)} times in {reg} ("
                      + ", ".join(f"{p}-{r}" for p, r, _ in hits) + ")")
    product, rev, ent = hits[0]
    if not _PRODUCT.match(product) or not _REV.match(rev):
        return None, f"{product}-{rev} in {reg} is not a PCB-NNNN-R number"
    return {"pn": f"{product}-{rev}", "product": product, "rev": rev,
            "title": ent.get("title"), "register": str(reg)}, ""


def new_dir(name: str, root: Path, pn: str | None = None) -> str:
    """The directory (and KiCad project) name a new workspace for board
    `name` takes under `root`: the register's `dir` for part number `pn`
    when given (ValueError when the register does not list it), else the
    `dir` of the one rev spelled <PN>_<name> that is its own PN, else the
    bare name (no number issued yet)."""
    reg = load(root)
    if pn:
        if pn not in reg:
            raise ValueError(f"{pn} is not in {Path(root) / REGISTER}; "
                             "add the rev there first (hwde never issues one)")
        return reg[pn]["dir"]
    hits = [e["dir"] for p, e in reg.items() if split_dir(e["dir"]) == (p, name)]
    return hits[0] if len(hits) == 1 else name


def locate(arg: str | Path, root: Path) -> Path:
    """A workspace path as given, or - when it does not exist - the workspace
    its last component names (resolve()) in the directory above it, or in
    `root` (the boards root) for a bare name - or, for a board not created
    yet, the <PN>_<name> directory the register issued it (new_dir()).
    Unchanged otherwise, so the caller's own "no such workspace" message
    still fires."""
    p = Path(arg).expanduser()
    if p.exists():
        return p
    base = p.parent if len(p.parts) > 1 else Path(root)
    hit = resolve(p.name, base)
    if hit is None and not _PN.match(p.name):
        issued = new_dir(p.name, base)
        hit = base / issued if issued != p.name else None
    return hit or p
