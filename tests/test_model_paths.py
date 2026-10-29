"""3D model paths that survive a checkout move (check_model_paths.py, the
kc.py helpers it shares with render's relink, and lib_pull's rewrite).

Eleven boards named their models by absolute path into the track worktree
that built them; once the worktree went, the models were gone. Everything
here runs on small fixtures; one test repairs a /tmp copy of a real board and
skips without the boards repo.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".claude" / "skills" / "hwde" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_model_paths as cmp  # noqa: E402
import kc  # noqa: E402
import lib_pull  # noqa: E402
import verify_all  # noqa: E402

GONE = "/gone/worktrees/boards/t/ws/lib/aiee.3dshapes"
STOCK = "${KICAD10_3DMODEL_DIR}/Connector_PinHeader_2.54mm.3dshapes/H.step"


def _fp(ref: str, at: str, *models: str) -> str:
    ms = "".join(f'\t\t(model "{m}"\n\t\t\t(offset (xyz 0 0 0))\n\t\t)\n'
                 for m in models)
    return (f'\t(footprint "aiee:{ref}"\n\t\t(layer "F.Cu")\n'
            f'\t\t(at {at})\n\t\t(property "Reference" "{ref}"\n'
            f'\t\t\t(at 0 -2 0)\n\t\t)\n{ms}\t)\n')


def _ws(tmp_path: Path, *fps: str, state: bool = True) -> Path:
    """<ws>/kicad/b.kicad_pcb with <ws>/lib/aiee.3dshapes holding R.wrl and
    USBC.wrl, and <ws>/lib/aiee.pretty/R.kicad_mod naming R.wrl absolutely."""
    ws = tmp_path / "ws"
    shapes = ws / "lib" / "aiee.3dshapes"
    shapes.mkdir(parents=True)
    for n in ("R.wrl", "USBC.wrl"):
        (shapes / n).write_text("#VRML V2.0 utf8\n", encoding="utf-8")
    pretty = ws / "lib" / "aiee.pretty"
    pretty.mkdir()
    (pretty / "R.kicad_mod").write_text(
        f'(footprint "R"\n\t(model "{GONE}/R.wrl"\n\t)\n)\n', encoding="utf-8")
    (ws / "kicad").mkdir()
    if state:
        (ws / "state.json").write_text("{}", encoding="utf-8")
    pcb = ws / "kicad" / "b.kicad_pcb"
    pcb.write_text("(kicad_pcb\n\t(version 20241229)\n" + "".join(fps) + ")\n",
                   encoding="utf-8")
    return pcb


# ------------------------------------------------------------ kc helpers

@pytest.mark.parametrize("path,kind", [
    (STOCK, None),
    ("${KICAD9_3DMODEL_DIR}/X.3dshapes/Y.step", None),
    ("${KIPRJMOD}/../lib/aiee.3dshapes/R.wrl", None),
    ("${KIPRJMOD}/lib/aiee.3dshapes/R.wrl", None),
    (f"{GONE}/R.wrl", "absolute"),
    ("C:/dev/ai-ee3/boards/b/lib/aiee.3dshapes/R.wrl", "absolute"),
    ("C:\\\\dev\\\\b\\\\R.wrl", "absolute"),
    ("${KIPRJMOD}/../../../lib/aiee.3dshapes/R.wrl", "outside_workspace"),
    ("boards/b/lib/aiee.3dshapes/R.wrl", "unanchored"),
    ("${SOME_VAR}/R.wrl", "unanchored"),
])
def test_model_path_kind(tmp_path, path, kind):
    ws = tmp_path / "boards" / "ws"
    (ws / "kicad").mkdir(parents=True)
    assert kc.model_path_kind(path, ws / "kicad", ws) == kind


def test_model_paths_portable_rewrites_only_what_the_lib_holds(tmp_path):
    pcb = _ws(tmp_path)
    proj, ws = pcb.parent, pcb.parent.parent
    text = (f'(model "{GONE}/R.wrl")\n(model "{GONE}/R.wrl")\n'
            f'(model "/elsewhere/Missing.wrl")\n(model "{STOCK}")\n')
    new, done = kc.model_paths_portable(text, proj, ws, kc.model_libs(proj))
    rel = "${KIPRJMOD}/../lib/aiee.3dshapes/R.wrl"
    assert done == {f"{GONE}/R.wrl": rel}
    assert new.count(f'(model "{rel}")') == 2
    assert '(model "/elsewhere/Missing.wrl")' in new and STOCK in new
    # already portable: nothing to do
    assert kc.model_paths_portable(new, proj, ws, kc.model_libs(proj)) \
        == (new, {})


def test_portable_path_for_a_lib_beside_the_board(tmp_path):
    (tmp_path / "lib" / "x.3dshapes").mkdir(parents=True)
    f = tmp_path / "lib" / "x.3dshapes" / "A.wrl"
    f.write_text("", encoding="utf-8")
    assert kc.model_lib_file("/old/A.wrl", kc.model_libs(tmp_path)) == f
    assert kc.portable_model_path(f, tmp_path) == "${KIPRJMOD}/lib/x.3dshapes/A.wrl"


# ------------------------------------------------------------ the check

def test_check_flags_absolute_once_per_path_with_a_fix_line(tmp_path):
    pcb = _ws(tmp_path,
              _fp("R1", "10 20", f"{GONE}/R.wrl"),
              _fp("R2", "30 20", f"{GONE}/R.wrl"),
              _fp("J1", "50 20", STOCK),
              _fp("U1", "70 20", "/elsewhere/Missing.wrl"))
    payload, _ = cmp.run(["--pcb", str(pcb)])
    assert payload["status"] == "violations"
    by = {v["model_path"]: v for v in payload["violations"]}
    assert set(by) == {f"{GONE}/R.wrl", "/elsewhere/Missing.wrl"}
    r = by[f"{GONE}/R.wrl"]
    assert r["kind"] == "model_path_absolute" and r["severity"] == "error"
    assert r["refs"] == ["R1", "R2"] and r["pos"] == [10.0, 20.0]
    assert r["fixable"] and "--fix" in r["fix"]
    assert "${KIPRJMOD}/../lib/aiee.3dshapes/R.wrl" in r["msg"]
    u = by["/elsewhere/Missing.wrl"]
    assert not u["fixable"] and "lib_pull.py" in u["fix"]
    assert payload["workspace"] == str(pcb.parent.parent)


def test_check_flags_outside_workspace_and_unanchored(tmp_path):
    pcb = _ws(tmp_path,
              _fp("R1", "0 0", "${KIPRJMOD}/../../../lib/aiee.3dshapes/R.wrl"),
              _fp("R2", "1 0", "boards/b/lib/aiee.3dshapes/R.wrl"))
    kinds = sorted(v["kind"] for v in cmp.run(["--pcb", str(pcb)])[0]
                   ["violations"])
    assert kinds == ["model_path_outside_workspace", "model_path_unanchored"]


def test_clean_board_passes(tmp_path):
    pcb = _ws(tmp_path, _fp("R1", "0 0",
                            "${KIPRJMOD}/../lib/aiee.3dshapes/R.wrl"),
              _fp("J1", "1 0", STOCK))
    payload, _ = cmp.run(["--pcb", str(pcb)])
    assert payload["status"] == "pass" and "fixed" not in payload


def test_fix_rewrites_board_and_lib_footprints_and_is_idempotent(tmp_path):
    pcb = _ws(tmp_path,
              _fp("R1", "10 20", f"{GONE}/R.wrl"),
              _fp("J1", "50 20", f"{GONE}/USBC.wrl"),
              _fp("U1", "70 20", "/elsewhere/Missing.wrl"))
    # a CRLF board stays CRLF: only the path text changes
    pcb.write_bytes(pcb.read_bytes().replace(b"\n", b"\r\n"))
    before = pcb.read_bytes()
    payload, _ = cmp.run(["--pcb", str(pcb), "--fix"])
    fixed = payload["fixed"]
    assert fixed[str(pcb)] == {
        f"{GONE}/R.wrl": "${KIPRJMOD}/../lib/aiee.3dshapes/R.wrl",
        f"{GONE}/USBC.wrl": "${KIPRJMOD}/../lib/aiee.3dshapes/USBC.wrl"}
    mod = pcb.parent.parent / "lib" / "aiee.pretty" / "R.kicad_mod"
    assert str(mod) in fixed
    assert "${KIPRJMOD}/../lib/aiee.3dshapes/R.wrl" in mod.read_text("utf-8")
    after = pcb.read_bytes()
    assert after.count(b"\r\n") == before.count(b"\r\n")
    assert before.replace(GONE.encode(),
                          b"${KIPRJMOD}/../lib/aiee.3dshapes") == after
    # the unfixable path stays and is still reported
    assert [v["model_path"] for v in payload["violations"]] == \
        ["/elsewhere/Missing.wrl"]
    assert kc.model_audit(pcb)["missing"] == ["/elsewhere/Missing.wrl"]
    again, _ = cmp.run(["--pcb", str(pcb), "--fix"])
    assert again["fixed"] == {} and pcb.read_bytes() == after


def test_workspace_falls_back_to_the_dir_holding_lib(tmp_path):
    pcb = _ws(tmp_path, state=False)
    assert cmp.workspace_of(pcb) == pcb.parent.parent
    bare = tmp_path / "bare" / "b.kicad_pcb"
    bare.parent.mkdir()
    bare.write_text("(kicad_pcb)", encoding="utf-8")
    assert cmp.workspace_of(bare) == bare.parent.resolve()


def test_cli_exit_codes(tmp_path, capsys):
    pcb = _ws(tmp_path, _fp("R1", "0 0", f"{GONE}/R.wrl"))
    out = tmp_path / "r.json"
    assert cmp.main(["--pcb", str(pcb), "--out", str(out)]) == 1
    assert json.loads(out.read_text("utf-8"))["counts"]["total"] == 1
    assert cmp.main(["--pcb", str(pcb), "--fix", "--out", str(out)]) == 0
    assert cmp.main(["--pcb", str(tmp_path / "nope.kicad_pcb")]) == 2
    assert '"status": "error"' in capsys.readouterr().out


def test_registered_in_verify_suite():
    names = [c["name"] for c in verify_all.load_checks()]
    assert "check_model_paths" in names


# ------------------------------------------------------------ lib_pull

def test_lib_pull_writes_kiprjmod_relative_models(tmp_path):
    pcb = _ws(tmp_path)
    lib = pcb.parent.parent / "lib"
    r = lib_pull._portable_models(lib / "aiee.pretty", lib / "aiee.3dshapes",
                                  pcb.parent)
    assert r == {"rewritten": 1, "footprints": {
        "R.kicad_mod": ["${KIPRJMOD}/../lib/aiee.3dshapes/R.wrl"]}}
    assert lib_pull._portable_models(lib / "aiee.pretty",
                                     lib / "aiee.3dshapes",
                                     pcb.parent)["rewritten"] == 0


def test_lib_pull_rewrites_for_a_new_explicit_project(tmp_path):
    # --project <new>/kicad does not exist until _register_project runs
    pcb = _ws(tmp_path)
    lib = pcb.parent.parent / "lib"
    new = pcb.parent.parent / "kicad-new"
    r = lib_pull._portable_models(lib / "aiee.pretty", lib / "aiee.3dshapes",
                                  new, explicit=True)
    assert r == {"rewritten": 1, "footprints": {
        "R.kicad_mod": ["${KIPRJMOD}/../lib/aiee.3dshapes/R.wrl"]}}
    assert not new.exists()


def test_lib_pull_leaves_paths_without_a_project(tmp_path):
    pcb = _ws(tmp_path)
    lib = pcb.parent.parent / "lib"
    r = lib_pull._portable_models(lib / "aiee.pretty", lib / "aiee.3dshapes",
                                  tmp_path / "no-such-kicad")
    assert r["rewritten"] == 0 and "left absolute" in r["skipped"]
    r = lib_pull._portable_models(lib / "aiee.pretty", lib / "none.3dshapes",
                                  pcb.parent)
    assert r["rewritten"] == 0 and "--no-3d" in r["skipped"]
    assert GONE in (lib / "aiee.pretty" / "R.kicad_mod").read_text("utf-8")


# ------------------------------------------------------------ gate freshness

PCB_GATES = ["place", "drc_routed", "verify", "dfm"]


def _recorded_ws(tmp_path: Path, crlf: bool = False):
    """A fixture board with a real v2 state.json: the four board-reading
    gates passed against it, drc failed, erc passed (it does not read the
    board)."""
    import state as state_mod
    pcb = _ws(tmp_path, _fp("R1", "10 20", f"{GONE}/R.wrl"),
              _fp("J1", "50 20", f"{GONE}/USBC.wrl"))
    if crlf:
        pcb.write_bytes(pcb.read_bytes().replace(b"\n", b"\r\n"))
    ws = pcb.parent.parent
    st = state_mod.State.init(ws, "b", "P9", force=True)
    for g in PCB_GATES + ["erc"]:
        st.record_gate(g, {"status": "pass"})
    st.record_gate("drc", {"status": "fail", "failing_count": 1})
    st.save()
    return pcb, ws, state_mod


def _fresh(state_mod, ws: Path) -> list[str]:
    return state_mod.State.load(ws / "state.json").freshness()["summary"][
        "fresh"]


@pytest.mark.parametrize("crlf", [False, True])
def test_fix_keeps_passed_gates_fresh(tmp_path, crlf):
    """bb-buck, 2026-10-08: --fix rewrote model paths only and place,
    drc_routed, verify and dfm read as stale. They stay fresh now; the failed
    drc is not carried over and goes stale like any edit would make it. A
    CRLF board hashes the same way from the bytes read as from the file."""
    pcb, ws, state_mod = _recorded_ws(tmp_path, crlf)
    assert _fresh(state_mod, ws) == sorted(PCB_GATES + ["erc", "drc"])
    payload, _ = cmp.run(["--pcb", str(pcb), "--fix"])
    assert str(pcb) in payload["fixed"]
    assert payload["restamped"] == {"gates": sorted(PCB_GATES)}
    assert _fresh(state_mod, ws) == sorted(PCB_GATES + ["erc"])
    st = state_mod.State.load(ws / "state.json")
    assert st.data["artifacts"]["pcb"]["sha256"] == \
        st.freshness()["gates"]["verify"]["current_inputs"]["pcb"]
    assert st.data["history"][-1]["event"] == "restamp"
    # the board is clean now: a second --fix rewrites and restamps nothing
    again, _ = cmp.run(["--pcb", str(pcb), "--fix"])
    assert again["fixed"] == {} and "restamped" not in again


def test_geometry_edit_still_stales_the_gates(tmp_path):
    """A footprint move stales every board-reading gate, and a --fix after it
    does not revive them: they were recorded against another board."""
    pcb, ws, state_mod = _recorded_ws(tmp_path)
    pcb.write_text(pcb.read_text("utf-8").replace("(at 10 20)", "(at 11 20)"),
                   encoding="utf-8")
    assert _fresh(state_mod, ws) == ["erc"]
    payload, _ = cmp.run(["--pcb", str(pcb), "--fix"])
    assert str(pcb) in payload["fixed"]
    assert payload["restamped"] == {"gates": []}
    assert _fresh(state_mod, ws) == ["erc"]


def test_restamp_skips_what_it_cannot_prove(tmp_path, monkeypatch):
    # no v2 state: the fixture's "{}" is not a state file
    pcb = _ws(tmp_path, _fp("R1", "10 20", f"{GONE}/R.wrl"))
    payload, _ = cmp.run(["--pcb", str(pcb), "--fix"])
    assert payload["restamped"]["gates"] == []
    assert "unreadable" in payload["restamped"]["skipped"]
    # no state.json at all
    pcb = _ws(tmp_path / "n", _fp("R1", "10 20", f"{GONE}/R.wrl"),
              state=False)
    payload, _ = cmp.run(["--pcb", str(pcb), "--fix"])
    assert payload["restamped"]["skipped"] == "no state.json"
    # a board that is not the workspace's pcb kind
    pcb, ws, state_mod = _recorded_ws(tmp_path / "o")
    other = pcb.with_name("other.kicad_pcb")
    pcb.rename(other)
    payload, _ = cmp.run(["--pcb", str(other), "--fix"])
    assert "not this file" in payload["restamped"]["skipped"]
    # a rewrite that touched more than model paths
    pcb, ws, state_mod = _recorded_ws(tmp_path / "g")
    real = kc.model_paths_portable

    def moving(text, *a):
        new, done = real(text, *a)
        return new.replace("(at 10 20)", "(at 11 20)"), done
    monkeypatch.setattr(kc, "model_paths_portable", moving)
    payload, _ = cmp.run(["--pcb", str(pcb), "--fix"])
    assert "more than model paths" in payload["restamped"]["skipped"]
    assert _fresh(state_mod, ws) == ["erc"]


@pytest.mark.parametrize("edit", [
    ("(offset (xyz 0 0 0))", "(offset (xyz 0 0 1))"),
    ("(offset (xyz 0 0 0))", "(offset (xyz 0 0 0)) (scale (xyz 2 2 2))"),
    ("(offset (xyz 0 0 0))", "(offset (xyz 0 0 0)) (rotate (xyz 0 0 90))"),
])
def test_a_changed_model_transform_blocks_the_restamp(tmp_path, monkeypatch,
                                                      edit):
    """Only the path string may differ: a rewrite that also moved, scaled or
    turned a model is not provably harmless, so the gates go stale."""
    pcb, ws, state_mod = _recorded_ws(tmp_path)
    real = kc.model_paths_portable

    def turning(text, *a):
        new, done = real(text, *a)
        return new.replace(*edit, 1), done
    monkeypatch.setattr(kc, "model_paths_portable", turning)
    payload, _ = cmp.run(["--pcb", str(pcb), "--fix"])
    assert "more than model paths" in payload["restamped"]["skipped"]
    assert _fresh(state_mod, ws) == ["erc"]


def test_a_state_written_meanwhile_is_reported_not_a_crash(tmp_path,
                                                            monkeypatch):
    """Another writer lands on state.json between load and save: the CAS
    refuses, the board stays fixed, the report says why the gates went
    stale and the exit code is the fix's, not an error."""
    pcb, ws, state_mod = _recorded_ws(tmp_path)
    real = state_mod.State.restamp_input

    def racing(self, *a, **kw):
        sp = ws / "state.json"
        sp.write_text(sp.read_text("utf-8") + " ", encoding="utf-8")
        return real(self, *a, **kw)
    monkeypatch.setattr(state_mod.State, "restamp_input", racing)
    out = tmp_path / "r.json"
    assert cmp.main(["--pcb", str(pcb), "--fix", "--out", str(out)]) == 0
    payload = json.loads(out.read_text("utf-8"))
    assert payload["restamped"]["gates"] == []
    assert "state.json not updated" in payload["restamped"]["skipped"]
    assert str(pcb) in payload["fixed"]
    assert _fresh(state_mod, ws) == ["erc"]


# ------------------------------------------------------------ a real board

def test_fix_on_a_copy_of_a_real_board(tmp_path):
    import _boards
    src = _boards.real_board("esp32c3-node")
    pcbs = sorted((src / "kicad").glob("*.kicad_pcb"))
    if not pcbs or not (src / "lib").is_dir():
        pytest.skip(f"{src} has no kicad/*.kicad_pcb or lib/")
    ws = tmp_path / src.name
    (ws / "kicad").mkdir(parents=True)
    shutil.copytree(src / "lib", ws / "lib")
    shutil.copy2(pcbs[0], ws / "kicad" / pcbs[0].name)
    (ws / "state.json").write_text("{}", encoding="utf-8")
    pcb = ws / "kicad" / pcbs[0].name
    payload, _ = cmp.run(["--pcb", str(pcb), "--fix"])
    assert payload["status"] == "pass", payload["violations"][:3]
    missing = kc.model_audit(pcb)["missing"]
    assert all(m.startswith("${KICAD") for m in missing), missing
