#!/usr/bin/env python
"""Report (and optionally watch) the controller's runtime state over RTDE receive.

    python scripts/ursim_state.py            # one-shot
    python scripts/ursim_state.py --watch 10 # poll for 10 seconds

Receive-only, so it never takes control and works whether or not a program is
playing. Handy for answering "is the External Control program actually running
right now?" without opening the pendant UI.
"""

from __future__ import annotations

import argparse
import time

import rtde_receive

import ur7e_site

ROBOT_MODE = {
    -1: "NO_CONTROLLER", 0: "DISCONNECTED", 1: "CONFIRM_SAFETY", 2: "BOOTING",
    3: "POWER_OFF", 4: "POWER_ON", 5: "IDLE", 6: "BACKDRIVE", 7: "RUNNING",
}
SAFETY_MODE = {
    1: "NORMAL", 2: "REDUCED", 3: "PROTECTIVE_STOP", 4: "RECOVERY",
    5: "SAFEGUARD_STOP", 6: "SYSTEM_EMERGENCY_STOP", 7: "ROBOT_EMERGENCY_STOP",
    8: "VIOLATION", 9: "FAULT",
}
RUNTIME_STATE = {0: "STOPPING", 1: "STOPPED", 2: "PLAYING", 3: "PAUSING", 4: "PAUSED", 5: "RESUMING"}


def describe(rec: rtde_receive.RTDEReceiveInterface) -> str:
    rm, sm, rs = rec.getRobotMode(), rec.getSafetyMode(), rec.getRuntimeState()
    q = [round(v, 3) for v in rec.getActualQ()]
    return (
        f"robot={rm}:{ROBOT_MODE.get(rm, '?'):<9} "
        f"safety={sm}:{SAFETY_MODE.get(sm, '?'):<9} "
        f"program={rs}:{RUNTIME_STATE.get(rs, '?'):<8} q={q}"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ip", default=None)
    ap.add_argument("--watch", type=float, default=0.0, help="seconds to poll (0 = one shot)")
    ap.add_argument("--interval", type=float, default=1.0)
    args = ap.parse_args()

    ip = args.ip or ur7e_site.get("robot.ip")
    rec = rtde_receive.RTDEReceiveInterface(ip)
    try:
        if args.watch <= 0:
            print(describe(rec))
        else:
            end = time.perf_counter() + args.watch
            while time.perf_counter() < end:
                print(f"[{time.strftime('%H:%M:%S')}] {describe(rec)}", flush=True)
                time.sleep(args.interval)
    finally:
        rec.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
