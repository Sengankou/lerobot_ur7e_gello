"""GELLO teleoperator interface using Dynamixel motors.

Implements the LeRobot Teleoperator interface for the GELLO 7-DOF leader device.
Reads joint positions from Dynamixel servos and applies calibration offsets.
"""

import logging
import json
import sys
import time
import numpy as np
from threading import Thread, Event, Lock
from dataclasses import dataclass
from lerobot.motors import Motor, MotorNormMode
from lerobot.motors.dynamixel import (
    DynamixelMotorsBus,
    OperatingMode,
)
from lerobot.teleoperators import Teleoperator
from lerobot.utils.errors import DeviceAlreadyConnectedError, DeviceNotConnectedError
from .config_gello import GelloConfig
from .mock_bus import MockDynamixelBus
from pathlib import Path
logger = logging.getLogger(__name__)

@dataclass
class GelloCalibration:
    joint_offsets: dict[str, int] # map from motor name to offset in counts
    gripper_open_position: int # motor counts for the open position
    gripper_closed_position: int # motor counts for the closed position

class Gello(Teleoperator):
    """
    This is the GELLO teleoperator for the ur5 robot.
    The hardware is from Phillip Wu: https://wuphilipp.github.io/gello_site/
    """

    config_class = GelloConfig
    name = "gello"
    RAD_PER_COUNT = 2 * np.pi / (4096 - 1)
    JOINT_NAMES = ["joint_0", "joint_1", "joint_2", "joint_3", "joint_4", "joint_5"]

    def __init__(self, config: GelloConfig):
        super().__init__(config)
        self.config = config
        self.calibration = None
        motors = {
            "joint_0": Motor(1, "xl330-m288", MotorNormMode.RANGE_M100_100),
            "joint_1": Motor(2, "xl330-m288", MotorNormMode.RANGE_M100_100),
            "joint_2": Motor(3, "xl330-m288", MotorNormMode.RANGE_M100_100),
            "joint_3": Motor(4, "xl330-m288", MotorNormMode.RANGE_M100_100),
            "joint_4": Motor(5, "xl330-m288", MotorNormMode.RANGE_M100_100),
            "joint_5": Motor(6, "xl330-m288", MotorNormMode.RANGE_M100_100),
            "gripper": Motor(7, "xl330-m077", MotorNormMode.RANGE_0_100),
        }
        # Only the lowest layer is swapped when running without hardware; every
        # line below this point is the same code that drives the real leader.
        if self.config.mock:
            logger.warning(
                "Gello running with the SIMULATED bus (teleop.gello.mock=true). "
                "No Dynamixel hardware is being read."
            )
            self.bus = MockDynamixelBus(
                port=self.config.port,
                motors=motors,
                amplitude_rad=self.config.mock_amplitude_rad,
                period_s=self.config.mock_period_s,
                rad_per_count=self.RAD_PER_COUNT,
                joint_signs=self.config.joint_signs,
            )
        else:
            self.bus = DynamixelMotorsBus(port=self.config.port, motors=motors)

        self.thread: Thread | None = None
        self.stop_event: Event | None = None
        self.lock: Lock = Lock()
        self.latest_action: dict[str, float] | None = None

    @property
    def action_features(self) -> dict[str, type]:
        return {motor: float for motor in self.bus.motors}

    @property
    def feedback_features(self) -> dict[str, type]:
        return {}

    @property
    def is_connected(self) -> bool:
        return self.bus.is_connected

    def connect(self, calibrate: bool = True) -> None:
        if self.is_connected:
            raise DeviceAlreadyConnectedError(f"{self} already connected")

        self.bus.connect(handshake=False)
        self.bus.set_baudrate(self.config.baudrate)
        self.bus._handshake()
        self.bus._assert_motors_exist()
        self._load_calibration()
        if not self.is_calibrated and calibrate:
            logger.info(
                "Mismatch between calibration values in the motor and the calibration file or no calibration file found"
            )
            self.calibrate()

        self.configure()

        if self.config.use_async:
            # Initial read to populate latest_action
            raw_action = self.bus.sync_read("Present_Position", normalize=False)
            self.latest_action = self._process_action(raw_action)
            self._start_read_thread()

        logger.info(f"{self} connected.")

    @property
    def is_calibrated(self) -> bool:
        return self.calibration is not None

    @property
    def _interactive(self) -> bool:
        """Whether we may block on ``input()``.

        False with the simulated bus (there is nothing to physically move) and
        false when stdin is not a terminal, so unattended runs -- CI, the
        verification ladder, `lerobot-record` under nohup -- capture
        calibration instead of hanging forever on a prompt.
        """
        return not self.config.mock and sys.stdin is not None and sys.stdin.isatty()

    def calibrate(self) -> None:
        self.bus.disable_torque()
        if self.calibration and self._interactive:
            # Calibration exists exists, ask user whether to use it or run new calibration
            user_input = input(
                "Press ENTER to use existing calibration, or type 'c' and press ENTER to run new calibration: "
            )
            if user_input.strip().lower() != "c":
                logger.info("Using existing calibration")
                return
        elif self.calibration:
            logger.info("Using existing calibration (non-interactive session)")
            return

        logger.info(f"\nRunning calibration of {self}")

        if self._interactive:
            input(f"Move {self} to the home position and press ENTER....")
        else:
            logger.info("Non-interactive session: capturing the current pose as the home position.")

        start_joints = self.bus.sync_read("Present_Position", normalize=False)
        calibration = GelloCalibration(
            joint_offsets={motor: start_joints[motor] for motor in self.JOINT_NAMES},
            gripper_open_position=start_joints["gripper"],
            gripper_closed_position=start_joints["gripper"] - self.config.gripper_travel_counts,
        )
        self._warn_on_implausible_offsets(calibration)
        self.calibration = calibration
        # Save calibration to file
        self.calibration_fpath.parent.mkdir(parents=True, exist_ok=True)
        with open(self.calibration_fpath, "w") as f:
            json.dump(calibration.__dict__, f, indent=2)
        logger.info(f"Calibration saved to {self.calibration_fpath}")

    def _warn_on_implausible_offsets(self, calibration: "GelloCalibration") -> None:
        """Sanity-check fresh offsets against the documented bench values.

        The GELLO home jig puts every joint at a multiple of pi/2, and
        ``RAD_PER_COUNT = 2*pi/4096`` means k*pi/2 rad == k*1024 counts. Offsets
        that are not near a multiple of 1024 usually mean the arm was not in
        the jig, or a servo was reassembled a full turn out.
        """
        for motor, offset in calibration.joint_offsets.items():
            nearest_k = round(offset / 1024)
            residual = offset - nearest_k * 1024
            if abs(residual) > 200:  # ~17 deg of mounting error
                logger.warning(
                    "%s offset %d counts is %+d from the nearest k*1024 (k=%d). "
                    "Expected the arm to be in the home jig; check the pose and re-run.",
                    motor,
                    offset,
                    residual,
                    nearest_k,
                )

    def configure(self) -> None:
        self.bus.disable_torque()
        self.bus.configure_motors()
        for motor in self.bus.motors:
            if motor != "gripper":
                # Use 'extended position mode' for all motors except gripper, because in joint mode the servos
                # can't rotate more than 360 degrees (from 0 to 4095) And some mistake can happen while
                # assembling the arm, you could end up with a servo with a position 0 or 4095 at a crucial
                # point
                self.bus.write("Operating_Mode", motor, OperatingMode.EXTENDED_POSITION.value)

        # Use 'position control current based' for gripper to be limited by the limit of the current.
        # For the follower gripper, it means it can grasp an object without forcing too much even tho,
        # its goal position is a complete grasp (both gripper fingers are ordered to join and reach a touch).
        # For the leader gripper, it means we can use it as a physical trigger, since we can force with our finger
        # to make it move, and it will move back to its original target position when we release the force.
        self.bus.write("Operating_Mode", "gripper", OperatingMode.CURRENT_POSITION.value)

    def setup_motors(self) -> None:
        for motor in reversed(self.bus.motors):
            input(f"Connect the controller board to the '{motor}' motor only and press enter.")
            self.bus.setup_motor(motor)
            print(f"'{motor}' motor id set to {self.bus.motors[motor].id}")

    def _process_action(self, raw_action: dict[str, int]) -> dict[str, float]:
        # Normalize joint positions to [-pi, pi] and gripper position to [0, 1]
        result = {}
        for idx, motor in enumerate(self.JOINT_NAMES):
            offset = self.calibration.joint_offsets[motor]
            sign = self.config.joint_signs[idx]
            ref_pos_rad = self.config.calibration_position[idx]
            angle_rad = sign * (raw_action[motor] - offset) * self.RAD_PER_COUNT + ref_pos_rad
            result[motor] = angle_rad
        result["gripper"] = (raw_action["gripper"] - self.calibration.gripper_open_position) / (self.calibration.gripper_closed_position - self.calibration.gripper_open_position)
        return result

    def _read_loop(self) -> None:
        if self.stop_event is None:
            raise RuntimeError(f"{self}: stop_event is not initialized before starting read loop.")

        while not self.stop_event.is_set():
            try:
                raw_action = self.bus.sync_read("Present_Position", normalize=False)
                new_action = self._process_action(raw_action)

                with self.lock:
                    if self.latest_action is None:
                        self.latest_action = new_action
                    else:
                        # Apply EMA smoothing
                        alpha = self.config.smoothing
                        for k, v in new_action.items():
                            self.latest_action[k] = alpha * v + (1 - alpha) * self.latest_action[k]

            except Exception:
                time.sleep(0.1)

    def _start_read_thread(self) -> None:
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(timeout=0.1)
        if self.stop_event is not None:
            self.stop_event.set()

        self.stop_event = Event()
        self.thread = Thread(target=self._read_loop, args=(), name=f"{self}_read_loop")
        self.thread.daemon = True
        self.thread.start()

    def _stop_read_thread(self) -> None:
        if self.stop_event is not None:
            self.stop_event.set()

        if self.thread is not None and self.thread.is_alive():
            self.thread.join(timeout=2.0)

        self.thread = None
        self.stop_event = None

    def get_action(self) -> dict[str, float]:
        if not self.is_connected:
            raise DeviceNotConnectedError(f"{self} is not connected.")

        if self.config.use_async:
            with self.lock:
                if self.latest_action is None:
                    # If we haven't received data yet, perform a sync read
                    logger.warning(f"{self} async read loop has not updated latest_action yet. Performing synchronous read.")
                    raw_action = self.bus.sync_read("Present_Position", normalize=False)
                    self.latest_action = self._process_action(raw_action)

                return self.latest_action.copy()

        else:
            start = time.perf_counter()
            raw_action = self.bus.sync_read("Present_Position", normalize=False)
            result = self._process_action(raw_action)
            dt_bus_read_s = time.perf_counter() - start
            # debug, not print: this runs once per control tick and would
            # otherwise shred the teleop status display at 125 Hz.
            logger.debug("bus.sync_read took %.4f s", dt_bus_read_s)
            return result

    def send_feedback(self, feedback: dict[str, float]) -> None:
        # TODO(rcadene, aliberts): Implement force feedback
        raise NotImplementedError

    def disconnect(self) -> None:
        if not self.is_connected:
            # Callers put disconnect() in a `finally`, so raising here would
            # replace the real failure (e.g. a connect error) with a confusing
            # "not connected" traceback.
            logger.warning("%s was not connected; nothing to disconnect.", self)
            return

        if self.config.use_async:
            self._stop_read_thread()

        self.bus.disconnect()
        logger.info(f"{self} disconnected.")

    def _load_calibration(self, fpath: Path | None = None) -> None:
        if fpath is None:
            fpath = self.calibration_fpath
        if fpath.is_file():
            with open(fpath, "r") as f:
                self.calibration = GelloCalibration(**json.load(f))
            logger.info(f"Calibration loaded from {fpath}")
        else:
            logger.info(f"No calibration file found at {fpath}")
            self.calibration = None