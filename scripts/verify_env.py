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
            same_family = [a for a in arch_list if a.startswith(f"sm_{cap[0]}")]
            ptx = [a for a in arch_list if a.startswith("compute_")]
            if native in arch_list:
                pass  # exact kernels present, nothing to say
            elif same_family:
                # CUDA 13 runs a cubin on a newer minor of the same
                # architecture family. Measured on GB10 (sm_121) with torch
                # 2.11, whose arch list stops at sm_120 and carries no PTX at
                # all: matmul runs at full speed with correct results, and
                # torch emits no capability warning. So same-major is fine.
                line("family compat", f"{native} served by {', '.join(same_family)}")
            elif ptx:
                notes.append(
                    f"no {native} kernels and no same-family kernels; torch will "
                    f"PTX-JIT from {ptx[-1]} at first launch (one-off delay)."
                )
            else:
                problems.append(
                    f"this torch build has neither {native} kernels, nor sm_{cap[0]}x "
                    f"kernels to fall back on, nor PTX to JIT from (has "
                    f"{', '.join(arch_list)}). It cannot run on this GPU."
                )
        else:
            problems.append("CUDA is not available; training and rollout will fall back to CPU.")
    except Exception as e:
        problems.append(f"torch import failed: {e}")

    # torchcodec is the one that silently mispairs. lerobot's constraint is wide
    # and torchcodec declares no torch dependency at all, so the resolver can
    # hand you a build for a different torch. It fails at *import*, and only
    # when reading a dataset back -- long after recording "succeeded".
    print("\n== torchcodec (video decode)")
    try:
        import importlib.metadata as _md

        line("version", _md.version("torchcodec"))
        from torchcodec.decoders import VideoDecoder  # noqa: F401

        line("decoder import", "ok")
    except Exception as e:
        first = str(e).strip().splitlines()[0] if str(e).strip() else repr(e)
        line("decoder import", f"FAILED - {first[:100]}")
        problems.append(
            "torchcodec cannot be imported, so datasets will record fine and then "
            "fail to load. Two usual causes: (a) it is paired with a different "
            "torch -- 'undefined symbol' means this, and the fix is to move "
            "torch/torchvision/torchcodec together (see envs/ur7e.yaml); "
            "(b) $CONDA_PREFIX/lib is not on the loader path -- 'libavutil.so.NN: "
            "cannot open shared object file' means this, and scripts/setup_env.sh "
            "installs the activate.d hook that fixes it."
        )

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
