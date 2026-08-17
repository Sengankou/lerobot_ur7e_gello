#!/usr/bin/env python
"""Hold a listener open on :50002 while you fix the pendant, and say when it lands.

    python scripts/ursim_reconnect.py

Why this exists
---------------
External Control URCapX 1.1.0 caches the URScript when you press "Update
program", not when you press play, and that cache goes stale -- editing the
program invalidates it, and it also expires if the controller sits idle
(observed after ~43 h). Once stale, both program nodes turn yellow and play is
refused with "Program is not finished. Complete the yellow program-nodes".

Recovering means pressing "Update program" *while a ur_rtde client is already
listening*. Doing that from a normal command is a race: ur_rtde's constructor
gives up after a hard-coded 60 s, which is not much time to find the node in
the UI. This script just retries that constructor until it succeeds, so there
is no clock to beat.

It does not move the arm. It connects, confirms, and lets go.
"""

from __future__ import annotations

import argparse
import time

import rtde_control
import rtde_receive

import ur7e_site

RUNTIME_STATE = {0: "STOPPING", 1: "STOPPED", 2: "PLAYING", 3: "PAUSING", 4: "PAUSED", 5: "RESUMING"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ip", default=None)
    ap.add_argument("--attempts", type=int, default=10, help="60 s each")
    args = ap.parse_args()

    ip = args.ip or ur7e_site.get("robot.ip")
    host = ur7e_site.get("robot.external_control.host_ip")
    port = ur7e_site.get("robot.external_control.port")
    freq = float(ur7e_site.get("robot.rtde_frequency", 500.0))

    rec = rtde_receive.RTDEReceiveInterface(ip)
    state = rec.getRuntimeState()
    print(f"robot {ip} · program state {state}:{RUNTIME_STATE.get(state, '?')}")
    rec.disconnect()

    print(
        f"\nListening on {host}:{port}. Now, in the PolyScope X UI at http://{ip} :\n"
        f"  1. Program tab -> click the 'External Control Program' node to open it\n"
        f"  2. press 'Update program'   (both nodes stop being yellow)\n"
        f"  3. press play\n"
        f"\nRetrying until that happens -- take as long as you need. Ctrl-C to give up.\n"
    )

    flags = rtde_control.RTDEControlInterface.FLAG_USE_EXT_UR_CAP
    for attempt in range(1, args.attempts + 1):
        t0 = time.perf_counter()
        try:
            ctrl = rtde_control.RTDEControlInterface(ip, freq, flags)
        except Exception:
            print(f"  attempt {attempt}/{args.attempts}: still waiting ({time.perf_counter() - t0:.0f}s)")
            continue

        print(f"\nCONNECTED after attempt {attempt}. Control is established.")
        # Release cleanly WITHOUT stopScript(): stopScript would stop the
        # program we just got running, and with Loop Program enabled it would
        # restart into the same wait. Just drop the socket and leave the
        # program playing, ready for the next lerobot-* command.
        ctrl.disconnect()

        rec = rtde_receive.RTDEReceiveInterface(ip)
        state = rec.getRuntimeState()
        rec.disconnect()
        print(f"program state is now {state}:{RUNTIME_STATE.get(state, '?')}")
        print("\nReady. Run your lerobot-record / lerobot-rollout command now.")
        return 0

    print("\nGave up. Check that the External Control Application tile still says")
    print(f"{host}:{port}, and see docs/HANDOFF.md §12.4-A.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
