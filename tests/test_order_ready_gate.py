"""#712: --api-create spends real money, so it refuses a package the manifest
calls not order-ready for ANY reason, and a quote file whose laminate or
castellation disagrees with the board. Generated steps don't outlive their
cause. Mocked sessions only: this file must never reach a live endpoint."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "lib"))
sys.path.insert(0, str(REPO / "tests"))

import fabmaterial  # noqa: E402
import order_quote  # noqa: E402
import order_submit  # noqa: E402
import test_fabmaterial as tm  # noqa: E402
import test_jlcapi as tj  # noqa: E402

api_env = tj.api_env
CASTELLATED = REPO / "tests" / "fixtures" / "castellated" / "castellated_edge.kicad_pcb"
PLAIN = REPO / "tests" / "fixtures" / "rf_term_edge" / "rf_term_edge.kicad_pcb"
RO_LABEL = fabmaterial.row("ro4350b")["label"]


def _estimate(qj: Path, board: Path) -> None:
    """Replace make_fab's estimate with order_quote's own answer for `board`."""
    q = order_quote.run(board, [5], ["ENIG"], ["green"])
    qj.write_text(json.dumps(q), encoding="utf-8")


def _api_file(fab: Path, **pcb_param) -> Path:
    """A fresh api_quote.json for FR-4 and no castellation, as every real quote
    of a plain board is, with `pcb_param` over it."""
    path = tj.write_api_quote(fab)
    q = json.loads(path.read_text(encoding="utf-8"))
    q["calculate_request"]["pcbParam"].update(pcb_param)
    path.write_text(json.dumps(q), encoding="utf-8")
    return path


def _rogers(tmp_path: Path, pcb: Path) -> None:
    shutil.copy(tm._board(tmp_path, tm.RO), pcb)


def _create(pcb, fab, qj, aq, monkeypatch):
    fake = tj.FakeSession(create_order=tj.ok_resp(tj.CREATE_OK))
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    code = order_submit.main(tj.submit_argv(
        pcb, fab, qj, "--api-create", "--api-quote-file", str(aq),
        "--confirm", "b1 5pcs 12.5"))
    return code, fake


def _order(fab: Path) -> dict:
    return json.loads((fab / "order.json").read_text(encoding="utf-8"))


def _refused(code, fake, fab, capsys, word) -> None:
    assert code == 2
    assert "create_order" not in fake.names()
    assert word in capsys.readouterr().out
    api = _order(fab)["api"]
    assert api["verdict"] == "refused"
    # nothing was sent, so nothing is in doubt: a fixed package can retry
    assert "create_attempt" not in api


# ---- the known answers


def test_rogers_board_on_an_fr4_quote_never_reaches_create(
        tmp_path, monkeypatch, api_env, capsys):
    pcb, fab, qj = tj.make_fab(tmp_path)
    _rogers(tmp_path, pcb)
    code, fake = _create(pcb, fab, qj, _api_file(fab, plateType=1), monkeypatch)
    _refused(code, fake, fab, capsys, "MATERIAL MISMATCH")
    assert _order(fab)["status"] == "not_order_ready"


def test_castellated_board_on_a_plain_quote_never_reaches_create(
        tmp_path, monkeypatch, api_env, capsys):
    pcb, fab, qj = tj.make_fab(tmp_path)
    shutil.copy(CASTELLATED, pcb)
    code, fake = _create(pcb, fab, qj, _api_file(fab, castellatedHoles=0),
                         monkeypatch)
    _refused(code, fake, fab, capsys, "CASTELLATED MISMATCH")
    assert _order(fab)["status"] == "not_order_ready"


def test_plain_ready_board_reaches_create(tmp_path, monkeypatch, api_env,
                                          capsys):
    pcb, fab, qj = tj.make_fab(tmp_path)
    shutil.copy(PLAIN, pcb)
    code, fake = _create(pcb, fab, qj, _api_file(fab), monkeypatch)
    assert code == 0
    assert fake.names() == ["create_order"]
    assert _order(fab)["status"] == "ready_for_human"


def test_rogers_board_on_a_rogers_quote_reaches_create(tmp_path, monkeypatch,
                                                       api_env, capsys):
    pcb, fab, qj = tj.make_fab(tmp_path)
    _rogers(tmp_path, pcb)
    _estimate(qj, pcb)
    aq = _api_file(fab, plateType=5,
                   orderDetailsRemark=f"Material: {RO_LABEL}; thickness 0.65 mm; ENIG")
    code, fake = _create(pcb, fab, qj, aq, monkeypatch)
    assert code == 0
    assert fake.names() == ["create_order"]


def test_castellated_board_on_a_castellated_quote_reaches_create(
        tmp_path, monkeypatch, api_env, capsys):
    pcb, fab, qj = tj.make_fab(tmp_path)
    shutil.copy(CASTELLATED, pcb)
    _estimate(qj, pcb)
    code, fake = _create(pcb, fab, qj, _api_file(fab, castellatedHoles=1),
                         monkeypatch)
    assert code == 0
    assert fake.names() == ["create_order"]


# ---- the estimate is right but the api_quote.json is stale: the manifest reads
# ready, and only the payload's own fields can show it


def test_stale_fr4_api_quote_is_refused_when_the_estimate_matches(
        tmp_path, monkeypatch, api_env, capsys):
    pcb, fab, qj = tj.make_fab(tmp_path)
    _rogers(tmp_path, pcb)
    _estimate(qj, pcb)
    code, fake = _create(pcb, fab, qj, _api_file(fab, plateType=1), monkeypatch)
    _refused(code, fake, fab, capsys, "plateType")
    assert _order(fab)["status"] == "ready_for_human"


def test_rogers_api_quote_on_an_fr4_board_is_refused(tmp_path, monkeypatch,
                                                     api_env, capsys):
    pcb, fab, qj = tj.make_fab(tmp_path)
    shutil.copy(PLAIN, pcb)
    aq = _api_file(fab, plateType=5,
                   orderDetailsRemark=f"Material: {RO_LABEL}; thickness 0.65 mm; ENIG")
    code, fake = _create(pcb, fab, qj, aq, monkeypatch)
    _refused(code, fake, fab, capsys, "plateType")


@pytest.mark.parametrize("board, holes", [(CASTELLATED, 0), (PLAIN, 1)])
def test_stale_castellation_in_the_api_quote_is_refused(
        tmp_path, monkeypatch, api_env, capsys, board, holes):
    pcb, fab, qj = tj.make_fab(tmp_path)
    shutil.copy(board, pcb)
    _estimate(qj, pcb)
    code, fake = _create(pcb, fab, qj, _api_file(fab, castellatedHoles=holes),
                         monkeypatch)
    _refused(code, fake, fab, capsys, "castellatedHoles")


@pytest.mark.parametrize("field", ["plateType", "castellatedHoles"])
def test_api_quote_that_does_not_say_is_refused(tmp_path, monkeypatch, api_env,
                                                capsys, field):
    """Every real quote carries both, and create sends the file verbatim."""
    pcb, fab, qj = tj.make_fab(tmp_path)
    shutil.copy(PLAIN, pcb)
    aq = _api_file(fab)
    q = json.loads(aq.read_text(encoding="utf-8"))
    del q["calculate_request"]["pcbParam"][field]
    aq.write_text(json.dumps(q), encoding="utf-8")
    code, fake = _create(pcb, fab, qj, aq, monkeypatch)
    _refused(code, fake, fab, capsys, field)


def test_same_plate_type_other_laminate_is_refused():
    """RO4350B and PTFE share plateType 5: only the remark tells JLC which."""
    man = {"spec_snapshot": {"material": "ptfe_zyf", "castellated_pads": 0}}
    q = {"calculate_request": {"pcbParam": {
        "plateType": 5, "castellatedHoles": 0,
        "orderDetailsRemark": f"Material: {RO_LABEL}; thickness 0.65 mm; ENIG"}}}
    with pytest.raises(order_submit.ApiRefused, match="PTFE"):
        order_submit._check_quote_board(man, q)


# ---- any reason at all


def test_incomplete_package_never_reaches_create(tmp_path, monkeypatch, api_env,
                                                 capsys):
    pcb, fab, qj = tj.make_fab(tmp_path)
    shutil.copy(PLAIN, pcb)
    aq = _api_file(fab)
    (fab / "BOM.csv").unlink()
    code, fake = _create(pcb, fab, qj, aq, monkeypatch)
    _refused(code, fake, fab, capsys, "BOM")
    assert _order(fab)["status"] == "incomplete"


def test_via_fill_mismatch_never_reaches_create(tmp_path, monkeypatch, api_env,
                                                capsys):
    pcb, fab, qj = tj.make_fab(tmp_path)
    shutil.copy(PLAIN, pcb)
    q = json.loads(qj.read_text(encoding="utf-8"))
    q["matrix"][0]["via_fill"] = "epoxy"
    qj.write_text(json.dumps(q), encoding="utf-8")
    code, fake = _create(pcb, fab, qj, _api_file(fab), monkeypatch)
    _refused(code, fake, fab, capsys, "VIA-FILL MISMATCH")


@pytest.mark.parametrize("status", ["not_order_ready", "incomplete", "new_kind",
                                    None])
def test_any_status_but_ready_refuses_with_or_without_a_reason(
        tmp_path, monkeypatch, api_env, capsys, status):
    """A whitelist: a status added later, with no problem list the gate has
    heard of, still can't spend money."""
    pcb, fab, qj = tj.make_fab(tmp_path)
    shutil.copy(PLAIN, pcb)
    real_run = order_submit.run

    def run(*args, **kwargs):
        return {**real_run(*args, **kwargs), "status": status}

    monkeypatch.setattr(order_submit, "run", run)
    code, fake = _create(pcb, fab, qj, _api_file(fab), monkeypatch)
    _refused(code, fake, fab, capsys, "not order-ready")


