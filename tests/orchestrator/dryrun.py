"""dryrun.py - scripted P4->P9 orchestrator dry-run on golden board 1 (S13).

Enacts the SKILL.md phase machine + fix-loop protocol DETERMINISTICALLY (no
LLM): workspace from the blinky2 golden, machine gates in pipeline order with
state.json recording every transition, two mutations injected mid-pipeline,
fixer work orders produced by fix_dispatch.py and executed by a scripted
router fixer (the reference implementation of agents/fixer.md's router
domain), and a kill/resume seam the smoke test exercises by running this
driver twice (--stop-after drc_routed, then plain).

Every gate runs as `gate.py --gate <g> <input> --workspace <ws>` and the driver
asserts the result reached state.json with input hashes before moving on (U16).

Sequence (each step is derived from state.json, so any kill point resumes):
    workspace  copy golden + state init (P4) + artifacts + the P3-exit
               coverage report (knowledge.py --coverage -> log/, U13)
    erc        gate erc on the schematic -> pass -> phase P6
               (P5 setup + P6 placement are pre-satisfied by the golden)
    place      gate place on the board -> pass -> phase P7
    inject_a   narrow a +3V3 segment 0.25 -> 0.05 mm (track_width DRC ERROR)
    drc_routed gate fails -> budget -> dispatch -> fixer (remove-by-uuid +
               re-add at abutting-copper width) -> re-gate pass -> phase P8
    inject_b   the canonical undersized-power-trace neck 0.8 -> 0.16 mm
               (DRC-quiet; IPC-2152 catches it)
    verify     gate fails (check_current undersized_track) -> dispatch ->
               fixer (locate uuid by segment coords) -> re-gate pass
    regate     drc_routed re-run after P8 copper fixes (protocol) -> P9, done

Exit 0 = completed (or cleanly stopped at --stop-after); 2 = error.
All tool calls go through the real CLIs (gate.py / state.py / fix_dispatch.py
/ route_edit.py) with this interpreter, proving the contracts end to end.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / ".claude" / "skills" / "hwde" / "scripts"
GOLDEN = REPO / "tests" / "golden" / "blinky2"
BOARD = "blinky2"
PY = sys.executable

WS_FILES = [f"{BOARD}.kicad_pcb", f"{BOARD}.kicad_sch", f"{BOARD}.kicad_pro",
            "constraints.json", "decoupling.json"]

# Mutation A: a +3V3 branch narrowed below the 0.127 mm DRC floor -> the
# drc_routed gate (P7) fails with a track_width ERROR carrying the item uuid.
MUT_A_OLD = ("\t(segment\n\t\t(start 145.35 121.4)\n"
             "\t\t(end 132.225 121.4)\n\t\t(width 0.25)\n")
MUT_A_NEW = MUT_A_OLD.replace("(width 0.25)", "(width 0.05)")
# Mutation B: the canonical undersized-power-trace neck (S1 mutant strings).
# DRC-quiet (0.16 > 0.127) but far under IPC-2152 for 0.4 A -> verify gate.
MUT_B_OLD = ("\t(segment\n\t\t(start 118.5 106.95)\n"
             "\t\t(end 118.5 110.5)\n\t\t(width 0.8)\n")
MUT_B_NEW = MUT_B_OLD.replace("(width 0.8)", "(width 0.16)")

# (gate -> phase lives in gates.yaml; gate.py stamps it when it records)

SEG_RE = re.compile(
    r"\(segment\s*\(start ([-\d.]+) ([-\d.]+)\)\s*\(end ([-\d.]+) ([-\d.]+)\)"
    r"\s*\(width ([-\d.]+)\)\s*\(layer \"([^\"]+)\"\)\s*\(net \"([^\"]+)\"\)"
    r"\s*\(uuid \"([^\"]+)\"\)")


class DryrunError(RuntimeError):
    pass


def run_cli(script: str, *args: str, ok=(0,), cwd=None) -> tuple[int, str]:
    cmd = [PY, str(SCRIPTS / script), *args]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=cwd or str(REPO))
    if p.returncode not in ok:
        raise DryrunError(
            f"{script} {' '.join(args)} -> exit {p.returncode}\n"
            f"stdout: {p.stdout[-2000:]}\nstderr: {p.stderr[-2000:]}")
    return p.returncode, p.stdout


class Dryrun:
    def __init__(self, ws: Path):
        self.ws = ws
        self.board = ws / "kicad" / f"{BOARD}.kicad_pcb"
        self.sch = ws / "kicad" / f"{BOARD}.kicad_sch"
        self.reports = ws / "reports"

    # ---- state helpers ---------------------------------------------------
    def st(self, *args: str, ok=(0,)) -> dict:
        out = self.reports / "_state_cmd.json"
        run_cli("state.py", *args, "--workspace", str(self.ws),
                "--out", str(out), ok=ok)
        return json.loads(out.read_text(encoding="utf-8"))

    def state(self) -> dict:
        return self.st("show")

    def has_event(self, event: str, **match) -> bool:
        for e in self.state()["history"]:
            if e.get("event") == event and all(
                    e.get(k) == v for k, v in match.items()):
                return True
        return False

    # ---- steps -----------------------------------------------------------
    def ensure_workspace(self) -> None:
        (self.ws / "kicad").mkdir(parents=True, exist_ok=True)
        self.reports.mkdir(parents=True, exist_ok=True)
        for f in WS_FILES:
            shutil.copy2(GOLDEN / f, self.ws / "kicad" / f)
        run_cli("state.py", "init", "--workspace", str(self.ws),
                "--board", BOARD, "--phase", "P4", "--force")
        for name, rel in (("pcb", f"kicad/{BOARD}.kicad_pcb"),
                          ("schematic", f"kicad/{BOARD}.kicad_sch"),
                          ("constraints", "kicad/constraints.json"),
                          ("decoupling", "kicad/decoupling.json")):
            self.st("artifact", "--name", name, "--path", rel)
        self.st("log", "--event", "dryrun_workspace_created",
                "--data", json.dumps({"golden": "blinky2"}))
        # U13: the P3-exit coverage report the full-run recipe emits before
        # P4 (blinky2 declares no blocks -> zero slots + a warning, exit 0;
        # a gap-bearing design would exit 1 - both are report-emitting runs).
        (self.ws / "log").mkdir(exist_ok=True)
        code, _ = run_cli("knowledge.py", "--coverage", "--workspace",
                          str(self.ws), "--phase", "P3", "--out",
                          str(self.ws / "log" / "coverage-P3.json"), ok=(0, 1))
        rep = json.loads((self.ws / "log" / "coverage-P3.json").read_text(
            encoding="utf-8"))
        self.st("log", "--event", "coverage_reported", "--data",
                json.dumps({"phase": "P3", "exit": code,
                            "summary": rep["summary"]}))

    def inject(self, name: str, old: str, new: str) -> None:
        if self.has_event("mutation_injected", name=name):
            return
        text = self.board.read_text(encoding="utf-8")
        n = text.count(old)
        if n != 1:
            raise DryrunError(f"mutation {name}: target text found {n}x "
                              "(golden drifted?)")
        self.board.write_text(text.replace(old, new, 1), encoding="utf-8")
        self.st("log", "--event", "mutation_injected",
                "--data", json.dumps({"name": name}))
        print(f"dryrun: injected mutation {name}")

    def run_gate(self, gate: str) -> tuple[int, Path]:
        target = self.sch if gate == "erc" else self.board
        attempt = self.state()["gates"].get(gate, {}).get("attempts", 0) + 1
        rep = self.reports / f"gate-{gate}-a{attempt}.json"
        # U16: the gate records itself into the workspace state.json - the
        # recipe's one gate form. A separate record step is exactly what
        # bb-buck's run skipped at every phase.
        code, _ = run_cli("gate.py", "--gate", gate, str(target),
                          "--workspace", str(self.ws),
                          "--out", str(rep), ok=(0, 1))
        recorded = self.state()["gates"].get(gate, {})
        if recorded.get("attempts") != attempt \
                or not (recorded.get("last") or {}).get("inputs"):
            raise DryrunError(
                f"gate {gate} attempt {attempt} did not reach state.json "
                f"with input hashes (got {recorded.get('attempts')})")
        print(f"dryrun: gate {gate} attempt {attempt} -> "
              f"{'PASS' if code == 0 else 'FAIL'}")
        return code, rep

    def gate_with_fixes(self, gate: str) -> None:
        for _ in range(4):  # 1 + fix_loops budget (3)
            code, rep = self.run_gate(gate)
            if code == 0:
                return
            self.st("budget", "--path", f"fix_loops.{gate}", "--consume")
            disp = self.reports / rep.name.replace("gate-", "dispatch-")
            rc, _ = run_cli("fix_dispatch.py", "--input", str(rep),
                            "--board", str(self.board),
                            "--state", str(self.ws / "state.json"),
                            "--out", str(disp), ok=(1,))
            summary = json.loads(disp.read_text(encoding="utf-8"))
            for o in summary["orders"]:
                self.fix_order(o)
        raise DryrunError(f"gate {gate} still failing after fix loops")

    def fix_order(self, o: dict) -> None:
        wo = json.loads(Path(o["work_order"]).read_text(encoding="utf-8"))
        if wo["fixer"] != "router":
            raise DryrunError(f"dry-run only enacts router fixers, "
                              f"got {wo['fixer']} (order {o['id']})")
        oid = str(o["id"])
        self.st("issue", "--id", oid, "--status", "fixing",
                "--agent", "dryrun-fixer", "--bump-attempts")
        self.st("snapshot", "--label", f"pre-fix-{oid}",
                "--files", f"kicad/{BOARD}.kicad_pcb")
        ops = self.router_ops(wo)
        ops_file = self.reports / f"fix-{oid}.ops.json"
        ops_file.write_text(json.dumps({"version": 1, "ops": ops}, indent=1),
                            encoding="utf-8")
        run_cli("route_edit.py", "--pcb", str(self.board),
                "--ops", str(ops_file))
        self.st("issue", "--id", oid, "--status", "fixed")
        print(f"dryrun: issue {oid} fixed via route_edit ({len(ops)} ops)")

    def router_ops(self, wo: dict) -> list[dict]:
        """The reference router fixer: for each violation, locate the exact
        offending segment (by DRC item uuid, else by the check's segment
        coordinates), remove it, and re-add it at the width of the same-net
        copper abutting its endpoints (never below the check's required
        minimum)."""
        segs = [
            {"start": (float(m[0]), float(m[1])),
             "end": (float(m[2]), float(m[3])), "width": float(m[4]),
             "layer": m[5], "net": m[6], "uuid": m[7]}
            for m in SEG_RE.findall(self.board.read_text(encoding="utf-8"))]

        def close(a, b, tol=1e-3):
            return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol

        def pt_seg_dist(p, a, b):
            ax, ay = a
            vx, vy = b[0] - ax, b[1] - ay
            L2 = vx * vx + vy * vy
            t = 0.0 if L2 == 0 else max(
                0.0, min(1.0, ((p[0] - ax) * vx + (p[1] - ay) * vy) / L2))
            cx, cy = ax + t * vx, ay + t * vy
            return ((p[0] - cx) ** 2 + (p[1] - cy) ** 2) ** 0.5

        ops: list[dict] = []
        for v in wo["cluster"]["violations"]:
            target = None
            uuid = ((v.get("items") or [{}])[0] or {}).get("uuid")
            if uuid:
                target = next((s for s in segs if s["uuid"] == uuid), None)
            if target is None and v.get("segment"):
                vs = tuple(v["segment"]["start"])
                ve = tuple(v["segment"]["end"])
                target = next(
                    (s for s in segs
                     if (close(s["start"], vs) and close(s["end"], ve))
                     or (close(s["start"], ve) and close(s["end"], vs))),
                    None)
            if target is None:
                raise DryrunError(f"fixer: cannot locate segment for "
                                  f"violation {v.get('msg')!r}")
            cands = []
            for s in segs:
                if s["uuid"] == target["uuid"] or s["net"] != target["net"]:
                    continue
                if any(pt_seg_dist(p, s["start"], s["end"])
                       <= s["width"] / 2 + 0.01
                       for p in (target["start"], target["end"])):
                    cands.append(s["width"])
            required = float(v.get("required_mm") or 0.0)
            width = max([*cands, required])
            if width <= 0:
                raise DryrunError("fixer: no abutting copper and no required "
                                  "width - cannot pick a repair width")
            ops.append({"op": "remove", "uuid": target["uuid"]})
            ops.append({"op": "add_track",
                        "start": list(target["start"]),
                        "end": list(target["end"]), "width": width,
                        "layer": target["layer"], "net": target["net"]})
        return ops

    # ---- the machine -----------------------------------------------------
    def steps(self):
        return [
            ("erc",
             lambda st: st["gates"].get("erc", {}).get("status") == "pass",
             self.step_erc),
            ("place",
             lambda st: st["gates"].get("place", {}).get("status") == "pass",
             self.step_place),
            ("inject_a",
             lambda st: self.has_event("mutation_injected", name="A"),
             lambda: self.inject("A", MUT_A_OLD, MUT_A_NEW)),
            ("drc_routed",
             lambda st: st["gates"].get("drc_routed", {}).get("status")
             == "pass",
             self.step_drc_routed),
            ("inject_b",
             lambda st: self.has_event("mutation_injected", name="B"),
             lambda: self.inject("B", MUT_B_OLD, MUT_B_NEW)),
            ("verify",
             lambda st: st["gates"].get("verify", {}).get("status") == "pass",
             lambda: self.gate_with_fixes("verify")),
            ("regate",
             lambda st: self.has_event("regate_drc_routed"),
             self.step_regate),
        ]

    def step_erc(self):
        self.gate_with_fixes("erc")
        self.st("log", "--event", "phases_pre_satisfied", "--data",
                json.dumps({"phases": ["P5", "P6"],
                            "reason": "golden fixture is set up and placed"}))
        self.st("set-phase", "--phase", "P6")

    def step_place(self):
        self.gate_with_fixes("place")
        self.st("set-phase", "--phase", "P7")

    def step_drc_routed(self):
        self.gate_with_fixes("drc_routed")
        self.st("set-phase", "--phase", "P8")

    def step_regate(self):
        code, _ = self.run_gate("drc_routed")
        if code != 0:
            raise DryrunError("post-P8-fix drc_routed regression")
        self.st("log", "--event", "regate_drc_routed")
        self.st("set-phase", "--phase", "P9")
        self.st("log", "--event", "dryrun_complete")

    def run(self, stop_after: str | None, reset: bool) -> int:
        if reset and self.ws.exists():
            shutil.rmtree(self.ws)
        if not (self.ws / "state.json").exists():
            self.ensure_workspace()
            print("dryrun: workspace created")
        else:
            summary = self.st("resume")
            self.st("log", "--event", "resumed", "--data",
                    json.dumps({"phase": summary["phase"],
                                "next_gate": summary["next_gate"]}))
            print(f"dryrun: resumed at phase {summary['phase']}, "
                  f"next gate {summary['next_gate']}")
        for name, done, action in self.steps():
            if done(self.state()):
                continue
            action()
            if stop_after == name:
                print(f"dryrun: stopped after step {name} (simulated kill)")
                return 0
        print("dryrun: complete (P4->P9 + regate)")
        return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--reset", action="store_true",
                    help="delete the workspace and start fresh")
    ap.add_argument("--stop-after",
                    choices=[s for s in ("erc", "place", "inject_a",
                                         "drc_routed", "inject_b", "verify",
                                         "regate")],
                    help="exit right after this step (simulated kill)")
    args = ap.parse_args(argv)
    try:
        return Dryrun(Path(args.workspace)).run(args.stop_after, args.reset)
    except DryrunError as exc:
        print(f"dryrun: ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
