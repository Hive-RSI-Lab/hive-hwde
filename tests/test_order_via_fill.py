"""#696: order_submit orders the via process the board asks for. A filled or
capped board is never sent tented, a quote that prices another via process
stops the package, and unfilled via-in-pad goes out named in the remark.
Mocked sessions only: this file must never reach a live endpoint."""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "lib"))
sys.path.insert(0, str(REPO / "tests"))

import check_wicking as cw  # noqa: E402
import order_submit  # noqa: E402
import sexpdata  # noqa: E402
import test_jlcapi as tj  # noqa: E402

api_env = tj.api_env
GOLD = REPO / "tests" / "golden" / "blinky2" / "blinky2.kicad_pcb"


def _via_in_pad(pcb: Path, fill: str = "") -> None:
    """Plant a via at the first SMD pad's centre of a copy of the gold board."""
    text = GOLD.read_text(encoding="utf-8")
    root = sexpdata.loads(text)
    pad = next(p for ps in cw.read_pads(root, cw.board_defaults(root)).values()
               for p in ps if p["kind"] == "smd")
    x, y = pad["center"]
    via = (f'\n  (via (at {x} {y}) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu")'
           f'{fill} (net 0) (uuid "00000000-0000-0000-0000-000000000696"))\n')
    pcb.write_text(text.rstrip()[:-1] + via + ")\n", encoding="utf-8")


def _filled(pcb: Path) -> None:
    _via_in_pad(pcb, " (filling yes) (capping yes)")


def _plain(pcb: Path) -> None:
    shutil.copy(GOLD, pcb)


def _quote_fill(qj: Path, *fills) -> None:
    """Rewrite the quote's matrix to one row per via_fill, as order_quote
    --via-fill writes it (no key for 'none')."""
    q = json.loads(qj.read_text(encoding="utf-8"))
    row = q["matrix"][0]
    q["matrix"] = [{**row, **({"via_fill": f} if f != "none" else {}),
                    "total": 4.0 if f == "none" else 21.33} for f in fills]
    qj.write_text(json.dumps(q), encoding="utf-8")


def _param(fab: Path) -> dict:
    q = json.loads((fab / "api_quote.json").read_text(encoding="utf-8"))
    return q["calculate_request"]["pcbParam"]


def _order(fab: Path) -> dict:
    return json.loads((fab / "order.json").read_text(encoding="utf-8"))


def _steps(man: dict, word: str) -> list[str]:
    return [s for s in man["human_steps"] if word in s]


# ---- the issue's three known answers


def test_plain_board_is_unchanged(tmp_path, monkeypatch, api_env, capsys):
    pcb, fab, qj = tj.make_fab(tmp_path)
    _plain(pcb)
    monkeypatch.setattr(order_submit, "_make_session", tj.quote_session)
    assert order_submit.main(tj.submit_argv(pcb, fab, qj, "--api")) == 0
    p = _param(fab)
    assert p["viaCovering"] == 1 and p["orderDetailsRemark"] == ""
    assert not _steps(_order(fab), "VIA-")


def test_filled_board_is_refused_before_any_network(tmp_path, monkeypatch,
                                                    api_env, capsys):
    """The POFV enum is not recorded anywhere (POFV TODO), so the only safe
    answer today is a refusal, not a tented order."""
    pcb, fab, qj = tj.make_fab(tmp_path)
    _filled(pcb)
    _quote_fill(qj, "epoxy")
    fake = tj.quote_session()
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    assert order_submit.main(tj.submit_argv(pcb, fab, qj, "--api")) == 2
    assert fake.names() == []
    assert not (fab / "api_quote.json").exists()
    assert "viaCovering" in capsys.readouterr().out


