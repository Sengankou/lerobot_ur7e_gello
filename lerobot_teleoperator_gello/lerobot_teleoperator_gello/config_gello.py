"""Configuration dataclass for the GELLO teleoperator plugin.

Serial port, baudrate and the mock switch come from ``config/site.yaml`` via
:mod:`ur7e_site`, so moving the U2D2 between machines -- or running without the
hardware at all -- is a config edit rather than a code edit.
"""

from dataclasses import dataclass, field
from pathlib import Path

from lerobot.teleoperators.config import TeleoperatorConfig

from ur7e_site import get as site_get
from ur7e_site import resolve_path


@TeleoperatorConfig.register_subclass("gello")
@dataclass
class GelloConfig(TeleoperatorConfig):
    # Port of the U2D2 adapter. The /dev/serial/by-id/... form is tied to the
    # FTDI chip, so it stays valid when the adapter is moved to another host.
    port: str = field(default_factory=lambda: site_get("teleop.gello.port", "/dev/ttyUSB0"))
    baudrate: int = field(default_factory=lambda: int(site_get("teleop.gello.baudrate", 57_600)))

    #: Run against a simulated Dynamixel bus instead of real servos. Everything
    #: above the bus (calibration maths, action assembly, async read thread) is
    #: the same code that runs on hardware -- see :mod:`.mock_bus`.
    mock: bool = field(default_factory=lambda: bool(site_get("teleop.gello.mock", False)))
    mock_amplitude_rad: float = field(
        default_factory=lambda: float(site_get("teleop.gello.mock_motion.amplitude_rad", 0.2))
    )
    mock_period_s: float = field(
        default_factory=lambda: float(site_get("teleop.gello.mock_motion.period_s", 8.0))
    )

    #: Arm pose (rad) that the leader is holding when calibration is captured.
    #: Raw motor counts are re-referenced to this, so it must match the physical
    #: home jig.
    calibration_position: list[float] = field(
        default_factory=lambda: [0, -1.57, 1.57, -1.57, -1.57, -1.57]
    )
    #: Per-joint direction. Determined by how each servo is mounted; unchanged
    #: across hosts, so it is safe to carry over from the bench calibration.
    joint_signs: list[int] = field(default_factory=lambda: [1, 1, -1, 1, 1, 1])
    gripper_travel_counts: int = 575

    # Smoothing factor for Exponential Moving Average (EMA).
    # Range [0, 1]. 1 means no smoothing (instant update), 0 means no update (freeze).
    # Lower values smooth out jitter but add latency.
    smoothing: float = 0.85
    # Whether to run device reading in a background thread.
    # This helps when USB communication is slow (e.g. long cables).
    use_async: bool = True

    #: Keep calibration inside the repo (committed) rather than in the user's
    #: HF cache, so a fresh clone on the Spark starts with known-good numbers.
    calibration_dir: Path | None = field(default_factory=lambda: resolve_path("calibration"))

    def __post_init__(self) -> None:
        # `id` becomes the calibration filename; None would give "None.json".
        # Mock and real calibrations must not overwrite each other, since the
        # mock's offsets are synthetic.
        if self.id is None:
            self.id = "gello_mock" if self.mock else "gello"
