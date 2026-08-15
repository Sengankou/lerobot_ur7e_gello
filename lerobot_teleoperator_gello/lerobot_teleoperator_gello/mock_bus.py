"""A simulated Dynamixel bus, so the GELLO code path runs without the hardware.

Why this exists
---------------
The physical GELLO leader arm lives on another machine, but we still want the
*real* teleoperator code -- the calibration maths, the 7-wide action dict, the
async read thread, the EMA smoothing -- executed end to end against URSim. So
instead of writing a second "fake teleoperator", we swap only the lowest layer:
this class is a drop-in for ``DynamixelMotorsBus`` as far as :mod:`.gello` uses
it, and everything above it is the code that will run on the real leader.

The synthetic motor counts are anchored on the internally measured GELLO home
offsets (``[4, 5, 3, 2, 2, 3] x 1024`` counts, i.e. ``k x pi/2`` rad at
``RAD_PER_COUNT = 2*pi/4096``). Feeding those through
``Gello._process_action`` therefore yields exactly ``calibration_position``
at rest, which is a valid UR arm pose -- so URSim receives sane targets.
"""

from __future__ import annotations

import logging
import math
import time

logger = logging.getLogger(__name__)

#: Home position in raw motor counts, per joint, from the 2026-07-23 bench
#: calibration of the physical GELLO (offsets of k*pi/2 rad -> k*1024 counts).
HOME_COUNTS: dict[str, int] = {
    "joint_0": 4 * 1024,
    "joint_1": 5 * 1024,
    "joint_2": 3 * 1024,
    "joint_3": 2 * 1024,
    "joint_4": 2 * 1024,
    "joint_5": 3 * 1024,
}
#: Gripper home (fully open) in counts; closing moves it *down* by
#: ``gripper_travel_counts``, matching the real device's wiring.
HOME_GRIPPER_COUNTS: int = 2048

#: Wall time one ``sync_read`` of the 7 servos costs on real hardware
#: (U2D2 at 57600 baud). The mock spends the same time on purpose -- see the
#: note in :meth:`MockDynamixelBus.sync_read`.
DEFAULT_READ_LATENCY_S: float = 0.003


class MockDynamixelBus:
    """Minimal stand-in for ``lerobot.motors.dynamixel.DynamixelMotorsBus``.

    Only the surface :mod:`.gello` actually touches is implemented. Anything
    else raising ``AttributeError`` is intentional: it tells us the real code
    path drifted away from what the mock covers.
    """

    def __init__(
        self,
        port: str,
        motors: dict,
        *,
        amplitude_rad: float = 0.2,
        period_s: float = 8.0,
        rad_per_count: float = 2 * math.pi / (4096 - 1),
        joint_signs: list[int] | None = None,
        read_latency_s: float = DEFAULT_READ_LATENCY_S,
    ):
        self.port = port
        self.motors = motors
        self._connected = False
        self._baudrate: int | None = None
        self._t0 = time.perf_counter()

        self.amplitude_rad = amplitude_rad
        self.period_s = period_s
        self.read_latency_s = read_latency_s
        self._rad_per_count = rad_per_count
        self._joint_signs = joint_signs or [1] * 6

        #: Registers written by ``configure()``. Kept so tests can assert the
        #: real configuration sequence ran.
        self.written: dict[tuple[str, str], int] = {}

    # ------------------------------------------------------------- lifecycle

    @property
    def is_connected(self) -> bool:
        return self._connected

    def connect(self, handshake: bool = True) -> None:
        self._connected = True
        self._t0 = time.perf_counter()
        logger.info("MockDynamixelBus connected (simulated port %s).", self.port)

    def disconnect(self) -> None:
        self._connected = False

    def set_baudrate(self, baudrate: int) -> None:
        self._baudrate = baudrate

    def _handshake(self) -> None:
        pass

    def _assert_motors_exist(self) -> None:
        pass

    # ------------------------------------------------------------ configure

    def disable_torque(self, *args, **kwargs) -> None:
        pass

    def enable_torque(self, *args, **kwargs) -> None:
        pass

    def configure_motors(self, *args, **kwargs) -> None:
        pass

    def write(self, register: str, motor: str, value, *args, **kwargs) -> None:
        self.written[(register, motor)] = value

    # ------------------------------------------------------------------ read

    def _phase(self) -> float:
        return 2 * math.pi * ((time.perf_counter() - self._t0) / self.period_s)

    def sync_read(self, register: str, *args, normalize: bool = True, **kwargs) -> dict[str, int]:
        if register != "Present_Position":
            raise NotImplementedError(f"MockDynamixelBus only simulates Present_Position, got {register!r}")

        # Deliberate sleep. Gello runs this in a background thread with no
        # pacing of its own (`_read_loop` is a bare while-loop) because on real
        # hardware the serial round-trip paces it. Returning instantly would
        # turn that thread into a GIL-hogging spin loop and add milliseconds of
        # latency to the *robot* control path -- measured at ~5 ms/tick, enough
        # to drag a 125 Hz teleop loop down to 86 Hz.
        if self.read_latency_s > 0:
            time.sleep(self.read_latency_s)

        phase = self._phase()
        out: dict[str, int] = {}
        for idx, (name, home) in enumerate(HOME_COUNTS.items()):
            # Quarter-period offset per joint so the arm traces a smooth,
            # non-degenerate path instead of every joint moving in lockstep.
            offset_rad = self.amplitude_rad * math.sin(phase + idx * math.pi / 4)
            sign = self._joint_signs[idx] if idx < len(self._joint_signs) else 1
            out[name] = int(round(home + sign * offset_rad / self._rad_per_count))

        # Gripper: 0..1 raised-cosine sweep over the calibrated travel.
        grip_frac = 0.5 * (1.0 - math.cos(phase))
        out["gripper"] = int(round(HOME_GRIPPER_COUNTS - grip_frac * 575))
        return out
