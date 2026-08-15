"""Jog a UR arm joint-by-joint from the keyboard.

Purpose
-------
LeRobot's built-in ``keyboard`` teleoperator emits *key events*, not joint
targets, so it cannot drive a 6-DOF joint-space robot directly. This one emits
exactly the action dict the GELLO leader emits -- ``joint_0..joint_5`` plus
``gripper`` -- which makes it a drop-in stand-in for the leader arm whenever the
leader is unavailable, and a genuinely useful bring-up tool on the real cell
(jog to a start pose, verify direction conventions, check joint limits).

Key map (letters jog +, the key below jogs -)::

    joint_0  q / a        joint_3  r / f
    joint_1  w / s        joint_4  t / g
    joint_2  e / d        joint_5  y / h
    gripper  o (open) / p (close)
    home     space  -> snap the target back to the seed pose
    quit     esc or ctrl-c

Safety
------
The target vector is *seeded from the arm's actual pose* over RTDE receive
(read-only; no control session, no running program required). Without that,
the first ``send_action`` would command whatever pose this class happened to
start with and the arm would lurch towards it.
"""

from __future__ import annotations

import logging
import select
import sys
import termios
import tty
from threading import Event, Lock, Thread

from lerobot.teleoperators import Teleoperator
from lerobot.utils.errors import DeviceAlreadyConnectedError, DeviceNotConnectedError

from .config_keyboard_joint import KeyboardJointConfig

logger = logging.getLogger(__name__)

JOINT_KEYS = [f"joint_{i}" for i in range(6)]

#: key -> (joint index, direction)
JOG_KEYS: dict[str, tuple[int, float]] = {
    "q": (0, +1.0), "a": (0, -1.0),
    "w": (1, +1.0), "s": (1, -1.0),
    "e": (2, +1.0), "d": (2, -1.0),
    "r": (3, +1.0), "f": (3, -1.0),
    "t": (4, +1.0), "g": (4, -1.0),
    "y": (5, +1.0), "h": (5, -1.0),
}
GRIPPER_KEYS: dict[str, float] = {"o": +1.0, "p": -1.0}
HOME_KEY = " "
QUIT_KEYS = {"\x1b", "\x03"}  # esc, ctrl-c


#: Printed on connect so the operator does not have to look up the mapping.
KEYMAP_HELP = (
    "keys: joint_0 q/a  joint_1 w/s  joint_2 e/d  joint_3 r/f  joint_4 t/g  "
    "joint_5 y/h  gripper o/p  home <space>  quit <esc>"
)


