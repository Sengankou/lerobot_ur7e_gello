"""Configuration for the keyboard joint-jog teleoperator."""

from dataclasses import dataclass, field

from lerobot.teleoperators.config import TeleoperatorConfig

from ur7e_site import get as site_get


@TeleoperatorConfig.register_subclass("keyboard_joint")
@dataclass
class KeyboardJointConfig(TeleoperatorConfig):
    #: Radians added to a joint target per key press (per tick while held, with
    #: the pynput backend). Deliberately small: this drives a real arm.
    step_rad: float = field(default_factory=lambda: float(site_get("teleop.keyboard_joint.step_rad", 0.02)))
    #: Normalised gripper delta per key press, in [0, 1] space.
    gripper_step: float = field(
        default_factory=lambda: float(site_get("teleop.keyboard_joint.gripper_step", 0.05))
    )

    #: Where the initial joint target comes from. "robot" reads the arm's
    #: current pose over RTDE (receive only -- no control, no program needed)
    #: so the first command cannot make the arm jump. "home" starts from
    #: `home_position` instead, which is only safe if the arm is already there.
    seed_from: str = "robot"
    #: Controller IP used when seed_from == "robot".
    robot_ip: str = field(default_factory=lambda: site_get("robot.ip", "192.168.56.101"))
    home_position: list[float] = field(default_factory=lambda: [0.0, -1.57, 1.57, -1.57, -1.57, 0.0])

    #: Key source. "stdin" reads raw keystrokes from the terminal, which works
    #: over SSH and can be driven from a pipe (used by the verification ladder).
    #: "pynput" installs a global hotkey listener and needs a desktop session.
    #: "auto" prefers stdin when attached to a TTY, else pynput.
    input_backend: str = "auto"

    def __post_init__(self) -> None:
        if self.id is None:
            self.id = "keyboard_joint"
        if self.seed_from not in ("robot", "home"):
            raise ValueError(f"seed_from must be 'robot' or 'home', got {self.seed_from!r}")
        if self.input_backend not in ("auto", "stdin", "pynput"):
            raise ValueError(f"input_backend must be auto/stdin/pynput, got {self.input_backend!r}")
