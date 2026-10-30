"""checklistverify.py - an independent agent's ruling on a coverage checklist,
graded against planted faults (the logic behind scripts/checklist_verify.py).

A coverage checklist says which knowledge classes a topology or interface
needs before hwde may claim it knows enough to design one. Upstream, a
checklist counted only once the person approved it. The Hive has to run
without the person in the loop, so a checklist can count on a ruling
instead, once that ruling has shown it can tell a sound checklist from a
broken one:

  packet  the checklist and N planted mutants of it (a required class
          dropped or swapped, an unrelated class added, a number in a note
          corrupted), blinded: same shape, shuffled labels, no maturity,
          approval or origin. The answer key goes to a separate file that the
          verifier never sees; the packet's manifest pins its sha256.
  rule    grades the verifier's verdicts against the key. It counts only
          when the verifier is not the checklist's author, every planted
          mutant was called faulty for the planted reason, the real item
          was called sound, and every required class rests on a library
          record holding that class or a cited source file that exists.
          Then (--write) the checklist goes to maturity `verified` with a
          `verification` block pinned to its content digest, and coverage
          counts it as it counts an approved one
          (knowledgelib.checklist_meets_floor). Any later edit to what the
          checklist decides breaks the digest and drops it back.

A verifier that misses a planted fault proves nothing about the real item,
so its ruling is refused whole, whatever it said about the real one.

What the planted faults can't stop: the mutants differ from the real item
in one place each, so a verifier that diffs the items can guess the real one
by majority without reading a page. The basis check is what doesn't give
way to that: every class the real checklist requires has to rest on an
active record that holds the class and applies to the checklist's own
tokens, or on a cited file that exists, and that is checked here, not
taken on the verifier's word.
"""
from __future__ import annotations

import copy
import datetime as _dt
import hashlib
import json
import random
import re
import secrets
from pathlib import Path

import yaml

import knowledgelib

MUTATIONS = ("drop_row", "swap_class", "add_foreign", "corrupt_number")
FAULT_KINDS = ("missing", "unsupported", "wrong-fact")
# What a blinded item keeps: the content a verifier rules on.
ITEM_KEYS = ("id", "kind", "applies", "requires", "sources", "prose")

# A plausible row for each class, so an added or swapped row reads like the
# rest and only knowing the block gives it away.
FOREIGN_NOTES = {
    "power-loop": "the high di/dt loop through the input cap and switch, kept small and on one layer",
    "emi": "radiating nodes contained, aggressors kept away from sensitive traces",
    "thermal": "dissipation against the package and copper, junction kept under its limit at max ambient",
    "thermal-via": "thermal-via array under the exposed pad, drill and pitch per the package note",
    "decoupling": "local bypass caps at each supply pin, smallest value nearest the pin",
    "feedback": "feedback divider and its node routed away from switching copper",
    "selection": "the part chosen against its ratings at the worst corners",
    "sourcing": "a second source or stock depth for every part in the block",
    "inrush": "inrush at plug-in limited to what the upstream source allows",
    "sequencing": "enable and power-good order against the rails it depends on",
    "return-path": "an unbroken reference plane under every signal the block carries",
    "constraints-emission": "what the block must emit into constraints.json for placement and routing",
    "esd": "an ESD clamp at the connector, returned to ground without crossing logic ground",
    "creepage": "creepage and clearance across the isolation barrier for the working voltage",
    "diff-pair": "100 ohm differential pair: width and gap for the stackup, intra-pair skew budget",
}
_NUM = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)")


def today() -> str:
    return _dt.date.today().isoformat()


def blind(cl: dict) -> dict:
    """The checklist as a verifier sees it: its content, none of its
    standing (maturity, approval, verification, origin)."""
    return {k: copy.deepcopy(cl[k]) for k in ITEM_KEYS if k in cl}


def _classes(item: dict) -> list[str]:
    return [knowledgelib.norm_token(r.get("class", ""))
            for r in item.get("requires") or [] if isinstance(r, dict)]


def _foreign(item: dict, rng: random.Random, taken: set[str]) -> str | None:
    have = set(_classes(item)) | taken
    pool = sorted(c for c in knowledgelib.CLASSES if c not in have)
    return rng.choice(pool) if pool else None


def _bump(m: re.Match) -> str:
    v = m.group(1)
    return str(int(v) * 10) if "." not in v else f"{float(v) * 10:g}"