def test_recorded_order_still_reports_the_latch_first(tmp_path, monkeypatch,
                                                      api_env, capsys):
    """A placed order's message is about the order, not about the package."""
    pcb, fab, qj = tj.make_fab(tmp_path)
    _rogers(tmp_path, pcb)
    (fab / "order.json").write_text(json.dumps({
        "script": "order_submit", "status": "ready_for_human",
        "api": {"verdict": "created", "order": {"batchNum": "W1"}},
        "human_steps": []}), encoding="utf-8")
    code, fake = _create(pcb, fab, qj, _api_file(fab), monkeypatch)
    assert code == 2 and "create_order" not in fake.names()
    assert "already recorded" in capsys.readouterr().out


def test_castellated_quote_on_a_plain_board_is_not_order_ready(tmp_path):
    pcb, fab, qj = tj.make_fab(tmp_path)
    shutil.copy(PLAIN, pcb)
    _estimate(qj, CASTELLATED)
    man = order_submit.run(pcb, fab, quote=qj, qty=5)
    assert man["status"] == "not_order_ready"
    assert any(s.startswith("CASTELLATED MISMATCH:") for s in man["human_steps"])


def test_quote_leg_refuses_a_castellation_mismatch_before_any_network(
        tmp_path, monkeypatch, api_env, capsys):
    pcb, fab, qj = tj.make_fab(tmp_path)
    shutil.copy(CASTELLATED, pcb)
    fake = tj.quote_session()
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    assert order_submit.main(tj.submit_argv(pcb, fab, qj, "--api")) == 2
    assert fake.names() == []
    assert "CASTELLATED MISMATCH" in capsys.readouterr().out


