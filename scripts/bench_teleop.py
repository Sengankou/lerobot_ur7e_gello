#!/usr/bin/env python
"""Measure where the teleoperation loop actually spends its time.

    python scripts/bench_teleop.py --fps 30
    python scripts/bench_teleop.py --fps 125 --no-cameras

Runs the same shape of loop as ``lerobot-teleoperate`` (observe -> get action
-> send action -> pace) but times each stage separately, so a missed frame rate
can be attributed instead of guessed at. The three usual culprits are very
different problems:

* **get_observation** dominating  -> camera bound. A UVC webcam paces the loop
  at its own frame rate, and with ``exposure_dynamic_framerate`` on it silently
  halves that in a dim room.
* **send_action** dominating      -> RTDE/network bound. This is the number
  that matters for "can we drive the arm at 125 Hz".
* **sleep overshoot** dominating  -> the loop is pacing-bound, not work-bound.
  On Linux ``lerobot.utils.robot_utils.precise_sleep`` is a plain
  ``time.sleep``, whose granularity costs a couple of ms per tick.
"""

from __future__ import annotations

import argparse
import statistics
import time

from lerobot.utils.import_utils import register_third_party_plugins

register_third_party_plugins()

from lerobot.teleoperators import TeleoperatorConfig, make_teleoperator_from_config  # noqa: E402
from lerobot_robot_ur7e import UR7E, UR7EConfig  # noqa: E402


def pct(values: list[float], p: float) -> float:
    return sorted(values)[min(len(values) - 1, int(p * len(values)))]


def summarize(name: str, values_s: list[float]) -> str:
    ms = [v * 1e3 for v in values_s]
    return (
        f"  {name:<22} mean {statistics.mean(ms):7.3f} ms   "
        f"p50 {pct(ms, 0.50):7.3f}   p95 {pct(ms, 0.95):7.3f}   max {max(ms):7.3f}"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--teleop", default="gello", help="gello | keyboard_joint")
    ap.add_argument("--no-cameras", action="store_true", help="isolate the control path")
    args = ap.parse_args()

    robot_cfg = UR7EConfig(cameras={}) if args.no_cameras else UR7EConfig()
    robot = UR7E(robot_cfg)

    teleop_cfg = TeleoperatorConfig.get_choice_class(args.teleop)()
    teleop = make_teleoperator_from_config(teleop_cfg)

    print(f"\n== bench: teleop={args.teleop} fps={args.fps:g} cameras={list(robot_cfg.cameras)}")

    teleop.connect()
    robot.connect()

    t_obs: list[float] = []
    t_act: list[float] = []
    t_send: list[float] = []
    t_loop: list[float] = []
    t_sleep_err: list[float] = []

    period = 1.0 / args.fps
    n = int(args.seconds * args.fps)
    try:
        # One warm-up tick: first camera read and first servoJ are not
        # representative (buffer allocation, script handshake).
        robot.get_observation()
        teleop.get_action()

        for _ in range(n):
            loop_start = time.perf_counter()

            t0 = time.perf_counter()
            robot.get_observation()
            t1 = time.perf_counter()
            action = teleop.get_action()
            t2 = time.perf_counter()
            robot.send_action(action)
            t3 = time.perf_counter()

            t_obs.append(t1 - t0)
            t_act.append(t2 - t1)
            t_send.append(t3 - t2)

            remaining = period - (t3 - loop_start)
            if remaining > 0:
                s0 = time.perf_counter()
                time.sleep(remaining)
                t_sleep_err.append((time.perf_counter() - s0) - remaining)
            t_loop.append(time.perf_counter() - loop_start)
    finally:
        robot.disconnect()
        teleop.disconnect()

    achieved = 1.0 / statistics.mean(t_loop)
    work = statistics.mean(t_obs) + statistics.mean(t_act) + statistics.mean(t_send)

    print(f"\n  ticks {len(t_loop)}   target {args.fps:g} Hz   achieved {achieved:.1f} Hz")
    print(summarize("get_observation", t_obs))
    print(summarize("teleop.get_action", t_act))
    print(summarize("send_action", t_send))
    print(summarize("whole loop", t_loop))
    if t_sleep_err:
        print(summarize("sleep overshoot", t_sleep_err))
    print(f"\n  work per tick        : {work * 1e3:.3f} ms")
    print(f"  budget per tick      : {period * 1e3:.3f} ms")
    print(f"  headroom             : {(period - work) * 1e3:+.3f} ms")

    if work > period:
        slowest = max(
            [("get_observation", statistics.mean(t_obs)),
             ("teleop.get_action", statistics.mean(t_act)),
             ("send_action", statistics.mean(t_send))],
            key=lambda kv: kv[1],
        )
        print(f"  VERDICT: work-bound. Slowest stage is {slowest[0]}.")
    elif achieved < args.fps * 0.95:
        print("  VERDICT: pacing-bound -- the work fits, the sleep granularity does not.")
    else:
        print("  VERDICT: target rate met.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
