"""Configuration dataclass for the UR5e/UR7e robot plugin.

UR7e is a mechanical rebrand of the UR5e, so one plugin covers both; the
``ur5e`` registration name is kept for continuity with the upstream project and
with datasets already recorded against it.

Every default is read from ``config/site.yaml`` through :mod:`ur7e_site`, so the
sim -> real switch is a single-file edit and no IP or device path is baked into
the code. CLI flags (``--robot.ip=...``) still win over the site file.
"""

from dataclasses import dataclass, field
from typing import Any

from lerobot.cameras import CameraConfig
from lerobot.robots import RobotConfig

from ur7e_site import get as site_get

# Importing the concrete camera config modules is what registers the "opencv" /
# "intelrealsense" / "zmq" choices with draccus, so cameras can be described by
# type name in site.yaml. Kept lazy-tolerant: a missing optional backend (e.g.
# pyrealsense2 on a machine without RealSense) must not break the plugin.
from lerobot.cameras.opencv import OpenCVCameraConfig  # noqa: F401

try:  # pragma: no cover - depends on optional extras
    from lerobot.cameras.realsense import RealSenseCameraConfig  # noqa: F401
except Exception:  # pragma: no cover
    pass

try:  # pragma: no cover - built into lerobot >= 0.6
    from lerobot.cameras.zmq import ZMQCameraConfig  # noqa: F401
except Exception:  # pragma: no cover
    pass


def _cameras_from_site() -> dict[str, CameraConfig]:
    """Build camera configs from the ``robot.cameras`` block of site.yaml.

    Uses draccus' own subclass registry so any camera type LeRobot knows about
    can be named in the YAML without this module needing to know it.

    Failures degrade to "no cameras" rather than raising: this runs inside a
    dataclass ``default_factory`` at import time, and an exception here would
    take down every ``lerobot-*`` command, including ``--help``.
    """
    import logging

    spec: dict[str, Any] = site_get("robot.cameras", {}) or {}
    cameras: dict[str, CameraConfig] = {}
    for name, params in spec.items():
        if not isinstance(params, dict):
            continue
        params = dict(params)
        cam_type = params.pop("type", "opencv")
        try:
            cls = CameraConfig.get_choice_class(cam_type)
            cameras[name] = cls(**params)
        except Exception:
            logging.warning(
                "site.yaml: could not build camera %r of type %r; skipping it.",
                name,
                cam_type,
                exc_info=True,
            )
    return cameras


@dataclass
class ExternalControlConfig:
    """Where the robot dials back to when the External Control URCapX runs.

    This is the counter-intuitive half of the wiring: RTDE is PC -> robot, but
    External Control is robot -> PC. ``host_ip`` is therefore *this machine's*
    address as seen from the controller, never a loopback address.
    """

    host_ip: str = field(default_factory=lambda: site_get("robot.external_control.host_ip", "192.168.56.1"))
    port: int = field(default_factory=lambda: int(site_get("robot.external_control.port", 50002)))


@dataclass
class ServoJConfig:
    """Streaming-servo parameters passed to ``RTDEControlInterface.servoJ``."""

    acceleration: float = field(default_factory=lambda: float(site_get("robot.servoj.acceleration", 0.5)))
    speed: float = field(default_factory=lambda: float(site_get("robot.servoj.speed", 0.5)))
    #: Duration of one servoJ command. Keep close to the control-loop period.
    time_s: float = field(default_factory=lambda: float(site_get("robot.servoj.time_s", 0.008)))
    lookahead_time_s: float = field(
        default_factory=lambda: float(site_get("robot.servoj.lookahead_time_s", 0.2))
    )
    gain: int = field(default_factory=lambda: int(site_get("robot.servoj.gain", 300)))


@RobotConfig.register_subclass("ur5e")
@dataclass
class UR5EConfig(RobotConfig):
    #: Controller address. URSim on the ursim_net bridge by default.
    ip: str = field(default_factory=lambda: site_get("robot.ip", "192.168.56.101"))

    #: Robotiq gripper present and reachable. False on URSim (no tool I/O) and
    #: on PolyScope X until the ToolComm Forwarder path is implemented. The
    #: action/observation vectors keep their 7th element either way.
    use_gripper: bool = field(default_factory=lambda: bool(site_get("robot.use_gripper", False)))

    #: PolyScope X refuses ur_rtde's default "upload script" route; the
    #: External Control URCapX must be used instead (and the program must be
    #: playing on the pendant for control to establish).
    use_external_control_urcap: bool = field(
        default_factory=lambda: bool(site_get("robot.use_external_control_urcap", True))
    )

    rtde_frequency: float = field(default_factory=lambda: float(site_get("robot.rtde_frequency", 500.0)))

    external_control: ExternalControlConfig = field(default_factory=ExternalControlConfig)
    servoj: ServoJConfig = field(default_factory=ServoJConfig)

    cameras: dict[str, CameraConfig] = field(default_factory=_cameras_from_site)

    #: Seconds to wait for RTDEControlInterface to establish before giving up.
    connect_timeout_s: float = 10.0

    def __post_init__(self) -> None:
        # `id` ends up in the calibration filename and in dataset metadata.
        # Leaving it None yields a literal "None.json", which is confusing.
        if self.id is None:
            self.id = str(site_get("profile", "ur7e"))
