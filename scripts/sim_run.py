#!/usr/bin/env python
"""sim_run.py - SPICE testbench runner + bounds gate (the `sim` gate).

Advisory-with-explicit-gate behavioral verification: the sim-analyst agent
authors testbench `.cir` files (P2 feasibility / P4 value-verification) into
the workspace `kicad/sims/`, each with a `<name>.bounds.json` sidecar - a
JSON LIST of {measure, min?, max?, severity: error|warning, msg?}. This
script runs every testbench through the shared-library ngspice that KiCad
bundles (lib/simlib.py holds the machine-verified engine recipe) and gates
the measured values against the bounds. It catches the wrong-value defect
class (a 47k where 4.7k belongs) that ERC/DRC/verify/DFM are all blind to.

Mixed-signal benches: XSPICE `A` devices (d_and, d_dff, adc_bridge,
dac_bridge, ...) work, since simlib loads the code models KiCad ships beside
its libngspice. A Verilog-A model comes in with `.control` / `pre_osdi
FILE` / `.endc`; FILE is relative to the bench, and a `.va` is compiled with
the IIC tree's OpenVAF first (docs/solvers.md).

Each testbench runs in its OWN worker subprocess (this script re-invoked with
the internal --exec-one mode): a wedged or crashed engine is killed at
--timeout and becomes a structured sim_engine_error violation, never a
traceback and never a lost gate run for the other benches.

Violation kinds (S2 normalized schema via checklib, source sim.ngspice):
  sim_bound_fail       measured value outside the sidecar window
  sim_measure_missing  a bound's measure was not produced (analysis or
                       .measure failed) - severity of the bound itself
  sim_engine_error     bench could not run (engine crash/timeout, missing or
                       invalid bounds sidecar) - always error severity
  sim_tolerance_fail   the sidecar's tolerance analysis (wca: EVA band;
                       corners: every vertex) carries a measure outside
                       its bound - severity of the bound
  sim_yield_low        Monte Carlo yield under the sidecar's min_yield

Tolerance analysis (#495): a sidecar object {"bounds": [...], "tolerance":
{"analysis": "mc"|"corners"|"wca", "n", "seed", ...}} also varies the
deck's R/C/L values and tolerance-named .params (lib/simtol.py: sources
and defaults; lib/tolerance.py: EVA, RSS, corners, Monte Carlo and the
top-3 sensitivities), and reports them under testbenches[].tolerance.

Exit 0 pass / 1 violations / 2 error (SPEC.md section 6). Whole-run failures
(no ngspice library, no testbenches, bad CLI input) are exit-2 error JSON.

CLI:
  sim_run.py --dir sims/ [--timeout 60] [--out report.json]
  sim_run.py --testbench tb.cir [--timeout 60] [--out report.json]
  sim_run.py --list --dir sims/            (enumerate benches+bounds, no engine)
  sim_run.py --fragment --net board.net --refs R6,R7 [--nets /LED] [--out f.json]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import checklib  # noqa: E402
import simlib  # noqa: E402
import simtol  # noqa: E402
import tolerance  # noqa: E402
from lib import env  # noqa: E402

SCRIPT = "sim_run"
WORKER_GRACE_S = 15  # interpreter+DLL startup allowance on top of --timeout


def discover(input_path: Path) -> list[Path]:
    """Testbenches for a run: the single --testbench file, or every *.cir
    directly under --dir (sorted; sidecars are never testbenches)."""
    if input_path.is_file():
        return [input_path]
    if input_path.is_dir():
        benches = sorted(p for p in input_path.glob("*.cir") if p.is_file())
        if not benches:
            raise checklib.CheckError(f"no *.cir testbenches in {input_path}")
        return benches
    raise checklib.CheckError(f"input not found: {input_path}")


def bounds_path(cir: Path) -> Path:
    return cir.with_name(cir.stem + ".bounds.json")


def _run_worker(cir: Path, dll: Path, timeout: float) -> dict:
    """One testbench in a killable subprocess -> the worker's result dict.
    Every failure mode (timeout, crash, garbage output) comes back as a
    {"status": ...} dict; this function never raises."""
    cmd = [sys.executable, str(Path(__file__).resolve()),
           "--exec-one", str(cir), "--dll", str(dll)]
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True,
                            encoding="utf-8", errors="replace",
                            timeout=timeout + WORKER_GRACE_S)
    except subprocess.TimeoutExpired:
        return {"status": "timeout",
                "error": f"engine timeout after {timeout:g} s (killed)"}
    except OSError as exc:
        return {"status": "error", "error": f"cannot spawn worker: {exc}"}
    try:
        res = json.loads(cp.stdout)
        if not isinstance(res, dict) or "status" not in res:
            raise ValueError("no status")
        return res
    except (ValueError, json.JSONDecodeError):
        tail = (cp.stderr or cp.stdout or "").strip()[-400:]
        return {"status": "error",
                "error": f"worker died (exit {cp.returncode}): "
                         f"{tail or 'no output'}"}


def run(input_path: Path, timeout: float = 60.0) -> dict:
    """Run testbenches and gate them against their bounds sidecars.
    Library entry for gate.py (tool: sim); input is a sims dir or one .cir."""
    dll = env.find_ngspice_dll()  # EnvError (bad pin) propagates -> exit 2
    if dll is None:
        raise checklib.CheckError(
            "no ngspice shared library found - install KiCad 10 with its "
            "simulator component or set HWDE_NGSPICE_DLL")
    benches = discover(Path(input_path))
    vios: list[dict] = []
    facts: list[dict] = []
    version = None
    for cir in benches:
        entry: dict = {"name": cir.stem, "cir": str(cir)}
        facts.append(entry)
        bp = bounds_path(cir)
        if not bp.exists():
            vios.append(simlib.engine_error(
                cir.name, f"no bounds sidecar {bp.name} - an ungated "
                          "testbench proves nothing"))
            entry["status"] = "error"
            continue
        try:
            bounds, tcfg = simlib.load_sidecar(bp)
        except checklib.CheckError as exc:
            vios.append(simlib.engine_error(cir.name, str(exc)))
            entry["status"] = "error"
            continue
        entry["bounds"] = len(bounds)
        t0 = time.monotonic()
        res = _run_worker(cir, dll, timeout)
        entry["elapsed_s"] = checklib.rnd(time.monotonic() - t0, 3)
        if res.get("status") != "ok":
            vios.append(simlib.engine_error(
                cir.name, res.get("error", "unknown engine failure"),
                worker_status=res.get("status")))
            entry["status"] = "error"
            continue
        version = res.get("ngspice_version", version)
        entry["measures"] = res.get("measures", {})
        bvios = simlib.compare_bounds(bounds, entry["measures"], cir.name,
                                      failed_measures=res.get("failed_measures"))
        vios.extend(bvios)
        if tcfg is not None:
            tvios = run_tolerance(cir, bounds, tcfg, dll, timeout,
                                  entry["elapsed_s"], entry)
            bvios = bvios + tvios
            vios.extend(tvios)
        entry["status"] = "violations" if bvios else "pass"
    return checklib.report(
        SCRIPT, input_path, vios, input=str(input_path),
        engine={"dll": str(dll), "ngspice_version": version},
        timeout_s=timeout, testbenches=facts)


BATCH = 250  # variants per worker process


def _run_batch(circuits: list[str], dll: Path, timeout: float) -> dict:
    """Variants in killable workers of BATCH decks each -> {"runs": [...]}
    or {"error"}; never raises."""
    import tempfile
    runs: list[dict] = []
    version = None
    for i in range(0, len(circuits), BATCH):
        chunk = circuits[i:i + BATCH]
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as fh:
            json.dump(chunk, fh)
            tmp = Path(fh.name)
        cmd = [sys.executable, str(Path(__file__).resolve()),
               "--exec-many", str(tmp), "--dll", str(dll)]
        try:
            cp = subprocess.run(cmd, capture_output=True, text=True,
                                encoding="utf-8", errors="replace",
                                timeout=timeout + WORKER_GRACE_S)
        except subprocess.TimeoutExpired:
            return {"error": f"tolerance batch timeout after {timeout:g} s "
                             "(killed)"}
        finally:
            tmp.unlink(missing_ok=True)
        try:
            res = json.loads(cp.stdout)
        except json.JSONDecodeError:
            tail = (cp.stderr or cp.stdout or "").strip()[-400:]
            return {"error": f"tolerance worker died (exit {cp.returncode}): "
                             f"{tail or 'no output'}"}
        if res.get("status") != "ok":
            return {"error": res.get("error", "tolerance worker failed")}
        version = res.get("ngspice_version", version)
        runs.extend(res["runs"])
    return {"runs": runs, "ngspice_version": version}


def run_tolerance(cir: Path, bounds: list[dict], cfg: dict, dll: Path,
                  timeout: float, nominal_s: float, entry: dict) -> list[dict]:
    """The sidecar's tolerance analysis of one bench (lib/simtol.py): every
    variant deck through ngspice in batch workers, the band (wca/corners)
    or the yield (mc) gated against the same bounds. Facts land in
    entry["tolerance"]; returns the violations."""
    text = simlib.prepare_circuit(cir.read_text(encoding="utf-8",
                                                errors="replace"))
    try:
        params, info, assumed = simtol.build_params(text, cfg)
    except ValueError as exc:
        return [simlib.engine_error(cir.name, f"tolerance: {exc}")]
    if not params:
        return [simlib.engine_error(cir.name, "tolerance: nothing to vary")]
    failure: dict = {}

    def batch(points):
        circuits = [simtol.render(text, info, p) for p in points]
        # the bench's own timeout, plus its nominal run time for each deck
        budget = timeout + 3 * max(nominal_s, 0.05) * min(len(circuits),
                                                          BATCH)
        res = _run_batch(circuits, dll, budget)
        if "error" in res:
            failure.setdefault("error", res["error"])
            return [{} for _ in points]
        return [r.get("measures", {}) for r in res["runs"]]

    t0 = time.monotonic()
    out = tolerance.analyse(params, analysis=cfg["analysis"], n=cfg["n"],
                            seed=cfg["seed"], bounds=bounds, batch=batch)
    if failure:
        return [simlib.engine_error(cir.name, failure["error"])]
    if not out["nominal"]:
        return [simlib.engine_error(
            cir.name, "tolerance: the nominal variant measured nothing")]
    out.pop("samples", None)
    out["elapsed_s"] = checklib.rnd(time.monotonic() - t0, 3)
    if assumed:
        out["assumed"] = assumed
    entry["tolerance"] = out
    vios = []
    for b, edge, v in tolerance.band_violations(bounds, out):
        if cfg["analysis"] == "mc":
            continue
        name = b["measure"]
        tops = ", ".join(t["param"] for t in out["top3"].get(name.lower(), []))
        note = f" - {b['msg']}" if b.get("msg") else ""
        vios.append(checklib.violation(
            simlib.CHECK, b.get("severity", "error"), None, None, None, [],
            f"{cir.name}: {name} reaches {v:g} at the tolerance "
            f"{'corners' if cfg['analysis'] == 'corners' else 'worst case'}"
            f", outside {simlib._fmt_bound(b)} (top: {tops}){note}",
            simlib.SOURCE, kind="sim_tolerance_fail", measure=name,
            testbench=cir.name, analysis=cfg["analysis"], edge=edge,
            value=v, bound={"min": b.get("min"), "max": b.get("max")}))
    if cfg["analysis"] == "mc":
        y = out.get("yield", {})
        if y.get("all", 1.0) < cfg["min_yield"]:
            sev = "error" if any(b.get("severity", "error") == "error"
                                 and y["per_measure"].get(b["measure"], 1)
                                 < cfg["min_yield"] for b in bounds) \
                else "warning"
            worst = sorted(y["per_measure"].items(), key=lambda kv: kv[1])
            vios.append(checklib.violation(
                simlib.CHECK, sev, None, None, None, [],
                f"{cir.name}: Monte Carlo yield {y['all']:.1%} over "
                f"n={cfg['n']} (seed {cfg['seed']}) is under "
                f"{cfg['min_yield']:.1%}; worst {worst[0][0]} "
                f"{worst[0][1]:.1%}", simlib.SOURCE,
                kind="sim_yield_low", testbench=cir.name,
                analysis="mc", yield_all=y["all"],
                per_measure=y["per_measure"]))
    return vios


def run_list(input_path: Path) -> dict:
    """--list: enumerate testbenches + sidecar bounds without any engine."""
    benches = discover(Path(input_path))
    out = []
    for cir in benches:
        bp = bounds_path(cir)
        entry: dict = {"name": cir.stem, "cir": str(cir),
                       "bounds_file": str(bp), "has_bounds": bp.exists()}
        if bp.exists():
            try:
                entry["bounds"], tcfg = simlib.load_sidecar(bp)
                if tcfg is not None:
                    entry["tolerance"] = tcfg
            except checklib.CheckError as exc:
                entry["bounds_error"] = str(exc)
        out.append(entry)
    return {"script": SCRIPT, "status": "pass", "mode": "list",
            "input": str(input_path), "testbenches": out}


def run_fragment(net: Path, refs: list[str], nets: list[str]) -> dict:
    """--fragment: SPICE element lines + rename map from a kicadsexpr .net
    (debug CLI for simlib.synth_fragment; the sim-analyst composes
    testbenches around the same library call)."""
    parsed = simlib.parse_netlist(net)
    frag = simlib.synth_fragment(parsed, refs=refs, nets=nets)
    return {"script": SCRIPT, "status": "pass", "mode": "fragment",
            "netlist": str(net), **frag}


def exec_many(batch_json: Path, dll: Path) -> dict:
    """Internal worker: run a JSON list of prepared circuits in one engine."""
    circuits = json.loads(batch_json.read_text(encoding="utf-8"))
    simlib.setup_engine_env(dll)
    return simlib.run_circuits(circuits)


def exec_one(cir: Path, dll: Path) -> dict:
    """Internal worker: run ONE testbench in this process and return the raw
    engine result (measures etc.) - crash isolation and the kill switch live
    in the parent."""
    t0 = time.monotonic()
    text = cir.read_text(encoding="utf-8", errors="replace")
    circuit = simlib.prepare_circuit(text)
    try:
        circuit = simlib.resolve_osdi(circuit, cir.resolve().parent)
    except Exception as exc:  # no tree / openvaf failed: this bench only
        return {"status": "error",
                "error": f"pre_osdi: {type(exc).__name__}: {exc}"}
    simlib.setup_engine_env(dll)
    res = simlib.run_circuit(circuit)
    res["elapsed_s"] = checklib.rnd(time.monotonic() - t0, 3)
    return res


def main(argv: list[str] | None = None) -> int:
    checklib.utf8_stdout()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", help="run every *.cir in this directory")
    ap.add_argument("--testbench", help="run one .cir testbench")
    ap.add_argument("--timeout", type=float, default=60.0,
                    help="per-testbench engine timeout in seconds (default 60)")
    ap.add_argument("--list", action="store_true",
                    help="enumerate testbenches + bounds, do not run")
    ap.add_argument("--fragment", action="store_true",
                    help="synthesize SPICE fragment lines from --net")
    ap.add_argument("--net", help="kicadsexpr netlist for --fragment")
    ap.add_argument("--refs", help="comma list of refs for --fragment")
    ap.add_argument("--nets", help="comma list of net names for --fragment")
    ap.add_argument("--out", help="write JSON report here instead of stdout")
    ap.add_argument("--exec-one", help=argparse.SUPPRESS)  # internal worker
    ap.add_argument("--dll", help=argparse.SUPPRESS)       # internal worker
    ap.add_argument("--exec-many", help=argparse.SUPPRESS)  # internal worker
    args = ap.parse_args(argv)

    if args.exec_many:
        try:
            res = exec_many(Path(args.exec_many), Path(args.dll))
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"status": "error",
                              "error": f"{type(exc).__name__}: {exc}"}))
            return 2
        print(json.dumps(res))
        return 0 if res.get("status") == "ok" else 2

    if args.exec_one:  # worker: raw result JSON, exit 0 ran / 2 crashed
        try:
            res = exec_one(Path(args.exec_one), Path(args.dll))
        except Exception as exc:  # noqa: BLE001 (never a traceback)
            print(json.dumps({"status": "error",
                              "error": f"{type(exc).__name__}: {exc}"}))
            return 2
        print(json.dumps(res, indent=1))
        return 0 if res.get("status") == "ok" else 2

    def fn():
        if args.fragment:
            if not args.net:
                raise checklib.CheckError("--fragment needs --net")
            refs = [r for r in (args.refs or "").split(",") if r]
            nets = [n for n in (args.nets or "").split(",") if n]
            return run_fragment(Path(args.net), refs, nets), args.out
        if not args.dir and not args.testbench:
            raise checklib.CheckError("give --dir or --testbench (or --fragment)")
        target = Path(args.dir or args.testbench)
        if args.list:
            return run_list(target), args.out
        return run(target, timeout=args.timeout), args.out

    return checklib.cli_wrap(SCRIPT, fn)


if __name__ == "__main__":
    raise SystemExit(main())