def mutate(cl: dict, kind: str, rng: random.Random,
           taken: set[str]) -> tuple[dict, dict] | None:
    """One planted mutant of a blinded checklist and what catching it means
    (`expect`: the fault a verifier must name). None when the kind can't
    apply to this checklist (one row can't be dropped; no number to
    corrupt; no class left to add)."""
    item = blind(cl)
    rows = item.get("requires") or []
    if kind == "drop_row":
        if len(rows) < 2:
            return None
        i = rng.randrange(len(rows))
        cls = knowledgelib.norm_token(rows[i]["class"])
        del rows[i]
        return item, {"kind": kind, "row": i, "class": cls,
                      "expect": [{"kind": "missing", "class": cls}]}
    if kind in ("swap_class", "add_foreign"):
        new = _foreign(item, rng, taken)
        if new is None:
            return None
        taken.add(new)
        row = {"class": new, "min_level": "topology",
               "note": FOREIGN_NOTES.get(new, f"{new} rules for this block")}
        if kind == "add_foreign":
            i = rng.randrange(len(rows) + 1)
            rows.insert(i, row)
            return item, {"kind": kind, "row": i, "class": new,
                          "expect": [{"kind": "unsupported", "class": new}]}
        i = rng.randrange(len(rows))
        old = knowledgelib.norm_token(rows[i]["class"])
        row["min_level"] = rows[i].get("min_level", "topology")
        rows[i] = row
        return item, {"kind": kind, "row": i, "class": new, "replaced": old,
                      "expect": [{"kind": "unsupported", "class": new},
                                 {"kind": "missing", "class": old}]}
    if kind == "corrupt_number":
        spots = [("requires", i) for i, r in enumerate(rows)
                 if _NUM.search(str(r.get("note") or ""))]
        spots += [("sources", i) for i, s in enumerate(item.get("sources") or [])
                  if isinstance(s, dict) and _NUM.search(str(s.get("note") or ""))]
        if not spots:
            return None
        where, i = spots[rng.randrange(len(spots))]
        target = item[where][i]
        before = str(target["note"])
        target["note"] = _NUM.sub(_bump, before, count=1)
        info = {"kind": kind, "where": f"{where}[{i}]",
                "was": _NUM.search(before).group(1)}
        if where == "requires":
            cls = knowledgelib.norm_token(target["class"])
            info["expect"] = [{"kind": "wrong-fact", "class": cls}]
        else:
            info["expect"] = [{"kind": "wrong-fact", "source": i}]
        return item, info
    raise ValueError(f"unknown mutation {kind!r}")


