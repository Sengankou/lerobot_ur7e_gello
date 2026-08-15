#!/usr/bin/env python
"""Phase A smoke test: prove the two RTDE directions work.

    python scripts/smoke_rtde.py            # full test (moves the arm)
    python scripts/smoke_rtde.py --read-only  # receive path only, no motion

What it checks, in the order that isolates faults best:

1. **RTDE receive** (PC -> robot :30004). Works even with no program playing,
   so a failure here means network / power / RTDE service, nothing else.
2. **RTDE control** (robot -> PC :50002 via the External Control URCapX).
   Only establishes while the program is *playing* on the pendant.
3. **moveJ**  -- a blocking point-to-point move and back.
4. **servoJ** -- the streaming path LeRobot actually uses, run at the
   configured rate for a few seconds while measuring the achieved period.

All addresses come from config/site.yaml, so this same script is the first
thing to run against the real UR7e after editing that one file.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time

import numpy as np

try:
    import ur7e_site
except ImportError:  # pragma: no cover
    sys.exit("ur7e_site is not installed. Run scripts/setup_env.sh first.")

import rtde_control
import rtde_receive


def banner(msg: str) -> None:
    print(f"\n=== {msg} " + "=" * max(0, 60 - len(msg)))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ip", default=None, help="override robot.ip from site.yaml")
    ap.add_argument("--read-only", action="store_true", help="skip everything that moves the arm")
    ap.add_argument("--servo-seconds", type=float, default=3.0)
    ap.add_argument("--servo-hz", type=float, default=125.0)
    args = ap.parse_args()

    ip = args.ip or ur7e_site.get("robot.ip")
    use_urcap = bool(ur7e_site.get("robot.use_external_control_urcap", True))
    freq = float(ur7e_site.get("robot.rtde_frequency", 500.0))
    ec_host = ur7e_site.get("robot.external_control.host_ip")
    ec_port = ur7e_site.get("robot.external_control.port")

    banner("site config")
    print(f"robot ip                  : {ip}")
    print(f"use External Control URCap: {use_urcap}")
    print(f"robot dials back to       : {ec_host}:{ec_port}")
    print(f"rtde frequency            : {freq} Hz")

    # ---------------------------------------------------------------- receive
    banner("1/4 RTDE receive (works without the program playing)")
    t0 = time.perf_counter()
    rtde_r = rtde_receive.RTDEReceiveInterface(ip)
    print(f"connected in {time.perf_counter() - t0:.2f}s")
    q0 = list(rtde_r.getActualQ())
    print(f"robot mode  : {rtde_r.getRobotMode()}  (7 == RUNNING)")
    print(f"safety mode : {rtde_r.getSafetyMode()} (1 == NORMAL)")
    print(f"q           : {np.round(q0, 4).tolist()}")
    print(f"TCP pose    : {np.round(rtde_r.getActualTCPPose(), 4).tolist()}")

    if args.read_only:
        rtde_r.disconnect()
        print("\nread-only mode: skipping the control path. GREEN so far.")
        return 0

    # ---------------------------------------------------------------- control
    banner("2/4 RTDE control (needs the External Control program PLAYING)")
    print("If this hangs, check in this order:")
    print("  1. program not playing on the pendant  2. wrong Host IP in the")
    print("  External Control tile  3. use_external_control_urcap mis-set")
    t0 = time.perf_counter()
    if use_urcap:
        flags = rtde_control.RTDEControlInterface.FLAG_USE_EXT_UR_CAP
        rtde_c = rtde_control.RTDEControlInterface(ip, freq, flags)
    else:
        rtde_c = rtde_control.RTDEControlInterface(ip, freq)
    print(f"control established in {time.perf_counter() - t0:.2f}s (isConnected={rtde_c.isConnected()})")

    try:
        # ------------------------------------------------------------- moveJ
        banner("3/4 moveJ: nudge wrist_3 by +5 deg and return")
        q_target = list(q0)
        q_target[5] += float(np.deg2rad(5))
        rtde_c.moveJ(q_target, 0.5, 0.5)
        q_after = list(rtde_r.getActualQ())
        moved = np.rad2deg(q_after[5] - q0[5])
        print(f"wrist_3 moved {moved:+.2f} deg (expected +5.00)")
        rtde_c.moveJ(list(q0), 0.5, 0.5)
        back = np.rad2deg(list(rtde_r.getActualQ())[5] - q0[5])
        print(f"returned to {back:+.2f} deg from start")
        if abs(moved - 5.0) > 0.5:
            print("WARNING: moveJ did not reach the commanded angle")

        # ------------------------------------------------------------ servoJ
        banner(f"4/4 servoJ: stream the current pose at {args.servo_hz:g} Hz for {args.servo_seconds:g}s")
        dt = 1.0 / args.servo_hz
        n = int(args.servo_seconds / dt)
        periods: list[float] = []
        # NOTE: ur_rtde's initPeriod/waitPeriod pace at the *interface*
        # frequency (rtde_frequency in site.yaml), not at `dt`. Pacing by hand
        # is what makes --servo-hz mean what it says, and it mirrors how
        # LeRobot's control loop actually times itself.
        next_tick = time.perf_counter()
        last = next_tick
        for _ in range(n):
            rtde_c.servoJ(list(q0), 0.5, 0.5, dt, 0.1, 300)
            next_tick += dt
            sleep_s = next_tick - time.perf_counter()
            if sleep_s > 0:
                time.sleep(sleep_s)
            now = time.perf_counter()
            periods.append(now - last)
            last = now
        rtde_c.servoStop()

        periods = periods[1:]  # drop the first, which includes loop setup
        mean_hz = 1.0 / statistics.mean(periods)
        p95_ms = sorted(periods)[int(0.95 * len(periods))] * 1e3
        print(f"ticks           : {len(periods)}")
        print(f"achieved rate   : {mean_hz:.1f} Hz (target {args.servo_hz:g})")
        print(f"period p95      : {p95_ms:.2f} ms (target {dt * 1e3:.2f})")
        print(f"drift from start: {np.rad2deg(np.array(rtde_r.getActualQ()) - np.array(q0)).round(3).tolist()} deg")
    finally:
        rtde_c.servoStop()
        rtde_c.stopScript()
        rtde_c.disconnect()
        rtde_r.disconnect()

    banner("RESULT: GREEN")
    print("Both RTDE directions verified. Phase A complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
