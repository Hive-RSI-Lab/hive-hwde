"""The leak guard for #757: tests resolve 3D models against tests/fixtures/3dmodels, not the host."""
import os
import sys
from pathlib import Path

import pytest

from conftest import MODELS, MODEL_VARS

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures" / "3dmodels"))
import make_boxes  # noqa: E402


def test_model_dirs_point_at_the_fixture():
    for k in MODEL_VARS:
        assert Path(os.environ[k]) == MODELS, f"{k} leaks the host's library into a test"


KNOWN_ABSENT = ["Connector_USB.3dshapes/USB_Micro-B_Amphenol_10118194_Horizontal",
                "RF_Module.3dshapes/HOPERF_RFM9XW_SMD"]  # usbbuck4 J1, rf4 U1


def test_every_model_a_golden_board_names_has_a_fixture_or_is_absent_on_purpose():
    """A golden that names a stock model needs a box for it
    (python3 -I tests/fixtures/3dmodels/make_boxes.py <3dmodels tree>), or the
    model resolves on one host and not another."""
    refs = set()
    for p in (Path(__file__).resolve().parent / "golden").glob("*/*.kicad_pcb"):
        refs.update(make_boxes.REF.findall(p.read_text(encoding="utf-8")))
    missing = sorted(n for n in refs
                     if not any((MODELS / f"{n}{s}").is_file() for s in (".wrl", ".step")))
    # the pinned library has no model for these: the goldens are judged without them
    assert missing == KNOWN_ABSENT




@pytest.mark.host_models
def test_a_host_models_test_sees_the_real_library():
    assert os.environ.get("KICAD10_3DMODEL_DIR") != str(MODELS)
