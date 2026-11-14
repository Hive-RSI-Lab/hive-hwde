"""loadlib.py - time limits that stretch for a busy machine, not for a hang (#970).

A wall-clock limit sized on an idle box gives a false red on a loaded one: the
tower at load 100 on 20 CPUs, with this account at a low CPU weight, runs a
32 s test in 90 s and a 300 s SWIG call past 300 s. Raising the numbers would
hide a real hang for longer. A Limit keeps its base and stretches only while
both of these hold:

  * the machine is contended: CPU pressure ("some", /proc/pressure/cpu style,
    read from our own cgroup first) says our tasks wait for a CPU a share p of
    the time, so work runs about 1/(1-p) times slower (stretch);
  * the work is still moving: its process tree (utime+stime of the process
    and every descendant, reaped children included) got at least MIN_SHARE of
    a CPU over the last check window, beyond one clock tick of noise and
    beyond the CPU the check itself spent reading /proc.

So a wedged call (idle, no CPU) fails at its base limit on any load, busy work
on an idle box fails at its base limit, and nothing runs past base x
STRETCH_MAX. With no pressure or /proc to read (not Linux) a Limit is the plain
base limit it replaced.

  run(argv, timeout=..., **subprocess.run kwargs)  subprocess.run with a Limit
  alarm(seconds, on_expire)                         a SIGALRM Limit on this process
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

STRETCH_MAX = 4.0     # never more than 4x the base, whatever the load
CONTENDED = 1.25      # a stretch under this is an idle box: no extension
MIN_SHARE = 0.02      # CPU share over a window that counts as still moving


def _psi_some(path: Path) -> float | None:
    """Largest of avg10/avg60/avg300 on the "some" line, as a 0..1 share."""
    try:
        line = next(ln for ln in path.read_text().splitlines()
                    if ln.startswith("some "))
    except (OSError, StopIteration):
        return None
    avgs = [float(f.split("=")[1]) for f in line.split()[1:]
            if f.startswith("avg")]
    return max(avgs) / 100 if avgs else None


def pressure() -> float:
    """Share of time our tasks waited for a CPU (0..1): this cgroup's
    cpu.pressure, else the machine's, else 1-min load per CPU over 1 as a
    rough stand-in, else 0."""
    try:
        rel = Path("/proc/self/cgroup").read_text().strip().split(":", 2)[2]
        p = _psi_some(Path("/sys/fs/cgroup") / rel.lstrip("/") / "cpu.pressure")
        if p is not None:
            return p
    except (OSError, IndexError):
        pass
    p = _psi_some(Path("/proc/pressure/cpu"))
    if p is not None:
        return p
    try:
        per = os.getloadavg()[0] / (os.cpu_count() or 1)
    except OSError:
        return 0.0
    return max(0.0, 1 - 1 / per) if per > 1 else 0.0


def stretch(p: float) -> float:
    """How much slower work runs when it waits a share `p` of the time."""
    return STRETCH_MAX if p >= 1 else min(STRETCH_MAX, 1 / (1 - p))


TICK = 1 / (os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100)


def tree_cpu(pid: int) -> float | None:
    """CPU seconds used by `pid` and every live descendant, plus their reaped
    children, from /proc; None when /proc can't be read."""
    kids: dict[int, list[int]] = {}
    used: dict[int, int] = {}
    try:
        entries = [e for e in os.listdir("/proc") if e.isdigit()]
    except OSError:
        return None
    for e in entries:
        try:
            raw = Path("/proc", e, "stat").read_text()
        except OSError:
            continue
        f = raw[raw.rfind(")") + 2:].split()   # fields from state on
        kids.setdefault(int(f[1]), []).append(int(e))
        used[int(e)] = sum(int(x) for x in f[11:15])   # u/s time, cu/cs time
    if pid not in used:
        return None
    total, todo = 0, [pid]
    while todo:
        q = todo.pop()
        total += used.get(q, 0)
        todo += kids.get(q, [])
    return total * TICK