def test_quote_without_fill_on_a_filled_board_is_refused(tmp_path, monkeypatch,
                                                         api_env, capsys):
    pcb, fab, qj = tj.make_fab(tmp_path)
    _filled(pcb)
    man = order_submit.run(pcb, fab, quote=qj, qty=5)
    assert man["status"] == "not_order_ready"
    assert _steps(man, "VIA-FILL MISMATCH")
    fake = tj.quote_session()
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    assert order_submit.main(tj.submit_argv(pcb, fab, qj, "--api")) != 0
    assert fake.names() == []
    assert "VIA-FILL MISMATCH" in capsys.readouterr().out


# ---- the enum, once someone resolves it


def test_resolved_enum_goes_out_on_a_filled_board(tmp_path, monkeypatch,
                                                  api_env, capsys):
    monkeypatch.setattr(order_submit, "VIA_COVERING_POFV", 77)
    pcb, fab, qj = tj.make_fab(tmp_path)
    _filled(pcb)
    _quote_fill(qj, "epoxy")
    monkeypatch.setattr(order_submit, "_make_session", tj.quote_session)
    assert order_submit.main(tj.submit_argv(pcb, fab, qj, "--api")) == 0
    assert _param(fab)["viaCovering"] == 77


def test_pofv_enum_stays_unresolved_until_a_calculate_proves_it():
    """Don't guess a code that buys a process: when this fails, a recorded
    calculate must show viaCoveringMoney for the new value."""
    assert order_submit.VIA_COVERING_POFV is None


# ---- quote against board


def test_filled_board_picks_the_epoxy_row_and_names_the_option(tmp_path):
    pcb, fab, qj = tj.make_fab(tmp_path)
    _filled(pcb)
    _quote_fill(qj, "none", "epoxy")
    man = order_submit.run(pcb, fab, quote=qj, qty=5)
    assert man["status"] == "ready_for_human"
    assert man["quote"]["selected"]["via_fill"] == "epoxy"
    assert man["spec_snapshot"]["via_fill"] == "epoxy"
    assert man["spec_snapshot"]["filled_vias"] == 1
    step = _steps(man, "VIA-FILL:")
    assert len(step) == 1 and "Epoxy Filled & Capped" in step[0]


def test_epoxy_quote_on_a_plain_board_is_refused(tmp_path):
    pcb, fab, qj = tj.make_fab(tmp_path)
    _plain(pcb)
    _quote_fill(qj, "epoxy")
    man = order_submit.run(pcb, fab, quote=qj, qty=5)
    assert man["status"] == "not_order_ready"
    assert _steps(man, "VIA-FILL MISMATCH")


def test_none_and_epoxy_quote_on_a_plain_board_takes_the_none_row(tmp_path):
    pcb, fab, qj = tj.make_fab(tmp_path)
    _plain(pcb)
    _quote_fill(qj, "epoxy", "none")
    man = order_submit.run(pcb, fab, quote=qj, qty=5)
    assert man["status"] == "ready_for_human"
    assert "via_fill" not in man["quote"]["selected"]


def test_mismatch_step_clears_once_the_quote_is_fixed(tmp_path, capsys):
    pcb, fab, qj = tj.make_fab(tmp_path)
    _filled(pcb)
    assert order_submit.main(tj.submit_argv(pcb, fab, qj)) == 1
    assert _steps(_order(fab), "VIA-FILL MISMATCH")
    _quote_fill(qj, "epoxy")
    assert order_submit.main(tj.submit_argv(pcb, fab, qj)) == 0
    order = _order(fab)
    assert not _steps(order, "VIA-FILL MISMATCH")
    assert len(_steps(order, "VIA-FILL:")) == 1


# ---- unfilled via-in-pad


def test_unfilled_via_in_pad_is_tented_and_named_in_the_remark(
        tmp_path, monkeypatch, api_env, capsys):
    pcb, fab, qj = tj.make_fab(tmp_path)
    _via_in_pad(pcb)
    monkeypatch.setattr(order_submit, "_make_session", tj.quote_session)
    assert order_submit.main(tj.submit_argv(pcb, fab, qj, "--api")) == 0
    p = _param(fab)
    assert p["viaCovering"] == 1
    assert re.search(r"Via-in-pad: 1 unfilled via", p["orderDetailsRemark"])
    assert len(_steps(_order(fab), "VIA-IN-PAD:")) == 1


