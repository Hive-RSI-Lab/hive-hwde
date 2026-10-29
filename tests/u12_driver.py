"""Subprocess driver for tests/test_u12_safety.py (real OS locks, real crashes).

  create <pcb> <fab> <quote.json> <api_quote.json> <hold_s> <confirm>
      order_submit --api-create with a fake session whose create_order sleeps
      hold_s seconds (the window a second creator must serialize behind).
  restore-crash <workspace> <label>
      State.restore with os._exit(137) injected after the FIRST file is
      staged (before verify + swap): a crash mid-restore.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_jlcapi as tj  # noqa: E402  (adds scripts + lib to sys.path)

import order_submit  # noqa: E402
import safelib  # noqa: E402
import state as state_mod  # noqa: E402


def main(argv):
    mode = argv[0]
    if mode == "create":
        pcb, fab, qj, aq, hold, confirm = argv[1:7]
        for k in tj.API_ENV:
            os.environ[k] = "X"

        class SlowSession(tj.FakeSession):
            def create_order(self, payload):
                time.sleep(float(hold))
                return super().create_order(payload)

        fake = SlowSession(create_order=tj.ok_resp(tj.CREATE_OK))
        order_submit._make_session = lambda: fake
        return order_submit.main(tj.submit_argv(
            Path(pcb), Path(fab), Path(qj), "--api-create",
            "--api-quote-file", aq, "--confirm", confirm))
    if mode == "restore-crash":
        ws, label = argv[1:3]

        def boom(point, **ctx):
            if point == "restore.staged" and ctx.get("n") == 1:
                os._exit(137)   # no finally, no cleanup: a real crash
        safelib.FAULT_HOOK = boom
        st = state_mod.State.load(Path(ws) / "state.json")
        st.restore(label)
        return 0
    raise SystemExit(f"unknown mode {mode}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