class KeyboardJoint(Teleoperator):
    """Keyboard joint-jog teleoperator emitting the same action dict as GELLO."""

    config_class = KeyboardJointConfig
    name = "keyboard_joint"

    def __init__(self, config: KeyboardJointConfig):
        super().__init__(config)
        self.config = config

        self._lock = Lock()
        self._target: list[float] | None = None
        self._gripper: float = 0.0
        self._seed: list[float] | None = None

        self._connected = False
        self._stop: Event | None = None
        self._thread: Thread | None = None
        self._listener = None  # pynput listener, when that backend is used
        self._old_term_attrs = None
        self.should_quit = False

    # ------------------------------------------------------------------ specs

    @property
    def action_features(self) -> dict[str, type]:
        # Identical to the GELLO leader's, on purpose: either device can drive
        # the same robot and produce the same dataset schema.
        return {**{k: float for k in JOINT_KEYS}, "gripper": float}

    @property
    def feedback_features(self) -> dict[str, type]:
        return {}

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def is_calibrated(self) -> bool:
        return True

    def calibrate(self) -> None:
        pass

    def configure(self) -> None:
        pass

    # ------------------------------------------------------------- lifecycle

    def _resolve_backend(self) -> str:
        if self.config.input_backend != "auto":
            return self.config.input_backend
        return "stdin" if (sys.stdin is not None and sys.stdin.isatty()) else "pynput"

    def _seed_target(self) -> list[float]:
        if self.config.seed_from == "home":
            return list(self.config.home_position)

        # Receive-only RTDE: works whether or not a program is playing, and
        # never takes control of the arm.
        import rtde_receive

        rec = rtde_receive.RTDEReceiveInterface(self.config.robot_ip)
        try:
            q = [float(v) for v in rec.getActualQ()]
        finally:
            rec.disconnect()
        logger.info("Seeded keyboard target from the arm's current pose: %s", [round(v, 3) for v in q])
        return q

    def connect(self, calibrate: bool = True) -> None:
        if self._connected:
            raise DeviceAlreadyConnectedError(f"{self} already connected")

        seed = self._seed_target()
        with self._lock:
            self._seed = list(seed)
            self._target = list(seed)
            self._gripper = 0.0

        backend = self._resolve_backend()
        if backend == "stdin":
            self._start_stdin_reader()
        else:
            self._start_pynput_listener()

        self._connected = True
        logger.info("%s connected (backend=%s). %s", self, backend, KEYMAP_HELP)

    def disconnect(self) -> None:
        if not self._connected:
            logger.warning("%s was not connected; nothing to disconnect.", self)
            return

        if self._stop is not None:
            self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None
        self._stop = None

        if self._listener is not None:
            self._listener.stop()
            self._listener = None

        self._restore_terminal()
        self._connected = False

    # ---------------------------------------------------------------- inputs

    def _apply_key(self, key: str) -> None:
        """Fold one keystroke into the target vector. Backend-independent."""
        if key in QUIT_KEYS:
            self.should_quit = True
            return

        with self._lock:
            if self._target is None:
                return
            if key in JOG_KEYS:
                idx, direction = JOG_KEYS[key]
                self._target[idx] += direction * self.config.step_rad
            elif key in GRIPPER_KEYS:
                self._gripper = min(
                    1.0, max(0.0, self._gripper + GRIPPER_KEYS[key] * self.config.gripper_step)
                )
            elif key == HOME_KEY and self._seed is not None:
                self._target = list(self._seed)

    def _restore_terminal(self) -> None:
        if self._old_term_attrs is not None:
            try:
                termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self._old_term_attrs)
            except Exception:
                logger.debug("Could not restore terminal attributes.", exc_info=True)
            self._old_term_attrs = None

    def _start_stdin_reader(self) -> None:
        """Read single keystrokes from stdin without waiting for Enter.

        cbreak mode only when stdin is a real terminal; when it is a pipe (the
        verification ladder feeds keys from a file) plain reads already give us
        one character at a time.
        """
        fd = sys.stdin.fileno()
        if sys.stdin.isatty():
            self._old_term_attrs = termios.tcgetattr(fd)
            tty.setcbreak(fd)

        self._stop = Event()

        def loop() -> None:
            while self._stop is not None and not self._stop.is_set():
                # select() so the thread wakes up to check _stop even when no
                # key is pressed; a bare read() would block until process exit.
                try:
                    ready, _, _ = select.select([sys.stdin], [], [], 0.1)
                except Exception:
                    return
                if not ready:
                    continue
                ch = sys.stdin.read(1)
                if not ch:  # EOF: a piped key script ran out
                    return
                self._apply_key(ch.lower())

        self._thread = Thread(target=loop, name=f"{self}_stdin_reader", daemon=True)
        self._thread.start()

    def _start_pynput_listener(self) -> None:
        from pynput import keyboard

        def on_press(key) -> None:
            try:
                ch = key.char
            except AttributeError:
                ch = " " if key == keyboard.Key.space else ("\x1b" if key == keyboard.Key.esc else None)
            if ch:
                self._apply_key(ch.lower())

        self._listener = keyboard.Listener(on_press=on_press)
        self._listener.start()

    # ---------------------------------------------------------------- output

    def get_action(self) -> dict[str, float]:
        if not self._connected:
            raise DeviceNotConnectedError(f"{self} is not connected.")
        with self._lock:
            assert self._target is not None
            action = {k: float(v) for k, v in zip(JOINT_KEYS, self._target, strict=True)}
            action["gripper"] = float(self._gripper)
        return action

    def send_feedback(self, feedback: dict[str, float]) -> None:
        raise NotImplementedError