class Limit:
    """A base wall-clock limit that stretches while the box is contended and
    the watched process tree is still getting CPU. Call wait() for how long to
    sleep before the next check; it returns None once the limit has run out."""

    def __init__(self, base: float, pid: int | None = None, window: float | None = None,
                 read_pressure=None, read_cpu=None, clock=time.monotonic):
        self.base = float(base)
        # a tenth of the base, at least 1 s (CPU time counts in 10 ms ticks)
        # unless the base is under 2 s, at most a minute
        self.window = window or min(60.0, max(self.base / 10, min(1.0, self.base / 2)))
        self.pid = os.getpid() if pid is None else pid
        self.read_pressure = read_pressure or pressure
        self.read_cpu = read_cpu or tree_cpu
        self.clock = clock
        self.t0 = clock()
        self.mark = None          # (time, cpu) the next check measures from
        # watching our own process: our CPU spent reading /proc is not work
        self.own = self.pid == os.getpid() and read_cpu is None
        self.cost = 0.0
        self.why = ""             # why it ran out, for the failure message

    def _cpu(self) -> float | None:
        """The tree's CPU seconds, less what our own reads of it have cost."""
        c0 = time.process_time()
        cpu = self.read_cpu(self.pid)
        if self.own:
            self.cost += time.process_time() - c0
        return None if cpu is None else cpu - self.cost

    def elapsed(self) -> float:
        return self.clock() - self.t0

    def wait(self) -> float | None:
        now = self.elapsed()
        if self.mark is None:     # sample the CPU one window before the base
            first = self.base - self.window
            if now < first:
                return first - now
            self.mark = (now, self._cpu())
            return max(self.base - now, self.window)
        if now < self.base:
            return self.base - now
        p = self.read_pressure()
        s = stretch(p)
        cpu = self._cpu()
        t_m, cpu_m = self.mark
        # less one clock tick: /proc counts in ticks, so one tick is noise
        share = (None if cpu is None or cpu_m is None
                 else (cpu - cpu_m - TICK) / max(now - t_m, 1e-6))
        until = self.base * s
        if s < CONTENDED:
            self.why = f"CPU pressure {p:.0%}, not stretched"
        elif share is None:
            self.why = "no process CPU to read, not stretched"
        elif share < MIN_SHARE:
            self.why = (f"no CPU in the last {now - t_m:.0f} s (stuck, not slow), "
                        f"pressure {p:.0%}")
        elif now >= until:
            self.why = f"stretched to {until:.0f} s for CPU pressure {p:.0%}"
        else:
            self.mark = (now, cpu)
            return min(self.window, until - now)
        return None


def run(argv, *, timeout: float, input=None, capture_output: bool = False,
        **kw) -> subprocess.CompletedProcess:
    """subprocess.run(argv, timeout=...) under a Limit on the child's tree:
    the same CompletedProcess, and the same TimeoutExpired (child killed) once
    the limit runs out."""
    if capture_output:
        kw["stdout"] = kw["stderr"] = subprocess.PIPE
    if input is not None:
        kw["stdin"] = subprocess.PIPE
    with subprocess.Popen(argv, **kw) as p:
        lim = Limit(timeout, pid=p.pid)
        while True:
            w = lim.wait()
            if w is None:
                p.kill()
                out, err = p.communicate()
                raise subprocess.TimeoutExpired(argv, lim.elapsed(), out, err)
            try:
                out, err = p.communicate(input, timeout=w)
                break
            except subprocess.TimeoutExpired:
                continue
    return subprocess.CompletedProcess(argv, p.returncode, out, err)


@contextmanager
def alarm(seconds: float, on_expire, window: float | None = None, **lim_kw):
    """Call on_expire(limit) from SIGALRM once a Limit on this process (and its
    children) runs out; it should raise. Main thread only, like signal."""
    lim = Limit(seconds, window=window, **lim_kw)

    def tick(signum, frame):
        w = lim.wait()
        if w is None:
            on_expire(lim)
        else:
            signal.setitimer(signal.ITIMER_REAL, w)
    old = signal.signal(signal.SIGALRM, tick)
    signal.setitimer(signal.ITIMER_REAL, lim.wait())
    try:
        yield lim
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)