# ---- steps that outlive their cause


def _run_twice(pcb, fab, qj, fix) -> dict:
    """First run writes the step, `fix` removes its cause, second run rebuilds."""
    argv = tj.submit_argv(pcb, fab, qj)
    order_submit.main(argv)
    fix()
    assert order_submit.main(argv) == 0
    return _order(fab)


def test_material_mismatch_step_clears_after_the_requote(tmp_path, capsys):
    pcb, fab, qj = tj.make_fab(tmp_path)
    _rogers(tmp_path, pcb)
    man = _run_twice(pcb, fab, qj, lambda: _estimate(qj, pcb))
    assert man["status"] == "ready_for_human"
    assert not [s for s in man["human_steps"] if "MATERIAL MISMATCH" in s]


def test_castellation_mismatch_step_clears_after_the_requote(tmp_path, capsys):
    pcb, fab, qj = tj.make_fab(tmp_path)
    shutil.copy(CASTELLATED, pcb)
    man = _run_twice(pcb, fab, qj, lambda: _estimate(qj, pcb))
    assert man["status"] == "ready_for_human"
    assert not [s for s in man["human_steps"] if "CASTELLATED MISMATCH" in s]
    assert len([s for s in man["human_steps"] if s.startswith("CASTELLATED:")]) == 1


