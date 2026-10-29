"""lib/boardreg.py: a workspace's part number comes from the register.yaml
beside it (the boards repo root), matched on the rev's `dir`; hwde never
invents one."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / ".claude" / "skills" / "hwde" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

from lib import boardreg, env  # noqa: E402

REG = """products:
  PCB-0001:
    title: USB-C PD trigger
    revs:
      A: {dir: pd-lite, date: 2026-09-24, bom: pd-lite/fab/BOM.csv}
      B: {dir: pd-lite-dip, date: 2026-09-24, change: "DIP switch"}
  PCB-0002:
    title: buck
    revs:
      A: {dir: buck, date: 2026-09-01}
"""


def boards(tmp_path: Path, reg: str | None = REG) -> Path:
    root = tmp_path / "root"
    for d in ("pd-lite", "pd-lite-dip", "buck", "stranger"):
        (root / d).mkdir(parents=True)
    if reg is not None:
        (root / "register.yaml").write_text(reg, encoding="utf-8")
    return root


def test_listed_board_gets_product_and_rev(tmp_path):
    root = boards(tmp_path)
    pn, why = boardreg.part_number(root / "pd-lite-dip")
    assert why == ""
    assert (pn["pn"], pn["product"], pn["rev"], pn["title"]) == (
        "PCB-0001-B", "PCB-0001", "B", "USB-C PD trigger")
    assert boardreg.part_number(root / "buck")[0]["pn"] == "PCB-0002-A"


def test_unlisted_board_has_no_number(tmp_path):
    root = boards(tmp_path)
    pn, why = boardreg.part_number(root / "stranger")
    assert pn is None and "stranger is not in" in why


def test_no_register_has_no_number(tmp_path):
    root = boards(tmp_path, reg=None)
    pn, why = boardreg.part_number(root / "pd-lite")
    assert pn is None and "no register.yaml" in why


def test_duplicate_or_malformed_entry_has_no_number(tmp_path):
    root = boards(tmp_path, REG + """  PCB-0003:
    title: copy
    revs:
      A: {dir: buck}
  PCB-12:
    title: bad
    revs:
      O: {dir: pd-lite}
""")
    pn, why = boardreg.part_number(root / "buck")
    assert pn is None and "listed 2 times" in why
    pn, why = boardreg.part_number(root / "pd-lite")   # listed twice too
    assert pn is None
    root2 = boards(tmp_path / "b", """products:
  PCB-12:
    revs:
      O: {dir: buck}
""")
    pn, why = boardreg.part_number(root2 / "buck")
    assert pn is None and "not a PCB-NNNN-R" in why


def test_unreadable_register_has_no_number(tmp_path):
    root = boards(tmp_path, "products: [unclosed")
    pn, why = boardreg.part_number(root / "buck")
    assert pn is None and "unreadable" in why


def test_boards_root_env(monkeypatch, tmp_path):
    monkeypatch.setenv("HWDE_BOARDS_ROOT", str(tmp_path / "elsewhere"))
    assert env.boards_root() == tmp_path / "elsewhere"
    assert env.is_boards_dir(tmp_path / "elsewhere")
    assert not env.is_boards_dir(tmp_path)
    assert env.is_boards_dir(tmp_path / "x" / "boards")    # fixture layout
    monkeypatch.delenv("HWDE_BOARDS_ROOT")
    monkeypatch.delenv("AIEE_BOARDS_ROOT", raising=False)
    assert env.boards_root() == Path.home() / "dev" / "boards"


# ---- <PN>_<name> directories (owner, #ai-ee: "project names/folders should
# be PN_[human-name]") ----------------------------------------------------

NUMBERED = """products:
  PCB-0016:
    title: pd trigger lite
    revs:
      A: {dir: PCB-0016-A_pd-lite, date: 2026-09-24}
      B: {dir: pd-lite-dip, date: 2026-09-24}
  PCB-0020:
    title: issued, not built yet
    revs:
      A: {dir: PCB-0020-A_fresh, date: 2026-09-25}
