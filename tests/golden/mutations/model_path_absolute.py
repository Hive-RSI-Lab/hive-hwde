"""Mutant: model-path-absolute (blinky2).

LIBRARY fault (the hit: eleven boards in the boards repo). D1's 3D model is
named by absolute path into the track worktree that pulled its library,
instead of through ${KICAD10_3DMODEL_DIR}. The path resolved while that
worktree lived; once it was removed the model was gone, STEP export dropped
the part and a doc's render showed it wrong. Copper is untouched. Must be
caught by check_model_paths (kind "model_path_absolute", ref D1, at D1's
origin).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib

OLD = '(model "${KICAD10_3DMODEL_DIR}/LED_SMD.3dshapes/LED_0805_2012Metric.step"'
NEW = ('(model "/work/worktrees/boards/blinky2/blinky2/lib/aiee.3dshapes/'
       'LED_0805_2012Metric.step"')


def surgery(text):
    s, e = mutlib.footprint_block(text, "D1")
    block = mutlib.replace_once(text[s:e], OLD, NEW, "D1 model path")
    return text[:s] + block + text[e:], {"ref": "D1", "model": NEW[8:-1]}


if __name__ == "__main__":
    sys.exit(mutlib.run("model-path-absolute", "blinky2", surgery))
