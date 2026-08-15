"""UR5e/UR7e robot interface using RTDE.

Implements the LeRobot ``Robot`` interface for Universal Robots e-Series arms.
Joint targets are streamed with ``servoJ`` for smooth real-time control.

PolyScope X notes (the controller generation on UR7e)
----------------------------------------------------
* ``ur_rtde`` cannot upload a control script the classic way. The **External
  Control URCapX** must be installed on the controller and ``ur_rtde`` told to
  use it via ``FLAG_USE_EXT_UR_CAP``. Control only establishes while the
  program containing the External Control node is *playing* on the pendant.
* The Dashboard server (:29999) is gone, so nothing here depends on it.
* The legacy Robotiq socket (:63352) is closed, so the gripper is opt-in and
  defaults to off. The 7th action/observation element is kept regardless, which
  keeps dataset shapes identical between the simulator and the real cell.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import rtde_control
import rtde_receive
from lerobot.cameras import make_cameras_from_configs
from lerobot.robots import Robot
from lerobot.utils.errors import DeviceNotConnectedError

from .config_ur5e import UR5EConfig
from .robotiq_gripper import RobotiqGripper

logger = logging.getLogger(__name__)

#: Motor names, in the order UR reports them (base -> wrist_3).
JOINT_NAMES = [f"joint_{i}" for i in range(6)]

#: Feature keys exposed to LeRobot. Since 0.6 the framework's rollout path
#: selects motor features by the ``.pos`` suffix
#: (``lerobot/rollout/context.py``: ``k.endswith(".pos")``), so a robot that
#: names them anything else still records and teleoperates fine but produces an
#: empty ``observation.state`` at rollout time. Follow the convention.
JOINT_KEYS = [f"{name}.pos" for name in JOINT_NAMES]
GRIPPER_KEY = "gripper.pos"


class UR5E(Robot):
    config_class = UR5EConfig
    name = "ur5e"

    def __init__(self, config: UR5EConfig):
        super().__init__(config)
        self.config = config

        self.cameras = make_cameras_from_configs(config.cameras)

        self.robot_ip = config.ip
        self.rtde_ctrl: rtde_control.RTDEControlInterface | None = None
        self.rtde_rec: rtde_receive.RTDEReceiveInterface | None = None

        self.with_gripper = config.use_gripper
        self.gripper: RobotiqGripper | None = RobotiqGripper() if self.with_gripper else None
        self.gripper_speed = 255
        self.gripper_force = 10

        #: Last commanded joint vector, used for the optional per-step clamp.
        self._last_goal: list[float] | None = None

    # ------------------------------------------------------------------ specs

    @property
    def _motors_ft(self) -> dict[str, type]:
        # The gripper entry is present even when the gripper is disabled, so a
        # dataset recorded on URSim can be replayed/trained against on the real
        # cell without a shape mismatch.
        return {**{k: float for k in JOINT_KEYS}, GRIPPER_KEY: float}

    @property
    def _cameras_ft(self) -> dict[str, tuple]:
        return {cam: (self.cameras[cam].height, self.cameras[cam].width, 3) for cam in self.cameras}

    @property
    def observation_features(self) -> dict:
        return {**self._motors_ft, **self._cameras_ft}

    @property
    def action_features(self) -> dict:
        return self._motors_ft

    @property
    def is_connected(self) -> bool:
        return (
            self.rtde_ctrl is not None
            and self.rtde_rec is not None
            and self.rtde_ctrl.isConnected()
            and self.rtde_rec.isConnected()
            and all(cam.is_connected for cam in self.cameras.values())
        )

    # ------------------------------------------------------------- lifecycle

    def _make_control_interface(self) -> rtde_control.RTDEControlInterface:
        """Build the control interface, honouring the PolyScope X requirement."""
        if not self.config.use_external_control_urcap:
            # Legacy PolyScope 5 path: ur_rtde uploads its own control script.
            return rtde_control.RTDEControlInterface(self.robot_ip, self.config.rtde_frequency)

        flags = rtde_control.RTDEControlInterface.FLAG_USE_EXT_UR_CAP
        return rtde_control.RTDEControlInterface(self.robot_ip, self.config.rtde_frequency, flags)

    def connect(self, calibrate: bool = True) -> None:
        if self.is_connected:
            return

        # Receive first: it succeeds even when no program is playing, so a
        # failure here isolates "network/robot down" from "program not running".
        try:
            self.rtde_rec = rtde_receive.RTDEReceiveInterface(self.robot_ip)
        except Exception as e:
            raise DeviceNotConnectedError(
                f"RTDE receive to {self.robot_ip} failed ({e}). Check that the controller is powered "
                f"and reachable (`ping {self.robot_ip}`), and that RTDE is enabled in Settings > Security."
            ) from e

        try:
            self.rtde_ctrl = self._make_control_interface()
        except Exception as e:
            self.rtde_rec.disconnect()
            self.rtde_rec = None
            ec = self.config.external_control
            hint = (
                f"RTDE control to {self.robot_ip} failed ({e}). With PolyScope X this almost always means "
                f"one of, in order of likelihood:\n"
                f"  1. the program with the External Control node is NOT playing (press the play button)\n"
                f"  2. the External Control Application tile has the wrong Host IP -- it must be "
                f"{ec.host_ip}:{ec.port}, i.e. THIS machine as the robot sees it (never 127.0.0.1)\n"
                f"  3. use_external_control_urcap is false while the controller is PolyScope X"
                if self.config.use_external_control_urcap
                else f"RTDE control to {self.robot_ip} failed ({e}). On PolyScope X you must set "
                f"use_external_control_urcap: true in config/site.yaml."
            )
            raise DeviceNotConnectedError(hint) from e

        if self.with_gripper:
            assert self.gripper is not None
            # NOTE: :63352 is the PolyScope 5 route. On PolyScope X this port is
            # closed and this call will hang -- see docs/MIGRATION.md.
            self.gripper.connect(self.robot_ip, 63352)
            self.gripper.activate(auto_calibrate=True)

        for cam in self.cameras.values():
            cam.connect()

        self.configure()
        logger.info("%s connected (%d camera(s), gripper=%s).", self, len(self.cameras), self.with_gripper)

    def configure(self) -> None:
        pass

    def disconnect(self) -> None:
        # Stopping the servo stream and the remote-control script before
        # dropping the socket leaves the controller idle instead of stuck
        # waiting for the next servoJ command.
        if self.rtde_ctrl is not None:
            try:
                if self.rtde_ctrl.isConnected():
                    self.rtde_ctrl.servoStop()
                    self.rtde_ctrl.stopScript()
            except Exception:
                logger.warning("Could not cleanly stop the control script.", exc_info=True)
            self.rtde_ctrl.disconnect()
            self.rtde_ctrl = None

        if self.rtde_rec is not None:
            self.rtde_rec.disconnect()
            self.rtde_rec = None

        for cam in self.cameras.values():
            cam.disconnect()

        self._last_goal = None

    @property
    def is_calibrated(self) -> bool:
        # UR arms are calibrated by the controller itself; nothing for LeRobot
        # to store or replay here.
        return True

    def calibrate(self) -> None:
        pass

    # ------------------------------------------------------------------- i/o

    def get_observation(self) -> dict[str, Any]:
        if not self.is_connected:
            raise DeviceNotConnectedError(f"{self} is not connected.")

        joint_positions = self.rtde_rec.getActualQ()
        obs_dict: dict[str, Any] = {k: float(v) for k, v in zip(JOINT_KEYS, joint_positions, strict=True)}

        if self.with_gripper:
            assert self.gripper is not None
            obs_dict[GRIPPER_KEY] = self.gripper.get_current_position() / 255.0  # -> [0, 1]
        else:
            # Placeholder so the observation vector keeps its 7th element.
            obs_dict[GRIPPER_KEY] = 0.0

        for cam_key, cam in self.cameras.items():
            obs_dict[cam_key] = cam.async_read()

        return obs_dict

    def send_action(self, action: dict[str, float]) -> dict[str, float]:
        if not self.is_connected:
            raise DeviceNotConnectedError(f"{self} is not connected.")

        unknown = set(action) - set(self.action_features)
        if unknown:
            raise ValueError(f"Invalid action keys {sorted(unknown)}; expected {list(self.action_features)}")
        missing = set(JOINT_KEYS) - set(action)
        if missing:
            raise ValueError(f"Action is missing joint keys {sorted(missing)}")

        goal = [float(action[k]) for k in JOINT_KEYS]

        sj = self.config.servoj
        self.rtde_ctrl.servoJ(
            goal,
            sj.acceleration,
            sj.speed,
            sj.time_s,
            sj.lookahead_time_s,
            sj.gain,
        )
        self._last_goal = goal

        sent: dict[str, float] = dict(zip(JOINT_KEYS, goal, strict=True))

        if self.with_gripper:
            assert self.gripper is not None
            gripper_cmd = float(np.clip(action.get(GRIPPER_KEY, 0.0) * 255.0, 0, 255))
            self.gripper.move(int(gripper_cmd), self.gripper_speed, self.gripper_force)
            sent[GRIPPER_KEY] = gripper_cmd / 255.0
        else:
            # Echo back the placeholder so callers (and the dataset writer) see
            # the same 7 keys they would on the real cell.
            sent[GRIPPER_KEY] = float(action.get(GRIPPER_KEY, 0.0))

        return sent
