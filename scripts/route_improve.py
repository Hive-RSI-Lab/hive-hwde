"""route_improve - re-route the worst-routed nets until the layout stops getting better.

A routed, DRC-clean board can still be a poor layout (route_quality.py: long
detours, needless vias, zig-zags). A person steering a router fixes that in
rounds - "that one is too long, rip it and try again" - and stops when another
round no longer helps. This is those rounds without the person:

  1. Score the board (route_quality.score_board): every routed net against
     its shortest 45-degree route, rolled up per net class.
  2. Take the next BATCH nets that waste the most (cost - ideal mm) among the
     classes it may touch, and that no earlier round tried.
  3. Rip those nets' tracks and vias (route_edit `rip` by net), refill, DRC,
     and route them again with route_finish's maze router (finish_staged,
     only those nets, never ripping another net's copper). Its A* lays the
     shortest legal path it finds on the board as it is now - other nets
     have moved since the autorouter laid the first one. A net whose new
     route is no better (or not whole) gets its old copper back (route_edit
     rip + copy_from the pre-round copy) while the others keep theirs -
     unless that old copper now runs into the new routes (DRC grows), when
     the round is judged as routed.
  4. Keep the round only if nothing is left unconnected that was connected,
     no DRC error or warning type grows, and the board's quality score
     rises. Else restore the board as it was before the round; its nets
     stay tried.
  5. Stop when --patience rounds in a row gained less than --min-gain (kept
     or not: it stops improving), when no candidate is left, or at
     --rounds.

Classes it touches by default: signal, power, high_speed. diff_pair,
matched and rf nets are left alone (--classes adds them): their geometry is
intent - a coupled pair, a meander to a length target, an impedance trace -
which the maze router does not reproduce. Plane nets are never touched.
--keep-net NET (repeatable) protects a net by name.

Everything runs on a staged copy (route_auto's staging: the board with its
.kicad_pro/.kicad_dru/.kicad_sch); the board is swapped in only when a
round was kept, and never with --dry-run.

Contract:
  route_improve.py --pcb B.kicad_pcb [--constraints c.json]
      [--classes signal,power,high_speed] [--keep-net NET ...]
      [--batch 4] [--rounds 12] [--min-gain 0.002] [--patience 2]
      [--below 0.9] [--grid 0.1] [--dry-run] [--work-dir DIR]
      [--out-report r.json] [--verbose]
  Facts: score_before/score_after, classes_before/classes_after (per-class
  scores), rounds [{nets, kept, restored, score_before, score_after, reason}],
  stopped (why it stopped), kept_rounds.
  exit 0 = ran (kept or not; the board is never worse); 2 = error (board
  untouched).
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import checklib  # noqa: E402
import env  # noqa: E402
import geom  # noqa: E402
import kc  # noqa: E402
import routelib  # noqa: E402
import route_quality as rq  # noqa: E402
from checklib import CheckError  # noqa: E402

SCRIPT = "route_improve"
DEFAULT_CLASSES = ("signal", "power", "high_speed")


def issue_counts(drc: dict) -> dict[str, int]:
    """DRC errors and warnings by source:check, unconnected aside."""
    out: dict[str, int] = {}
    for v in drc["violations"]:
        if v.get("source") == "unconnected":
            continue
        if v.get("severity") in ("error", "warning"):
            key = f"{v.get('source')}:{v.get('check')}"
            out[key] = out.get(key, 0) + 1
    return out


def unconnected(drc: dict) -> int:
    return sum(1 for v in drc["violations"] if v.get("source") == "unconnected")


def judge_round(before_drc: dict, after_drc: dict, q_before: float,
                q_after: float) -> str | None:
    """None when the round is kept, else why not."""
    if unconnected(after_drc) > unconnected(before_drc):
        return (f"unconnected {unconnected(before_drc)} -> "
                f"{unconnected(after_drc)}")
    b, a = issue_counts(before_drc), issue_counts(after_drc)
    grew = sorted(k for k in a if a[k] > b.get(k, 0))
    if grew:
        return "DRC grew: " + ", ".join(f"{k} {b.get(k, 0)}->{a[k]}" for k in grew)
    if q_after <= q_before:
        return f"quality {q_before:.4f} -> {q_after:.4f} (no gain)"
    return None


def not_better(q: dict, q2: dict, after: dict, nets) -> list[str]:
    """The round's nets whose new route is no better than the old: quality
    not higher (None: no longer judged, so likely not whole) or something
    of the net left unconnected."""
    left = {v.get("net") for v in after["violations"]
            if v.get("source") == "unconnected"}
    out = []
    for n in nets:
        old = q["nets"][n]["quality"]
        new = q2["nets"].get(n, {}).get("quality")
        if n in left or new is None or new <= old:
            out.append(n)
    return out


def candidates(q: dict, classes, keep: set, tried: set, below: float,
               batch: int) -> list[str]:
    return [n for n in rq.worst_nets(q, classes=classes, below=below)
            if n not in keep and n not in tried][:batch]


def _score(staged: Path, cons, pro) -> dict:
    return rq.score_board(geom.BoardGeom.from_file(staged), cons, pro)


def _class_scores(q: dict) -> dict:
    return {c: r["score"] for c, r in q["classes"].items()}


def improve_staged(cli: Path, staged: Path, cons, pro, *, classes,
                   keep=(), batch: int = 4, rounds: int = 12,
                   min_gain: float = 0.002, patience: int = 2,
                   below: float = 0.9, grid: float = 0.1, parity: bool = False,
                   refill: bool = True, log=None,
                   reroute=None, restore=None) -> dict:
    """The rounds on a staged board, in place. `reroute(staged, drc, nets)`
    returns the DRC after the nets were ripped and routed again (the default
    rips by net and runs route_finish); `restore(staged, backup, nets)`
    puts those nets' copper back from the backup and returns the DRC after
    (the default: route_edit rip + copy_from). Tests pass their own."""
    import route_edit

    def _drc():
        if refill:
            kc.run_drc(cli, staged, refill=True, save_board=True)
        return kc.run_drc(cli, staged, parity=parity, all_track_errors=True)

    if restore is None:
        def restore(staged, backup, nets):
            route_edit.apply_ops(staged, [
                op for n in nets
                for op in ({"op": "rip", "net": n},
                           {"op": "copy_from", "pcb": str(backup), "net": n})])
            return _drc()

    if reroute is None:
        import route_finish

        def reroute(staged, drc, nets):
            route_edit.apply_ops(staged, [{"op": "rip", "net": n} for n in nets])
            ripped = _drc()
            f = route_finish.finish_staged(
                cli, staged, ripped, nets=nets, grid=grid, parity=parity,
                refill=refill, rip_board=False, log=log)
            return f.get("drc") or ripped

    drc = _drc()
    q = _score(staged, cons, pro)
    facts = {"score_before": q["score"], "classes_before": _class_scores(q),
             "unconnected_before": unconnected(drc),
             "issues_before": issue_counts(drc), "rounds": [],
             "classes_touched": list(classes)}
    backup = staged.with_name("preimprove_" + staged.name)
    routed = staged.with_name("rerouted_" + staged.name)
    tried: set = set()
    keep = set(keep)
    flat = 0
    stopped = f"rounds ({rounds})"
    for i in range(rounds):
        nets = candidates(q, classes, keep, tried, below, batch)
        if not nets:
            stopped = "no candidate left"
            break
        tried.update(nets)
        shutil.copy2(staged, backup)
        row = {"round": i + 1, "nets": nets, "score_before": q["score"],
               "waste_mm": checklib.rnd(sum(
                   q["nets"][n]["cost_mm"] - q["nets"][n]["ideal_mm"]
                   for n in nets), 2)}
        try:
            after = reroute(staged, drc, nets)
            q2 = _score(staged, cons, pro)
            worse = not_better(q, q2, after, nets)
            if worse and len(worse) < len(nets):
                # keep the nets that got better, put the rest back as they
                # were - unless their old copper now runs into the new
                # routes, then the round is judged as it was routed
                shutil.copy2(staged, routed)
                try:
                    back = restore(staged, backup, worse)
                    clash = judge_round(drc, back, 0.0, 1.0)
                except CheckError as exc:
                    back, clash = None, str(exc)[:200]
                if clash is None:
                    after, q2 = back, _score(staged, cons, pro)
                    row["restored"] = worse
                else:
                    shutil.copy2(routed, staged)
                    row["restore_skipped"] = f"{worse}: {clash}"
            why = judge_round(drc, after, q["score"] or 0.0,
                              q2["score"] or 0.0)
        except CheckError as exc:
            after, q2, why = None, None, f"reroute failed: {str(exc)[:200]}"
        row["score_after"] = q2["score"] if q2 else None
        if why is None:
            gain = (q2["score"] or 0.0) - (q["score"] or 0.0)
            row.update(kept=True, gain=checklib.rnd(gain, 4))
            drc, q = after, q2
            flat = 0 if gain >= min_gain else flat + 1
        else:
            shutil.copy2(backup, staged)
            row.update(kept=False, reason=why)
            flat += 1
        if log:
            log(f"round {i + 1}: {nets} kept={row['kept']} "
                f"{row['score_before']} -> {row['score_after']}"
                + ("" if row["kept"] else f" ({row['reason']})"))
        facts["rounds"].append(row)
        if flat >= patience:
            stopped = (f"stopped improving: {flat} round(s) in a row under "
                       f"+{min_gain}")
            break
    backup.unlink(missing_ok=True)
    routed.unlink(missing_ok=True)
    facts.update(score_after=q["score"], classes_after=_class_scores(q),
                 unconnected_after=unconnected(drc),
                 issues_after=issue_counts(drc), stopped=stopped,
                 kept_rounds=sum(r["kept"] for r in facts["rounds"]),
                 drc=drc)
    return facts


def run(argv: list[str] | None = None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pcb", required=True)
    ap.add_argument("--constraints", default=None)
    ap.add_argument("--classes", default=",".join(DEFAULT_CLASSES),
                    help="net classes it may re-route (route_quality's names)")
    ap.add_argument("--keep-net", action="append", default=[],
                    help="never re-route this net (repeatable)")
    ap.add_argument("--batch", type=int, default=4,
                    help="nets ripped and routed again per round")
    ap.add_argument("--rounds", type=int, default=12)
    ap.add_argument("--min-gain", type=float, default=0.002,
                    help="board quality a round must add to count as a gain")
    ap.add_argument("--patience", type=int, default=2,
                    help="stop after this many rounds in a row without a gain")
    ap.add_argument("--below", type=float, default=0.9,
                    help="only nets whose quality is under this")
    ap.add_argument("--grid", type=float, default=0.1)
    ap.add_argument("--dry-run", action="store_true",
                    help="run the rounds on the staged copy, leave the board")
    ap.add_argument("--work-dir", default=None)
    ap.add_argument("--out-report", default=None)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    pcb = Path(args.pcb).resolve()
    if not pcb.is_file():
        raise CheckError(f"board not found: {pcb}")
    classes = [c for c in args.classes.split(",") if c]
    bad = [c for c in classes if c not in rq.CLASSES or c == "plane"]
    if bad:
        raise CheckError(f"unknown or untouchable class(es): {bad}")
    cli = env.find_kicad_cli()
    if cli is None:
        raise CheckError("kicad-cli not found (check_env.py)")
    import route_auto
    cons, pro = rq.load_context(pcb, args.constraints)
    work = routelib.fresh_work_dir(Path(args.work_dir).resolve()
                                   if args.work_dir
                                   else pcb.parent / "route_improve")
    staged = route_auto._stage_board(pcb, work)
    parity = staged.with_suffix(".kicad_sch").is_file()
    refill = bool(geom.BoardGeom.from_file(staged).zones_of())
    log = (lambda s: print(s, file=sys.stderr)) if args.verbose else None
    facts = improve_staged(
        cli, staged, cons, pro, classes=classes, keep=args.keep_net,
        batch=args.batch, rounds=args.rounds, min_gain=args.min_gain,
        patience=args.patience, below=args.below, grid=args.grid,
        parity=parity, refill=refill, log=log)
    final = facts.pop("drc")
    swapped = bool(facts["kept_rounds"]) and not args.dry_run
    if swapped:
        routelib.swap_in(staged, pcb)
    payload = {"script": SCRIPT, "status": "pass", "board": str(pcb),
               "counts": final["counts"], "violations": [],
               "facts": {**facts, "swapped": swapped, "work_dir": str(work)}}
    return payload, args.out_report


def main(argv: list[str] | None = None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    sys.exit(main())
