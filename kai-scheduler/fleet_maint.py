#!/usr/bin/env python3
"""KAI fleet maintenance-window control (FLEET-EPIC support).

Node-scoped, time-boxed mute for the fleet watchdog page. While a window is
active the watchdog suppresses the fleet page AND the reboot-surface notes for
the named hosts ONLY -- the spine and every non-muted host still page, and a
lost-visibility/structural failure ALWAYS pages (see
fleet_eval.maint_suppresses_page). The window AUTO-RESTORES at expiry: there is
nothing to remember to turn back on.

  fleet_maint.py on --nodes 71-kai-mini,mac-mini --hours 96 --reason "cutover"
  fleet_maint.py status
  fleet_maint.py off

Writes /vault/_fleet_maint.json (host path: ~/vault/_fleet_maint.json -- the
same bind-mounted file the scheduler container reads).
"""
from __future__ import annotations
import argparse, json, sys, time
from pathlib import Path

MAINT = Path("/vault/_fleet_maint.json")
if not MAINT.parent.exists():                 # running on the host, not the container
    MAINT = Path.home() / "vault" / "_fleet_maint.json"
SCHEMA = "kai.fleet_maint.v1"


def _load() -> dict:
    try:
        return json.loads(MAINT.read_text())
    except Exception:
        return {}


def _fmt_left(exp: float) -> str:
    left = int(exp - time.time())
    if left <= 0:
        return "EXPIRED (auto-restored)"
    h, m = divmod(left // 60, 60)
    return f"{h}h{m:02d}m left"


def cmd_on(a):
    nodes = [n.strip() for n in a.nodes.split(",") if n.strip()]
    if not nodes:
        sys.exit("refusing to arm an empty window -- pass --nodes a,b")
    now = time.time()
    doc = {
        "schema": SCHEMA,
        "muted": nodes,
        "reason": a.reason,
        "set_by": a.set_by,
        "set_at": now,
        "expires_at": now + a.hours * 3600,
    }
    MAINT.write_text(json.dumps(doc, indent=2))
    print(f"[OK] maintenance window ARMED -- muting {nodes} for {a.hours:g}h "
          f"({_fmt_left(doc['expires_at'])}).")
    print("     Spine + non-muted hosts still page; lost visibility still pages.")
    print(f"     reason: {a.reason}")
    print(f"     auto-restores at epoch {int(doc['expires_at'])}; end early: fleet_maint.py off")


def cmd_off(a):
    if MAINT.exists():
        MAINT.unlink()
        print("[OK] maintenance window CLEARED -- full fleet paging restored now.")
    else:
        print("no maintenance window set -- full fleet paging already active.")


def cmd_status(a):
    m = _load()
    if not m or m.get("schema") != SCHEMA:
        print("maintenance window: none -- full fleet paging active.")
        return
    exp = m.get("expires_at", 0)
    active = time.time() < exp
    print(f"maintenance window: {'ACTIVE' if active else 'expired (auto-restored)'}")
    print(f"  muted:   {m.get('muted')}")
    print(f"  reason:  {m.get('reason')}")
    print(f"  set_by:  {m.get('set_by')}  set_at epoch {int(m.get('set_at', 0))}")
    print(f"  expires: epoch {int(exp)}  ({_fmt_left(exp)})")


def main():
    p = argparse.ArgumentParser(description="KAI fleet maintenance-window control")
    sub = p.add_subparsers(dest="cmd", required=True)
    on = sub.add_parser("on", help="arm a mute window")
    on.add_argument("--nodes", required=True,
                    help="comma-separated host names, e.g. 71-kai-mini,mac-mini")
    on.add_argument("--hours", type=float, default=96.0)
    on.add_argument("--reason", default="fleet maintenance")
    on.add_argument("--set-by", dest="set_by", default="leo")
    on.set_defaults(fn=cmd_on)
    off = sub.add_parser("off", help="clear the window now")
    off.set_defaults(fn=cmd_off)
    st = sub.add_parser("status", help="show the window")
    st.set_defaults(fn=cmd_status)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