def test_board_steps_clear_when_the_board_changes(tmp_path, capsys):
    """A Rogers or castellated board respun as plain FR-4 stops asking for them."""
    pcb, fab, qj = tj.make_fab(tmp_path)
    _rogers(tmp_path, pcb)
    argv = tj.submit_argv(pcb, fab, qj)
    order_submit.main(argv)
    assert [s for s in _order(fab)["human_steps"] if "MATERIAL MISMATCH" in s]

    def respin():
        shutil.copy(PLAIN, pcb)
        _estimate(qj, pcb)

    respin()
    assert order_submit.main(argv) == 0
    steps = _order(fab)["human_steps"]
    assert not [s for s in steps if "MATERIAL" in s or "CASTELLATED" in s]


def test_hand_written_steps_survive(tmp_path, capsys):
    pcb, fab, qj = tj.make_fab(tmp_path)
    _rogers(tmp_path, pcb)
    order_submit.main(tj.submit_argv(pcb, fab, qj))
    man = _order(fab)
    note = "BOARD-SPECIFIC: MATERIAL MISMATCH was checked by hand, keep this."
    man["human_steps"].insert(0, note)
    (fab / "order.json").write_text(json.dumps(man), encoding="utf-8")
    _estimate(qj, pcb)
    assert order_submit.main(tj.submit_argv(pcb, fab, qj)) == 0
    assert note in _order(fab)["human_steps"]


# --- a board nobody read proves nothing --------------------------------------

def test_mistyped_board_path_never_reaches_create(tmp_path, monkeypatch,
                                                  api_env, capsys):
    """With the board file gone its laminate falls back to the quote's, so the
    quote would agree with itself. A fab manifest keeps the via check quiet."""
    pcb, fab, qj = tj.make_fab(tmp_path)
    _rogers(tmp_path, pcb)
    (fab / "manifest.json").write_text(json.dumps(
        {"board_digest": "x", "via_fill": {"filled_vias": 0, "in_pad": 0}}),
        encoding="utf-8")
    code, fake = _create(tmp_path / "typo.kicad_pcb", fab, qj,
                         _api_file(fab), monkeypatch)
    _refused(code, fake, fab, capsys, "BOARD NOT READ")


def test_package_only_run_without_the_board_stays_as_it_was(tmp_path, capsys):
    """The stand-in is deliberate for the human package: no API leg, no refusal."""
    pcb, fab, qj = tj.make_fab(tmp_path)
    (fab / "manifest.json").write_text(json.dumps(
        {"board_digest": "x", "via_fill": {"filled_vias": 0, "in_pad": 0}}),
        encoding="utf-8")
    man = order_submit.run(tmp_path / "typo.kicad_pcb", fab, quote=qj, qty=5)
    assert man["status"] == order_submit.ORDER_READY
    assert not man.get("board_problems")
