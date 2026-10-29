"""U12 - pre-credential order/state safety (codex C3+C5+C6). Fault-injection
suite; the DONE menu from the U12 board brief:

  (1) snapshot/restore containment: traversal / absolute / symlink refused
  (2) two concurrent --api-create -> exactly one order, loser journaled
  (3) corrupt / truncated latch -> hard refuse exit 2, file untouched
  (4) crash mid-restore leaves the workspace intact
  (5) stale CAS writer fails loudly (library + CLI --if-digest)
  (+) concurrent state writers serialize, no lost update; journal is
      append-only; atomic writes never leave a torn file.
All hermetic, zero network; the OS-lock and crash cases run real
subprocesses through tests/u12_driver.py.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_jlcapi as tj  # noqa: E402

import order_submit  # noqa: E402
import safelib  # noqa: E402
import state as state_mod  # noqa: E402
from checklib import CheckError  # noqa: E402

DRIVER = HERE / "u12_driver.py"
STATE_PY = tj.SCRIPTS / "state.py"
PY = sys.executable
POSIX = os.name == "posix"


def make_ws(tmp_path, n_files=2):
    ws = tmp_path / "ws"
    st = state_mod.State.init(ws, "b1")
    for i in range(n_files):
        f = ws / "kicad" / f"f{i}.txt"
        f.write_text(f"original {i}\n", encoding="utf-8")
        st.set_artifact(f"f{i}", f"kicad/f{i}.txt")
    st.save()
    return ws, st


def read_json(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


# ================================================ (1) containment

def test_snapshot_rejects_traversal_absolute_symlink(tmp_path):
    ws, st = make_ws(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    with pytest.raises(safelib.ContainmentError):
        st.snapshot("s", files=["../outside.txt"])
    with pytest.raises(safelib.ContainmentError):
        st.snapshot("s", files=[str(outside)])
    with pytest.raises(safelib.ContainmentError):
        st.snapshot("s", files=["kicad/../../outside.txt"])
    if POSIX:
        (ws / "kicad" / "link.txt").symlink_to(outside)
        with pytest.raises(safelib.ContainmentError):
            st.snapshot("s", files=["kicad/link.txt"])
        (ws / "dirlink").symlink_to(tmp_path)
        with pytest.raises(safelib.ContainmentError):
            st.snapshot("s", files=["dirlink/outside.txt"])
    with pytest.raises(safelib.ContainmentError):
        st.snapshot("../evil", files=["kicad/f0.txt"])
    assert not (ws / "state_snapshots").exists() or \
        not list((ws / "state_snapshots").iterdir())


def test_restore_rejects_tampered_manifest_and_symlink_target(tmp_path):
    ws, st = make_ws(tmp_path)
    st.snapshot("good", files=["kicad/f0.txt"])
    snap = ws / "state_snapshots" / "good"
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me", encoding="utf-8")
    # traversal in the manifest
    man = read_json(snap / "manifest.json")
    (snap / "evil.txt").write_text("pwned", encoding="utf-8")
    man["files"] = [{"path": "../../victim.txt",
                     "sha256": safelib.sha256_file(snap / "evil.txt")}]
    (snap / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
    with pytest.raises(safelib.ContainmentError):
        st.restore("good")
    assert victim.read_text(encoding="utf-8") == "keep me"
    # absolute path in the manifest
    man["files"] = [{"path": str(victim), "sha256": "0" * 64}]
    (snap / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
    with pytest.raises(safelib.ContainmentError):
        st.restore("good")
    assert victim.read_text(encoding="utf-8") == "keep me"
    if POSIX:
        # a valid entry whose TARGET has become a symlink out of the ws
        st.snapshot("good2", files=["kicad/f0.txt"])
        (ws / "kicad" / "f0.txt").unlink()
        (ws / "kicad" / "f0.txt").symlink_to(victim)
        with pytest.raises(safelib.ContainmentError):
            st.restore("good2")
        assert victim.read_text(encoding="utf-8") == "keep me"
        assert (ws / "kicad" / "f0.txt").is_symlink()


# ============================================ (4) crash mid-restore

def test_crash_mid_restore_leaves_workspace_intact(tmp_path):
    ws, st = make_ws(tmp_path, n_files=3)
    st.snapshot("pre", files=["kicad/f0.txt", "kicad/f1.txt", "kicad/f2.txt"])
    for i in range(3):
        (ws / "kicad" / f"f{i}.txt").write_text(f"edited {i}\n",
                                               encoding="utf-8")
    before = {i: (ws / "kicad" / f"f{i}.txt").read_bytes() for i in range(3)}
    cp = subprocess.run([PY, str(DRIVER), "restore-crash", str(ws), "pre"],
                        capture_output=True, text=True, timeout=60)
    assert cp.returncode == 137, cp.stdout + cp.stderr
    after = {i: (ws / "kicad" / f"f{i}.txt").read_bytes() for i in range(3)}
    assert after == before                       # nothing swapped in
    leftovers = list((ws / "kicad").glob(".f*.restore-*.tmp"))
    assert leftovers                             # the crash left its stage
    # the next restore sweeps the stage and lands whole
    st2 = state_mod.State.load(ws / "state.json")
    res = st2.restore("pre")
    assert sorted(res["restored"]) == ["kicad/f0.txt", "kicad/f1.txt",
                                       "kicad/f2.txt"]
    for i in range(3):
        assert (ws / "kicad" / f"f{i}.txt").read_text(
            encoding="utf-8") == f"original {i}\n"
    assert not list((ws / "kicad").glob(".f*.restore-*.tmp"))


def test_restore_hash_mismatch_restores_nothing(tmp_path):
    ws, st = make_ws(tmp_path, n_files=2)
    st.snapshot("pre", files=["kicad/f0.txt", "kicad/f1.txt"])
    (ws / "state_snapshots" / "pre" / "kicad" / "f1.txt").write_text(
        "corrupted", encoding="utf-8")
    for i in range(2):
        (ws / "kicad" / f"f{i}.txt").write_text(f"edited {i}\n",
                                               encoding="utf-8")
    with pytest.raises(CheckError, match="hash mismatch"):
        st.restore("pre")
    for i in range(2):                           # f0 verified fine, yet
        assert (ws / "kicad" / f"f{i}.txt").read_text(   # NOTHING moved
            encoding="utf-8") == f"edited {i}\n"


# ======================================== (5) compare-and-swap writers

def test_stale_cas_writer_fails_loudly(tmp_path):
    ws, _ = make_ws(tmp_path)
    a = state_mod.State.load(ws / "state.json")
    b = state_mod.State.load(ws / "state.json")
    a._log("from_a")
    a.save()
    b._log("from_b")
    with pytest.raises(safelib.StaleWriteError, match="changed since"):
        b.save()
    data = read_json(ws / "state.json")
    events = [h["event"] for h in data["history"]]
    assert "from_a" in events and "from_b" not in events
    # a fresh load carries the new base and writes fine
    c = state_mod.State.load(ws / "state.json")
    c._log("from_c")
    c.save()
    assert "from_c" in [h["event"] for h in read_json(ws / "state.json")
                        ["history"]]


def test_cli_if_digest_pin_refuses_stale_read(tmp_path):
    ws, _ = make_ws(tmp_path)
    show = subprocess.run([PY, str(STATE_PY), "show", "--workspace", str(ws)],
                          capture_output=True, text=True)
    digest = json.loads(show.stdout)["digest"]
    assert digest == safelib.sha256_file(ws / "state.json")
    ok = subprocess.run([PY, str(STATE_PY), "log", "--workspace", str(ws),
                         "--event", "tick", "--if-digest", digest],
                        capture_output=True, text=True)
    assert ok.returncode == 0, ok.stdout
    before = (ws / "state.json").read_bytes()
    stale = subprocess.run([PY, str(STATE_PY), "log", "--workspace", str(ws),
                            "--event", "tick", "--if-digest", digest],
                           capture_output=True, text=True)
    assert stale.returncode == 2
    assert "digest" in json.loads(stale.stdout)["error"]
    assert (ws / "state.json").read_bytes() == before   # nothing written


def test_concurrent_state_writers_lose_nothing(tmp_path):
    ws, _ = make_ws(tmp_path)
    workers, per = 3, 6
    codes = []

    def worker(w):
        for i in range(per):
            cp = subprocess.run(
                [PY, str(STATE_PY), "log", "--workspace", str(ws),
                 "--event", "tick", "--data", json.dumps({"w": w, "i": i})],
                capture_output=True, text=True, timeout=120)
            codes.append((cp.returncode, cp.stdout[-300:]))
    ths = [threading.Thread(target=worker, args=(w,)) for w in range(workers)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    assert all(c == 0 for c, _ in codes), codes
    ticks = [h for h in read_json(ws / "state.json")["history"]
             if h["event"] == "tick"]
    assert len(ticks) == workers * per            # no lost update
    assert not list(ws.glob(".state.json.*.tmp"))  # no orphaned temps


# ================================== (3) corrupt / truncated order latch

def _order_ready(tmp_path, api_env=False):
    pcb, fab, qj = tj.make_fab(tmp_path)
    code = order_submit.main(tj.submit_argv(pcb, fab, qj))
    assert code == 0
    return pcb, fab, qj


def test_truncated_latch_hard_refuses_exit_2(tmp_path, monkeypatch, capsys):
    pcb, fab, qj = _order_ready(tmp_path)
    capsys.readouterr()                          # drain the setup run
    latch = fab / "order.json"
    raw = latch.read_bytes()
    latch.write_bytes(raw[: len(raw) // 2])
    torn = latch.read_bytes()
    code = order_submit.main(tj.submit_argv(pcb, fab, qj))
    assert code == 2
    assert latch.read_bytes() == torn            # never rewritten/repaired
    err = json.loads(capsys.readouterr().out)["error"]
    assert "created-latch" in err and "refusing" in err
    journal = safelib.read_journal(fab / order_submit.JOURNAL_NAME)
    assert journal[-1]["event"] == "refused" and \
        journal[-1]["stage"] == "latch_load"
    # --api-create beside the torn latch: refused BEFORE any transport
    for k in tj.API_ENV:
        monkeypatch.setenv(k, "X")
    fake = tj.FakeSession()                       # any call would KeyError
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    aq = tj.write_api_quote(fab)
    code = order_submit.main(tj.submit_argv(
        pcb, fab, qj, "--api-create", "--api-quote-file", str(aq),
        "--confirm", "b1 5pcs 12.5"))
    assert code == 2 and fake.calls == []
    assert latch.read_bytes() == torn
    capsys.readouterr()


@pytest.mark.parametrize("doc", [
    '{"api": "not-an-object"}',
    '{"api": {"order": [1, 2]}}',
    '{"api": {"create_attempt": "in_flight"}}',
    '[]', '', 'null',
])
def test_wrong_shape_latch_hard_refuses(tmp_path, capsys, doc):
    pcb, fab, qj = _order_ready(tmp_path)
    capsys.readouterr()                          # drain the setup run
    latch = fab / "order.json"
    latch.write_text(doc, encoding="utf-8")
    code = order_submit.main(tj.submit_argv(pcb, fab, qj))
    assert code == 2
    assert latch.read_text(encoding="utf-8") == doc
    assert "refusing" in json.loads(capsys.readouterr().out)["error"]


# ===================================== (2) two concurrent creators

def test_two_concurrent_creates_exactly_one_wins(tmp_path):
    pcb, fab, qj = tj.make_fab(tmp_path)
    aq = tj.write_api_quote(fab)
    argv = [PY, str(DRIVER), "create", str(pcb), str(fab), str(qj), str(aq),
            "1.5", "b1 5pcs 12.5"]
    procs = [subprocess.Popen(argv, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True)
             for _ in range(2)]
    outs = [p.communicate(timeout=120) for p in procs]
    codes = [p.returncode for p in procs]
    assert sorted(codes) == [0, 2], [(c, o[0][-400:], o[1][-400:])
                                     for c, o in zip(codes, outs)]
    order = read_json(fab / "order.json")
    assert order["api"]["verdict"] == "created"
    assert order["api"]["order"]["batchNum"] == tj.CREATE_OK["batchNum"]
    journal = safelib.read_journal(fab / order_submit.JOURNAL_NAME)
    created = [r for r in journal if r["event"] == "created"]
    refused = [r for r in journal if r["event"] == "refused"]
    assert len(created) == 1                      # exactly one order
    assert len(refused) == 1 and refused[0]["stage"] == "create"
    assert "already recorded" in refused[0]["reason"]
    assert refused[0]["pid"] != created[0]["pid"]  # the LOSER journaled
    begins = [r for r in journal if r["event"] == "begin"]
    assert len(begins) == 2
    ends = [r for r in journal if r["event"] == "end"]
    assert sorted(e["exit"] for e in ends) == [0, 2]
    loser_out = json.loads(outs[codes.index(2)][0])
    assert loser_out["api"]["verdict"] == "created"       # sticky, not
    assert loser_out["api"]["last_create_verdict"] == "refused"  # downgraded


def test_lock_busy_refuses_and_journals(tmp_path, capsys):
    pcb, fab, qj = tj.make_fab(tmp_path)
    latch = fab / "order.json"
    held = threading.Event()
    release = threading.Event()

    def holder():
        with safelib.writer_lock(latch, what="test"):
            held.set()
            release.wait(10)
    t = threading.Thread(target=holder)
    t.start()
    held.wait(5)
    try:
        code = order_submit.main(tj.submit_argv(
            pcb, fab, qj, "--lock-timeout", "0.3"))
    finally:
        release.set()
        t.join()
    assert code == 2
    assert "writer lock" in json.loads(capsys.readouterr().out)["error"]
    assert not latch.exists()
    j = safelib.read_journal(fab / order_submit.JOURNAL_NAME)
    assert j[-1]["event"] == "refused" and j[-1]["stage"] == "lock"


# ================================================= primitives

def test_journal_is_append_only_and_corruption_is_loud(tmp_path):
    j = tmp_path / "j.jsonl"
    safelib.append_journal(j, {"event": "a"})
    safelib.append_journal(j, {"event": "b"})
    recs = safelib.read_journal(j)
    assert [r["event"] for r in recs] == ["a", "b"]
    assert all("ts" in r and "pid" in r for r in recs)
    with open(j, "a", encoding="utf-8") as fh:
        fh.write('{"event": "c"')                 # torn tail
    with pytest.raises(safelib.JournalCorrupt):
        safelib.read_journal(j)


def test_atomic_write_crash_before_replace_keeps_old(tmp_path, monkeypatch):
    p = tmp_path / "x.json"
    safelib.atomic_write_json(p, {"v": 1})

    def boom(point, **ctx):
        if point == "atomic_write.before_replace":
            raise RuntimeError("crash")
    monkeypatch.setattr(safelib, "FAULT_HOOK", boom)
    with pytest.raises(RuntimeError):
        safelib.atomic_write_json(p, {"v": 2})
    assert read_json(p) == {"v": 1}
    assert not list(tmp_path.glob(".x.json.*.tmp"))


# ======================== PR #5 review: whitelist + containment follow-ups

def _arm_attempt(fab, attempt):
    latch = fab / "order.json"
    doc = read_json(latch)
    doc.setdefault("api", {})["create_attempt"] = attempt
    latch.write_text(json.dumps(doc), encoding="utf-8")
    return latch


@pytest.mark.parametrize("attempt", [
    {},                                          # state missing
    {"at": "2026-09-01T00:00:00", "grand_total": 12.5},
    {"state": None},
    {"state": ""},
    {"state": 5},                                # not a string
    {"state": "created "},                       # near-miss spelling
    {"state": "failed:some_new_code"},           # unknown failure class
    {"state": "pending"},
])
def test_create_attempt_unknown_state_refuses(tmp_path, monkeypatch, capsys,
                                              attempt):
    """The ambiguous-attempt check is a WHITELIST: a create_attempt record
    whose state is missing or not one of the unambiguous states refuses
    --api-create with exit 2, zero transport calls, no pre-arm, and the
    attempt record and latch left exactly as they were."""
    pcb, fab, qj = _order_ready(tmp_path)
    capsys.readouterr()
    _arm_attempt(fab, attempt)
    for k in tj.API_ENV:
        monkeypatch.setenv(k, "X")
    fake = tj.FakeSession()
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    aq = tj.write_api_quote(fab)
    code = order_submit.main(tj.submit_argv(
        pcb, fab, qj, "--api-create", "--api-quote-file", str(aq),
        "--confirm", "b1 5pcs 12.5"))
    capsys.readouterr()
    assert code == 2
    assert fake.calls == []                       # no create sent
    order = read_json(fab / "order.json")
    assert order["api"]["create_attempt"] == attempt   # untouched
    assert order["api"]["verdict"] == "refused"
    assert "missing or unrecognised state" in order["api"]["note"]
    assert not (order["api"].get("order") or {}).get("batchNum")
    assert not order.get("order_number")
    journal = safelib.read_journal(fab / order_submit.JOURNAL_NAME)
    assert not any(r["event"] == "in_flight" for r in journal)
    assert journal[-2]["event"] == "refused" and \
        journal[-2]["stage"] == "create"


@pytest.mark.parametrize("state", ["failed:scope_pending",
                                   "failed:ip_blocked",
                                   "failed:rate_limited"])
def test_create_attempt_unambiguous_states_allow_retry(tmp_path, monkeypatch,
                                                       capsys, state):
    pcb, fab, qj = _order_ready(tmp_path)
    capsys.readouterr()
    _arm_attempt(fab, {"state": state, "at": "2026-09-01T00:00:00"})
    for k in tj.API_ENV:
        monkeypatch.setenv(k, "X")
    fake = tj.FakeSession(create_order=tj.ok_resp(tj.CREATE_OK))
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    aq = tj.write_api_quote(fab)
    code = order_submit.main(tj.submit_argv(
        pcb, fab, qj, "--api-create", "--api-quote-file", str(aq),
        "--confirm", "b1 5pcs 12.5"))
    capsys.readouterr()
    assert code == 0
    assert fake.names() == ["create_order"]
    assert read_json(fab / "order.json")["api"]["create_attempt"][
        "state"] == "created"


@pytest.mark.skipif(not POSIX, reason="symlinks")
def test_symlinked_snapshot_dir_is_refused_not_followed(tmp_path):
    ws, st = make_ws(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (ws / "state_snapshots").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(safelib.ContainmentError, match="symlink"):
        st.snapshot("s", files=["kicad/f0.txt"])
    assert list(elsewhere.iterdir()) == []        # nothing written outside
    # restore through the same link is refused too, workspace untouched
    good = elsewhere / "good"
    (good / "kicad").mkdir(parents=True)
    (good / "kicad" / "f0.txt").write_text("from outside", encoding="utf-8")
    (good / "manifest.json").write_text(json.dumps({"files": [
        {"path": "kicad/f0.txt",
         "sha256": safelib.sha256_file(good / "kicad" / "f0.txt")}]}),
        encoding="utf-8")
    with pytest.raises(safelib.ContainmentError, match="symlink"):
        st.restore("good")
    assert (ws / "kicad" / "f0.txt").read_text(
        encoding="utf-8") == "original 0\n"
    # a symlinked <label> dir inside a real state_snapshots/ is refused too
    (ws / "state_snapshots").unlink()
    (ws / "state_snapshots").mkdir()
    (ws / "state_snapshots" / "lbl").symlink_to(elsewhere,
                                                target_is_directory=True)
    with pytest.raises(safelib.ContainmentError, match="symlink"):
        st.snapshot("lbl", files=["kicad/f0.txt"])
    assert sorted(p.name for p in elsewhere.iterdir()) == ["good"]


@pytest.mark.parametrize("entry", ["state.json", "state.json.lock",
                                   "kicad/f0.txt.lock", "kicad/state.json"])
def test_snapshot_refuses_state_and_lock_entries(tmp_path, entry):
    ws, st = make_ws(tmp_path)
    p = ws / entry
    if not p.exists():
        p.write_text("x", encoding="utf-8")
    with pytest.raises(safelib.ContainmentError, match="never snapshotted"):
        st.snapshot("s", files=["kicad/f1.txt", entry])
    assert not (ws / "state_snapshots" / "s").exists()   # no half snapshot


def test_restore_refuses_state_json_in_manifest(tmp_path):
    ws, st = make_ws(tmp_path)
    st.snapshot("good", files=["kicad/f0.txt"])
    snap = ws / "state_snapshots" / "good"
    (snap / "state.json").write_text("{}", encoding="utf-8")
    man = read_json(snap / "manifest.json")
    man["files"].append({"path": "state.json",
                         "sha256": safelib.sha256_file(snap / "state.json")})
    (snap / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
    before = (ws / "state.json").read_bytes()
    (ws / "kicad" / "f0.txt").write_text("edited\n", encoding="utf-8")
    with pytest.raises(safelib.ContainmentError, match="never snapshotted"):
        st.restore("good")
    assert (ws / "state.json").read_bytes() == before
    assert (ws / "kicad" / "f0.txt").read_text(encoding="utf-8") == "edited\n"


def test_cli_mutation_on_missing_workspace_creates_nothing(tmp_path):
    typo = tmp_path / "no-such-ws"
    cp = subprocess.run([PY, str(STATE_PY), "log", "--workspace", str(typo),
                         "--event", "tick"], capture_output=True, text=True)
    assert cp.returncode == 2
    assert "no state file" in json.loads(cp.stdout)["error"]
    assert not typo.exists()                      # no mkdir, no stray lock
    empty = tmp_path / "empty-ws"
    empty.mkdir()
    cp = subprocess.run([PY, str(STATE_PY), "log", "--workspace", str(empty),
                         "--event", "tick"], capture_output=True, text=True)
    assert cp.returncode == 2
    assert list(empty.iterdir()) == []
