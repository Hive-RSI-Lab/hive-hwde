"""state_migrate.py - upgrade state.json v1 -> v2 (T7 freshness schema).

What changes (see state.py's schema docstring for v2):
  * artifacts: {name: "path"} -> {name: {path, kind, sha256, hashed}} - each
    entry is hashed NOW (standard kind names whose path IS the kind's default
    location get that kind; every other name infers a normalizer from its file
    suffix and carries kind: null - a v1 name that merely COLLIDES with a kind
    while pointing elsewhere must not redirect gate-input hashing). A missing
    file hashes to null (kept: the registration itself is information). On the
    next record-gate, hashed kinds auto-register under their kind names and
    reclaim any colliding slot with the typed entry.
  * spawns: [] seeded, plus any v1 `log --event spawn` history events lifted
    into it (the XC-8 first-class ledger); history itself is append-only and
    stays byte-untouched.
  * edits: [] seeded.
  * gates: UNTOUCHED. Pre-v2 results carry no input hashes, so freshness
    reports them "unknown" - honestly unverifiable rather than assumed fresh.
    They become fresh the next time each gate records a result.

Idempotent by construction: a version-2 file is reported and NOT rewritten
(second run is a byte-level no-op). Anything other than v1/v2 is an error.

CLI (SPEC 6):
    state_migrate.py --workspace ~/dev/boards/pd-trigger one workspace
    state_migrate.py --boards-dir ~/dev/boards          every */state.json
Exit 0 = all migrated/already-current, 2 = error.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import checklib  # noqa: E402
import statelib  # noqa: E402
from checklib import CheckError  # noqa: E402

SCRIPT = "state_migrate"


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def migrate_data(data: dict, ws: Path, imap: dict) -> dict:
    """Transform a parsed v1 payload in place; returns a summary dict."""
    ts = now()
    board = data.get("board") or ""
    old_artifacts = data.get("artifacts") or {}
    new_artifacts: dict[str, dict] = {}
    hashed = 0
    for name, val in old_artifacts.items():
        if isinstance(val, dict):           # partial/hand-edited: keep as-is
            new_artifacts[name] = val
            continue
        rel = str(val).replace("\\", "/")
        # A v1 name only becomes a typed kind when its path IS that kind's
        # default location. v1 registries reused kind names for other files
        # (lumina-strobe: "constraints" -> architecture/constraints.json);
        # blessing those as kind overrides would redirect gate-input hashing
        # away from the file the gate actually reads. They keep their name,
        # hash by suffix, and carry kind: null.
        kind = None
        if name in imap["artifact_kinds"]:
            default = imap["artifact_kinds"][name]["path"].replace(
                "{board}", board)
            if rel == default:
                kind = name
        norm = (imap["artifact_kinds"][kind]["norm"] if kind
                else statelib.norm_for_path(ws / rel))
        sha = statelib.hash_artifact(ws / rel, norm)
        if sha is not None:
            hashed += 1
        new_artifacts[name] = {"path": rel, "kind": kind, "sha256": sha,
                               "hashed": ts}
    data["artifacts"] = new_artifacts

    spawns = list(data.get("spawns") or [])
    lifted = 0
    for ev in data.get("history") or []:
        if ev.get("event") == "spawn":
            spawns.append({k: v for k, v in ev.items() if k != "event"})
            lifted += 1
    data["spawns"] = spawns
    data.setdefault("edits", [])
    data["version"] = 2
    data["updated"] = ts
    data.setdefault("history", []).append(
        {"ts": ts, "event": "migrated", "from_version": 1, "to_version": 2})
    return {"artifacts_hashed": hashed, "artifacts_total": len(new_artifacts),
            "spawns_lifted": lifted}


def migrate_file(state_path: Path, imap: dict) -> dict:
    data = checklib.load_json(state_path, "state file")
    version = data.get("version")
    rec: dict = {"state": str(state_path).replace("\\", "/"),
                 "from_version": version}
    if version == 2:
        rec.update(changed=False, already=True)
        return rec
    if version != 1:
        raise CheckError(f"{state_path}: cannot migrate version {version!r} "
                         "(only v1 -> v2)")
    rec.update(migrate_data(data, state_path.parent, imap))
    tmp = state_path.with_name(state_path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, state_path)
    rec.update(changed=True, to_version=2)
    return rec


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    tgt = ap.add_mutually_exclusive_group(required=True)
    tgt.add_argument("--workspace", help="one workspace dir (state.json inside)")
    tgt.add_argument("--boards-dir",
                     help="migrate every <dir>/*/state.json")
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)

    imap = statelib.load_map()
    if args.workspace:
        targets = [Path(args.workspace) / "state.json"]
        if not targets[0].is_file():
            raise CheckError(f"no state.json in {args.workspace}")
    else:
        root = Path(args.boards_dir)
        if not root.is_dir():
            raise CheckError(f"not a directory: {root}")
        targets = sorted(root.glob("*/state.json"))
        if not targets:
            raise CheckError(f"no */state.json under {root}")

    results = [migrate_file(p, imap) for p in targets]
    payload = {
        "script": SCRIPT, "status": "pass",
        "migrated": sum(1 for r in results if r.get("changed")),
        "already_v2": sum(1 for r in results if r.get("already")),
        "workspaces": results,
    }
    return payload, args.out


def main(argv=None) -> int:
    checklib.utf8_stdout()
    try:
        payload, out = run(argv)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001  (spec 6: any error -> exit 2)
        print(json.dumps({"script": SCRIPT, "status": "error",
                          "error": f"{type(exc).__name__}: {exc}"}))
        return 2
    text = json.dumps(payload, indent=1)
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
