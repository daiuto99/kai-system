#!/usr/bin/env python3
"""signal_bus tests — self-contained (no pytest dependency).

Run with `python3 shared/test_signal_bus.py`. Uses a temp bus path via env so no
vault mount is required. Covers the reader/writer contract:

  1. absent      — read before any write -> idle default, not a crash
  2. roundtrip   — set_state -> read_state returns it, seq increments monotonically
  3. invalid_in  — set_state('bogus') coerces to 'error', never raises
  4. invalid_file— corrupt JSON on disk -> read_state degrades to idle
  5. staleness   — a non-idle state older than STALE_AFTER_S reads back as stale idle
  6. atomic      — no leftover .tmp files after a write
"""
import importlib
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

_dir = tempfile.mkdtemp()
os.environ["KAI_SIGNAL_BUS_PATH"] = os.path.join(_dir, "signal_bus.json")

sys.path.insert(0, os.path.dirname(__file__))
import signal_bus as sb  # noqa: E402

importlib.reload(sb)  # ensure BUS_PATH picks up the env we just set

_FAILS = []


def _check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        _FAILS.append(name)


# 1. absent
_check("absent_reads_idle", sb.read_state()["state"] == "idle")

# 2. roundtrip + monotonic seq
r1 = sb.set_state("listening", "heard you", "stt")
s1 = sb.read_state()
_check("roundtrip_state", s1["state"] == "listening" and s1["detail"] == "heard you")
r2 = sb.set_state("thinking", None, "council")
_check("seq_monotonic", r2["seq"] == r1["seq"] + 1)
_check("read_reflects_latest", sb.read_state()["state"] == "thinking")

# 3. invalid input coerced, no raise
try:
    rr = sb.set_state("bogus")
    _check("invalid_state_coerced", rr["state"] == "error")
except Exception:
    _check("invalid_state_coerced", False)

# 4. corrupt file degrades to idle
with open(os.environ["KAI_SIGNAL_BUS_PATH"], "w") as f:
    f.write("{not json")
_check("corrupt_file_idle", sb.read_state()["state"] == "idle")

# 5. staleness: hand-write an old non-idle state
old = (datetime.now(timezone.utc) - timedelta(seconds=sb.STALE_AFTER_S + 10)).isoformat(timespec="seconds")
with open(os.environ["KAI_SIGNAL_BUS_PATH"], "w") as f:
    json.dump({"state": "speaking", "seq": 9, "updated_at": old}, f)
st = sb.read_state()
_check("stale_reads_idle", st["state"] == "idle" and st["stale"] is True)

# 6. no leftover tmp files
sb.set_state("idle", None, "test")
leftovers = [p for p in os.listdir(_dir) if p.startswith(".signal_bus.")]
_check("no_tmp_leftovers", leftovers == [])

print(f"\n{'ALL PASS' if not _FAILS else 'FAILURES: ' + ', '.join(_FAILS)}")
sys.exit(1 if _FAILS else 0)