"""


def numbered(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    for d in ("PCB-0016-A_pd-lite", "pd-lite-dip", "stray"):
        (root / d).mkdir(parents=True)
    (root / "register.yaml").write_text(NUMBERED, encoding="utf-8")
    return root


def test_split_dir():
    assert boardreg.split_dir("PCB-0016-A_pd-trigger-lite") == (
        "PCB-0016-A", "pd-trigger-lite")
    assert boardreg.split_dir("pd-trigger-lite") == (None, "pd-trigger-lite")
    assert boardreg.split_dir("PCB-0016-I_x") == (None, "PCB-0016-I_x")


def test_resolve_by_old_name_pn_or_new_dir(tmp_path):
    root = numbered(tmp_path)
    new = root / "PCB-0016-A_pd-lite"
    assert boardreg.resolve("pd-lite", root) == new
    assert boardreg.resolve("PCB-0016-A", root) == new
    assert boardreg.resolve("PCB-0016-A_pd-lite", root) == new
    assert boardreg.resolve("PCB-0016-B", root) == root / "pd-lite-dip"
    assert boardreg.resolve("pd-lite-dip", root) == root / "pd-lite-dip"
    # issued but not created, unlisted, or unknown: none, never a guess
    assert boardreg.resolve("PCB-0020-A", root) is None
    assert boardreg.resolve("nope", root) is None
    assert boardreg.resolve("PCB-0099-A", root) is None


def test_resolve_lone_pn_dir_without_register(tmp_path):
    root = tmp_path / "root"
    (root / "PCB-0003-A_solo").mkdir(parents=True)
    assert boardreg.resolve("solo", root) == root / "PCB-0003-A_solo"
    (root / "PCB-0003-B_solo").mkdir()
    assert boardreg.resolve("solo", root) is None   # two: ambiguous


def test_new_dir_takes_the_register_issued_name(tmp_path):
    root = numbered(tmp_path)
    assert boardreg.new_dir("fresh", root) == "PCB-0020-A_fresh"
    assert boardreg.new_dir("fresh", root, "PCB-0020-A") == "PCB-0020-A_fresh"
    assert boardreg.new_dir("unissued", root) == "unissued"
    try:
        boardreg.new_dir("x", root, "PCB-0099-A")
    except ValueError as exc:
        assert "PCB-0099-A is not in" in str(exc)
    else:
        raise AssertionError("an unlisted --pn must be refused")


def test_locate(tmp_path):
    root = numbered(tmp_path)
    assert boardreg.locate("pd-lite", root) == root / "PCB-0016-A_pd-lite"
    assert boardreg.locate(root / "pd-lite", tmp_path) == \
        root / "PCB-0016-A_pd-lite"
    assert boardreg.locate("fresh", root) == root / "PCB-0020-A_fresh"
    assert boardreg.locate("nope", root) == Path("nope")


def test_project_stem_finds_the_kicad_pro(tmp_path):
    from lib import statelib
    ws = tmp_path / "PCB-0016-A_pd-lite"
    kicad = ws / "kicad"
    kicad.mkdir(parents=True)
    # new numbered board, nothing written yet: the directory's own name
    assert statelib.project_stem(ws, "pd-lite") == "PCB-0016-A_pd-lite"
    (kicad / "pd-lite.kicad_pro").write_text("{}")
    assert statelib.project_stem(ws, "pd-lite") == "pd-lite"
    (kicad / "pd-lite.kicad_pro").rename(kicad / "PCB-0016-A_pd-lite.kicad_pro")
    assert statelib.project_stem(ws, "pd-lite") == "PCB-0016-A_pd-lite"
    imap = statelib.load_map()
    assert statelib.kind_path("pcb", "pd-lite", imap, {}, ws) == \
        "kicad/PCB-0016-A_pd-lite.kicad_pcb"
    assert statelib.kind_path("pcb", "pd-lite", imap, {}) == \
        "kicad/pd-lite.kicad_pcb"
    (kicad / "other.kicad_pro").write_text("{}")
    assert statelib.project_stem(ws, "pd-lite") == "pd-lite"  # several
    assert statelib.project_stem(tmp_path / "bare", "bare") == "bare"
