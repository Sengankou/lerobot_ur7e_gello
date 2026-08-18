#!/usr/bin/env python
"""Post-install check: is everything LeRobot needs actually wired up?

    python scripts/verify_env.py

Answers, in one screen: which lerobot is imported and from where, whether CUDA
sees the GPU with native kernels, whether the three plugin packages registered
themselves, and which site config is active. Run this first whenever something
behaves oddly -- it catches the "pip installed into base instead of the conda
env" trap and the "plugin import silently failed" trap, which look identical
from the CLI otherwise.
"""

from __future__ import annotations

import importlib.metadata as md
import platform
import sys


def line(label: str, value: object) -> None:
    print(f"  {label:<28}: {value}")


def main() -> int:
    problems: list[str] = []   # exit 1
    notes: list[str] = []      # worth knowing, not a failure

    print("\n== interpreter")
    line("python", sys.version.split()[0])
    line("executable", sys.executable)
    line("machine", platform.machine())
    if "envs/" not in sys.executable and "ur7e" not in sys.executable:
        problems.append(
            f"python is {sys.executable}, which does not look like the ur7e conda env. "
            "Under miniforge a bare `pip` often installs into base instead."
        )

    print("\n== torch")
    try:
        import torch

        line("torch", torch.__version__)
        line("cuda runtime", torch.version.cuda)
        line("cuda available", torch.cuda.is_available())
        if torch.cuda.is_available():
            cap = torch.cuda.get_device_capability(0)
            line("device", torch.cuda.get_device_name(0))
            line("compute capability", f"sm_{cap[0]}{cap[1]}")
            arch_list = torch.cuda.get_arch_list()
            line("built for", ", ".join(arch_list))
            native = f"sm_{cap[0]}{cap[1]}"
            if native not in arch_list:
                # A NOTE, not a problem. PTX from compute_1xx forward-compiles
                # to a newer minor in the same family, so this costs a one-off
                # JIT delay at first kernel launch rather than breaking.
                # Confirmed in practice: the so101 / openarm / smolvla
                # environments run torch 2.10+cu130 on GB10 (sm_121) on the
                # Sparks today. Escalate to the NGC PyTorch container only if
                # you actually observe a hang.
                ptx = [a for a in arch_list if a.startswith("compute_")]
                notes.append(
                    f"no native {native} kernels in this torch build "
                    f"(has {', '.join(arch_list)}). Expect a one-off PTX-JIT "
                    f"delay at first kernel launch"
                    + (f", compiled from {ptx[-1]}." if ptx else ".")
                )
        else:
            problems.append("CUDA is not available; training and rollout will fall back to CPU.")
    except Exception as e:
        problems.append(f"torch import failed: {e}")

    print("\n== lerobot")
    try:
        import lerobot

        line("version", md.version("lerobot"))
        line("path", lerobot.__file__)
    except Exception as e:
        problems.append(f"lerobot import failed: {e}")
        print("\n".join(f"  !! {p}" for p in problems))
        return 1

    print("\n== third-party plugin registration")
    from lerobot.utils.import_utils import register_third_party_plugins

    register_third_party_plugins()

    from lerobot.cameras import CameraConfig
    from lerobot.robots import RobotConfig
    from lerobot.teleoperators import TeleoperatorConfig

    robots = sorted(RobotConfig.get_known_choices())
    teleops = sorted(TeleoperatorConfig.get_known_choices())
    cameras = sorted(CameraConfig.get_known_choices())
    line("robots", robots)
    line("teleoperators", teleops)
    line("cameras", cameras)
    for name, pool, what in [
        ("ur7e", robots, "robot"),
        ("gello", teleops, "teleoperator"),
        ("keyboard_joint", teleops, "teleoperator"),
    ]:
        if name not in pool:
            problems.append(f"{what} '{name}' did not register (plugin import failed; re-run with -v)")

    print("\n== ur_rtde")
    try:
        import rtde_control

        line("has FLAG_USE_EXT_UR_CAP", hasattr(rtde_control.RTDEControlInterface, "FLAG_USE_EXT_UR_CAP"))
    except Exception as e:
        problems.append(f"ur_rtde import failed: {e}")

    print("\n== site config")
    try:
        from ur7e_site.config import describe

        line("active", describe())
    except Exception as e:
        problems.append(f"ur7e_site import failed: {e}")

    print()
    if notes:
        print("== NOTES (not failures)")
        for n in notes:
            print(f"  -- {n}")
        print()
    if problems:
        print("== PROBLEMS")
        for p in problems:
            print(f"  !! {p}")
        return 1
    print("== all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
