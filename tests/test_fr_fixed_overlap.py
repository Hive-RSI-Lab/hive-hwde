"""A routed trace lying along a fixed wire must not hang Freerouting (#477,
#657, #665).

stub_overlap.dsn is one net of an escaped UFBGA-64: its escape stub as a fixed
wire, a pin on a connector to reach, and the other pads as obstacles. On stock
2.2.4 the route lands on the stub at an interior point, the stub can't be split
because it is fixed, and BasicBoard.normalize_traces splits and recombines the
routed trace forever: pass 1 never ends, whatever the time limit.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "lib"))
import env  # noqa: E402
import routelib  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "freerouting" / "stub_overlap.dsn"


def test_live_route_along_a_fixed_stub_finishes_and_keeps_the_stub(tmp_path):
    jar = env.find_freerouting_jar()
    java = env.find_java()
    if jar is None or java is None:
        pytest.skip("freerouting or java not installed")
    java = java[0]
    dsn = tmp_path / "b.dsn"
    dsn.write_bytes(FIXTURE.read_bytes())
    ses = tmp_path / "b.ses"
    facts = routelib.run_freerouting(java, jar, dsn, ses, rung={"mp": 1},
                                     timeout=240, stall_s=120)
    assert facts["timed_out"] is False, facts["kill_reason"]
    assert facts["rc"] == 0 and facts["ses_written"]
    text = ses.read_text(encoding="utf-8")
    assert "(type protect)" in text     # the stub is still fixed copper
    assert facts["unrouted"] == 0


def test_only_the_jar_with_the_hang_gets_the_overlay():
    ses, dsn = Path("o.ses"), Path("i.dsn")
    old = routelib.build_fr_cmd(Path("java"), Path("t/freerouting-2.2.4.jar"), dsn, ses)
    cp = old.index("-cp")    # after the JVM flags (#897)
    assert old[cp + 1].startswith(str(routelib.FR_OVERLAY))
    assert old[cp + 1].endswith("t/freerouting-2.2.4.jar")
    assert old[cp + 2] == routelib.FR_MAIN_CLASS
    assert "-jar" not in old
    new = routelib.build_fr_cmd(Path("java"), Path("t/freerouting-2.5.0.jar"), dsn, ses)
    j = new.index("-jar")
    assert new[j + 1] == "t/freerouting-2.5.0.jar" and "-cp" not in new


def test_the_overlay_carries_its_class_and_source():
    board = routelib.FR_OVERLAY / "app" / "freerouting" / "board"
    assert (board / "BasicBoard.class").read_bytes()[:4] == b"\xca\xfe\xba\xbe"
    assert "MAX_NORMALIZE_PASSES" in (board / "BasicBoard.java").read_text(encoding="utf-8")


def test_live_freerouting_out_of_heap_fails_fast_as_oom(tmp_path, monkeypatch):
    """#897 planted fault: a heap cap too small for the board. The JVM must
    exit at once (ExitOnOutOfMemoryError) and the run read as oom, not hang
    in GC until the stall timer or pass as a route with nothing routed."""
    jar = env.find_freerouting_jar()
    java = env.find_java()
    if jar is None or java is None:
        pytest.skip("freerouting or java not installed")
    monkeypatch.setenv("HWDE_FR_HEAP_MB", "16")
    dsn = tmp_path / "b.dsn"
    dsn.write_bytes(FIXTURE.read_bytes())
    facts = routelib.run_freerouting(java[0], jar, dsn, tmp_path / "b.ses",
                                     rung={"mp": 1}, timeout=240, stall_s=120)
    assert facts["oom"] is True and facts["heap_mb"] == 16
    assert facts["timed_out"] is False and facts["rc"] != 0
