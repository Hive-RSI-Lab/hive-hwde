"""#950: a render of each edge connector, framed on its mouth, for the
release review.

#911 caught the laptop hub's backwards microSD socket (J6) by eye in the end:
a person looking in across the board edge at J6 sees the closed back of the
housing where the card should go. render.py --views edge writes one such
picture per J/P/CN part within matinglib.EDGE_NEAR_MM of the outline, and
labels each with what check_mating says should face the camera.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "lib"))
sys.path.insert(0, str(REPO / "tests"))
import kc  # noqa: E402
import render  # noqa: E402
from test_edge_connector import _sd, _unlisted  # noqa: E402
from test_mating import _board, _fp  # noqa: E402


def _plan(pcb, ref="J6"):
    return next(p for p in render.edge_plans(pcb) if p["ref"] == ref)


def test_planted_backwards_sd_socket_render_shows_its_back(tmp_path):
    """The planted fault: the laptop's J6 turned 180, mouth into the board.
    The camera still looks in across the edge J6 sits at (its bottom edge,
    +y), and the render is labelled as showing the back of the housing."""
    p = _plan(_sd(tmp_path, 180))
    assert p["edge"] == "+y" and p["shows"] == "back"
    assert p["rotate"] == f"{render.EDGE_TILT},0,0"


def test_sd_socket_facing_the_edge_shows_its_mouth(tmp_path):
    p = _plan(_sd(tmp_path, 0))
    assert (p["edge"], p["shows"], p["family"]) == ("+y", "mouth", "card_slot")
    # framed on the part: J6's centre (15, 13.25) from the board's (15, 10),
    # in cm with y up; zoomed in on the small board
    x, y, z = (float(v) for v in p["pivot"].split(","))
    assert (x, z) == (0.0, 0.0) and y == pytest.approx(-0.325, abs=0.01)
    assert 1.0 <= p["zoom"] <= 10.0


def test_unknown_edge_connector_is_rendered_and_says_unjudged(tmp_path):
    assert _plan(_unlisted(tmp_path))["shows"] == "unjudged"


def test_a_connector_away_from_the_edge_gets_no_render(tmp_path):
    pcb = _board(tmp_path, _fp("J6", "SOCKET-SMD_8P-UNLISTED", 15.0, 10.0,
                               cy=(-2, -2, 2, 2)))
    assert render.edge_plans(pcb) == []


def test_camera_sits_outside_each_edge():
    assert render._edge_camera((0.0, 1.0)) == 0
    assert render._edge_camera((0.0, -1.0)) == 180
    assert render._edge_camera((-1.0, 0.0)) == 90
    assert render._edge_camera((1.0, 0.0)) == -90


def test_edge_is_a_view_name():
    assert render.parse_views("review,edge") == ["top", "bottom", "iso",
                                                 "edge"]
    with pytest.raises(ValueError):
        render.parse_views("edges")


def test_render_png_passes_pivot_and_zoom(tmp_path, monkeypatch):
    seen = {}

    def fake(cli, args, env=None):
        seen["args"] = args
        raise RuntimeError("stop")
    monkeypatch.setattr(kc, "render_env", lambda cli: ({}, ""))
    monkeypatch.setattr(kc, "run_cli", fake)
    pcb = _sd(tmp_path, 0)
    with pytest.raises(RuntimeError):
        kc.render_png(Path("kicad-cli"), pcb, tmp_path / "o.png",
                      rotate="-60,0,0", pivot="0,-0.3,0", zoom=4.5)
    a = seen["args"]
    assert a[a.index("--pivot") + 1] == "0,-0.3,0"
    assert a[a.index("--zoom") + 1] == "4.5"


@pytest.mark.slow
def test_edge_renders_are_written_per_connector(tmp_path):
    from lib import env
    if env.find_kicad_cli() is None:
        pytest.skip("kicad-cli not installed")
    pcb = _sd(tmp_path, 180)
    r = render.render_views(pcb, ["edge"], tmp_path / "renders", width=400,
                            height=300, quality="basic")
    assert r["status"] == "pass", r
    (o,) = r["outputs"]
    assert o["view"] == "edge_J6" and o["shows"] == "back"
    assert Path(o["path"]).name == "b_edge_J6.png"
    assert Path(o["path"]).stat().st_size > 0
