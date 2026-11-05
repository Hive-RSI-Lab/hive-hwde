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
    assert old[1] == "-cp" and old[2].startswith(str(routelib.FR_OVERLAY))
    assert old[2].endswith("t/freerouting-2.2.4.jar") and old[3] == routelib.FR_MAIN_CLASS
    assert "-jar" not in old
    new = routelib.build_fr_cmd(Path("java"), Path("t/freerouting-2.5.0.jar"), dsn, ses)
    assert new[1:3] == ["-jar", "t/freerouting-2.5.0.jar"]


def test_the_overlay_carries_its_class_and_source():
    board = routelib.FR_OVERLAY / "app" / "freerouting" / "board"
    assert (board / "BasicBoard.class").read_bytes()[:4] == b"\xca\xfe\xba\xbe"
    assert "MAX_NORMALIZE_PASSES" in (board / "BasicBoard.java").read_text(encoding="utf-8")