def test_remark_keeps_the_material_line():
    spec = {"layers": 2, "qty": 5, "width_mm": 40, "height_mm": 30,
            "thickness_mm": 0.65, "copper_weight_oz": 1,
            "material": "ro4350b",
            "via_in_pad_unfilled": 2, "via_in_pad_refs": ["U1"]}
    remark = order_submit.build_pcb_param(spec)["orderDetailsRemark"]
    assert "Material:" in remark and "Via-in-pad: 2 unfilled" in remark


def test_remark_lists_at_most_a_few_refs():
    refs = [f"U{i}" for i in range(1, 41)]
    line = order_submit._via_remark({"via_in_pad_unfilled": 40,
                                     "via_in_pad_refs": refs})
    assert "U1," in line and "U40" not in line and "more" in line


# ---- create: the quote file was priced earlier, the board decides now


@pytest.mark.parametrize("plant", [_filled, _via_in_pad])
def test_create_refuses_a_quote_made_before_the_via_change(
        tmp_path, monkeypatch, api_env, capsys, plant):
    pcb, fab, qj = tj.make_fab(tmp_path)
    _plain(pcb)
    fake = tj.quote_session(ship=[{"options": "DHL", "cost": 20.0}])
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    assert order_submit.main(tj.submit_argv(pcb, fab, qj, "--api")) == 0
    plant(pcb)
    if plant is _filled:
        _quote_fill(qj, "epoxy")
    code = order_submit.main(tj.submit_argv(
        pcb, fab, qj, "--api-create",
        "--api-quote-file", str(fab / "api_quote.json"),
        "--confirm", "b1 5pcs 12.5"))
    assert code == 2
    assert "create_order" not in fake.names()
    assert "viaCovering" in capsys.readouterr().out


# ---- where the facts come from


def test_board_file_gone_reads_the_fab_manifest(tmp_path):
    pcb, fab, qj = tj.make_fab(tmp_path)
    pcb.unlink()
    (fab / "manifest.json").write_text(json.dumps(
        {"board_digest": "x", "via_fill": {"note": "VIA-FILL.md",
                                           "filled_vias": 32, "in_pad": 32}}))
    _quote_fill(qj, "epoxy")
    man = order_submit.run(pcb, fab, quote=qj, qty=5)
    assert man["spec_snapshot"]["via_fill"] == "epoxy"
    assert man["spec_snapshot"]["filled_vias"] == 32


def test_manifest_without_the_block_means_a_plain_board(tmp_path):
    pcb, fab, qj = tj.make_fab(tmp_path)
    pcb.unlink()
    (fab / "manifest.json").write_text(json.dumps({"board_digest": "x"}))
    man = order_submit.run(pcb, fab, quote=qj, qty=5)
    assert man["spec_snapshot"]["via_fill"] == "none"


@pytest.mark.parametrize("board", ["gone", "unreadable"])
def test_unknown_via_fill_refuses_the_api(tmp_path, monkeypatch, api_env,
                                          capsys, board):
    """No board and a manifest older than #694, or a board that can't be
    read: nothing says what the vias need, so no order goes out tented."""
    pcb, fab, qj = tj.make_fab(tmp_path)
    if board == "gone":
        pcb.unlink()
    else:
        pcb.write_text("(kicad_pcb (via", encoding="utf-8")
    fake = tj.quote_session()
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    assert order_submit.main(tj.submit_argv(pcb, fab, qj, "--api")) == 2
    assert fake.names() == []
    assert "VIA-FILL UNKNOWN" in capsys.readouterr().out
    assert _steps(_order(fab), "VIA-FILL UNKNOWN")