def plan(cl: dict, n: int, rng: random.Random) -> list[tuple[dict, dict]]:
    """n distinct mutants: a class lost (drop, or swap on a one-row list),
    an unrelated class added, a wrong number - then more swaps and adds."""
    order = ["drop_row" if len(cl.get("requires") or []) >= 2 else "swap_class",
             "add_foreign", "corrupt_number"]
    order += ["swap_class", "add_foreign"] * n
    out, taken, seen = [], set(), set()
    for kind in order:
        if len(out) >= n:
            break
        got = mutate(cl, kind, rng, taken)
        if got is None:
            continue
        blob = json.dumps(got[0], sort_keys=True, default=str)
        if blob in seen or got[0] == blind(cl):
            continue
        seen.add(blob)
        out.append(got)
    return out


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_packet(cl: dict, packet: Path, key_path: Path, planted: int,
                seed: int | None = None) -> dict:
    """Write the blinded packet to `packet` and the answer key to
    `key_path` (keep it from the verifier). Returns the manifest."""
    if planted < knowledgelib.CHECKLIST_MIN_PLANTED:
        raise ValueError(f"--planted must be at least "
                         f"{knowledgelib.CHECKLIST_MIN_PLANTED}")
    packet, key_path = Path(packet), Path(key_path)
    if key_path.resolve().is_relative_to(packet.resolve()):
        raise ValueError("the key must live outside the packet the verifier "
                         "reads")
    seed = secrets.randbits(32) if seed is None else seed
    rng = random.Random(seed)
    mutants = plan(cl, planted, rng)
    if len(mutants) < planted:
        raise ValueError(f"only {len(mutants)} distinct mutants fit checklist "
                         f"{cl.get('id')!r} (asked {planted})")
    entries = [(blind(cl), {"real": True})] + [
        (m, {"real": False, "mutation": info}) for m, info in mutants]
    rng.shuffle(entries)
    items_dir = packet / "items"
    items_dir.mkdir(parents=True, exist_ok=True)
    for old in items_dir.glob("*.yaml"):
        old.unlink()
    key = {"checklist": cl.get("id"), "digest": knowledgelib.checklist_digest(cl),
           "seed": seed, "created": today(), "items": {}}
    labels = []
    for n, (item, answer) in enumerate(entries, 1):
        label = f"item-{n}"
        labels.append(label)
        (items_dir / f"{label}.yaml").write_text(
            yaml.safe_dump(item, sort_keys=False, allow_unicode=False,
                           width=78), encoding="utf-8")
        key["items"][label] = answer
    key_bytes = (json.dumps(key, indent=2) + "\n").encode("utf-8")
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(key_bytes)
    # No path to the checklist: the verifier must not be pointed at the
    # real item.
    manifest = {"checklist": cl.get("id"),
                "digest": key["digest"], "key_sha256": _sha(key_bytes),
                "items": labels, "created": key["created"],
                "verdicts_template": verdicts_template(labels)}
    (packet / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def verdicts_template(labels: list[str]) -> dict:
    return {"by": "<your run id>", "items": {
        lb: {"verdict": "sound|faulty",
             "faults": [{"kind": "missing|unsupported|wrong-fact",
                         "class": "<class, for a requires row>",
                         "source": "<index, for a sources[] note>",
                         "why": "<page and what it says>"}],
             "basis": {"<class>": ["<record id> or <file>#<page>"]}}
        for lb in labels}}


def _fault_matches(fault: dict, want: dict) -> bool:
    if fault.get("kind") != want["kind"]:
        return False
    if "class" in want:
        return knowledgelib.norm_token(str(fault.get("class") or "")) \
            == want["class"]
    try:
        return int(fault.get("source")) == want["source"]
    except (TypeError, ValueError):
        return False


def basis_problems(cl: dict, basis: dict, records: list[dict],
                   source_roots=()) -> list[str]:
    """Every required class must rest on something real: an ACTIVE library
    (or workspace) record holding that class, or a cited file that exists
    (`file` or `file#page`)."""
    by_id = {r.get("id"): r for r in records}
    norm = {knowledgelib.norm_token(k): v for k, v in (basis or {}).items()}
    out = []
    for cls in _classes(cl):
        refs = norm.get(cls) or []
        if not isinstance(refs, list) or not refs:
            out.append(f"no basis given for required class {cls!r}")
            continue
        for ref in refs:
            ref = str(ref)
            r = by_id.get(ref)
            if r is not None:
                held = {knowledgelib.norm_token(c) for c in r.get("classes") or []}
                toks = {(f, knowledgelib.norm_token(t))
                        for f in ("topologies", "interfaces")
                        for t in (r.get("applies") or {}).get(f) or []}
                mine = {(f, knowledgelib.norm_token(t))
                        for f in ("topologies", "interfaces")
                        for t in (cl.get("applies") or {}).get(f) or []}
                if r.get("status") != "active":
                    out.append(f"{cls}: record {ref!r} is not active")
                elif cls not in held:
                    out.append(f"{cls}: record {ref!r} does not hold class "
                               f"{cls!r}")
                elif not (toks & mine) and r.get("level") != "principle":
                    out.append(f"{cls}: record {ref!r} applies to none of "
                               "the checklist's topologies/interfaces and is "
                               "not a principle")
                continue
            file = ref.split("#", 1)[0]
            if not knowledgelib._source_exists(file, source_roots):
                out.append(f"{cls}: {ref!r} is neither a record id nor a "
                           "source file that exists")
    return out


def grade(cl: dict, manifest: dict, key: dict, key_bytes: bytes,
          verdicts: dict, records: list[dict], source_roots=()) -> dict:
    """The ruling's grade. status verified only when everything holds;
    `problems` says what didn't."""
    problems: list[str] = []
    if manifest.get("key_sha256") != _sha(key_bytes):
        problems.append("the key does not match the packet's manifest "
                        "(key_sha256)")
    digest = knowledgelib.checklist_digest(cl)
    if key.get("digest") != digest or manifest.get("digest") != digest:
        problems.append("the checklist changed after the packet was made - "
                        "make a new packet")
    by = str(verdicts.get("by") or "").strip()
    if not by or by.startswith("<"):
        problems.append("verdicts name no verifier (`by`)")
    elif knowledgelib.same_author(by, cl.get("origin")):
        problems.append(f"verifier {by!r} is the checklist's author "
                        f"({cl.get('origin')!r}) - the ruling must come "
                        "from someone else")
    elif by.lower() == "owner":
        problems.append("`owner` is the approval path (approval block), "
                        "not a verifier")
    ruled = verdicts.get("items") or {}
    planted, caught, kinds = 0, 0, []
    missed, real_faults, basis = [], [], {}
    for label, answer in (key.get("items") or {}).items():
        v = ruled.get(label)
        if not isinstance(v, dict) or v.get("verdict") not in ("sound", "faulty"):
            problems.append(f"{label}: no sound/faulty verdict")
            if not answer["real"]:
                planted += 1
                missed.append({"item": label, **answer["mutation"]})
            continue
        if answer["real"]:
            if v["verdict"] == "faulty":
                real_faults = list(v.get("faults") or [])
                problems.append(f"{label}: the verifier ruled the checklist "
                                f"itself faulty: {json.dumps(real_faults)}")
            else:
                basis = {knowledgelib.norm_token(k): list(vv)
                         for k, vv in (v.get("basis") or {}).items()
                         if isinstance(vv, list)}
                problems += basis_problems(cl, basis, records, source_roots)
            continue
        planted += 1
        mut = answer["mutation"]
        kinds.append(mut["kind"])
        hit = v["verdict"] == "faulty" and any(
            isinstance(f, dict) and _fault_matches(f, want)
            for f in v.get("faults") or [] for want in mut["expect"])
        if hit:
            caught += 1
        else:
            missed.append({"item": label, **mut})
    if missed:
        problems.append(f"the verifier missed {len(missed)} of {planted} "
                        "planted fault(s), so its ruling on the real item "
                        "doesn't count")
    if planted < knowledgelib.CHECKLIST_MIN_PLANTED:
        problems.append(f"only {planted} planted fault(s); a ruling needs "
                        f"{knowledgelib.CHECKLIST_MIN_PLANTED}")
    verification = None
    if not problems:
        verification = {
            "by": by, "date": today(),
            "note": str(verdicts.get("note") or "").strip()
            or f"independent ruling on a blinded packet of {planted + 1}",
            "digest": digest, "key_sha256": manifest["key_sha256"],
            "planted": {"total": planted, "caught": caught,
                        "kinds": sorted(set(kinds))},
            "basis": {c: basis[c] for c in _classes(cl)}}
    return {"checklist": cl.get("id"),
            "status": "verified" if verification else "not_verified",
            "planted": {"total": planted, "caught": caught}, "missed": missed,
            "real_faults": real_faults, "problems": problems,
            "verification": verification}


def write_verification(path: Path, verification: dict) -> None:
    """Set maturity verified and the verification block in the checklist's
    file - a targeted edit that keeps its comments and prose - and check the
    result counts before keeping it."""
    import researchlib
    path = Path(path)
    before = path.read_text(encoding="utf-8")
    text = researchlib._drop_top_key(before, "verification")
    text = knowledgelib._set_top_key(text, "maturity", "verified")
    text = text.rstrip("\n") + "\n" + yaml.safe_dump(
        {"verification": verification}, sort_keys=False, width=78,
        allow_unicode=False)
    data = yaml.safe_load(text)
    why = knowledgelib.checklist_verification_problem(data)
    if why:
        raise ValueError(f"refused: the written checklist would not count "
                         f"({why})")
    path.write_text(text, encoding="utf-8")


def status(checklists: list[dict], floor: str) -> list[dict]:
    """Each checklist, whether it counts at the floor, and why not."""
    out = []
    for cl in checklists:
        mat = knowledgelib.record_maturity(cl)
        row = {"id": cl.get("id"), "path": cl.get("_path"), "maturity": mat,
               "counts": knowledgelib.checklist_meets_floor(cl, floor)}
        if not row["counts"]:
            row["why_not"] = (knowledgelib.checklist_verification_problem(cl)
                              if mat == "verified" else
                              f"maturity {mat}: needs an independent ruling "
                              "(checklist_verify.py packet / rule) or the "
                              "person's approval")
        out.append(row)
    return out
