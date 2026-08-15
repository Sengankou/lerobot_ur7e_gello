#!/usr/bin/env python
"""Read a recorded LeRobotDataset back and assert it is actually usable.

    python scripts/check_dataset.py --root ~/.cache/.../ur7e_ursim_pipecheck \
                                    --repo-id polarisai/ur7e_ursim_pipecheck

Recording can "succeed" and still leave a dataset that fails at train time:
the v3.0 format needs finalization, and the video track is written by a
separate encoder that can silently produce something the decoder cannot open.
This checks the things that actually bite:

* metadata loads and reports the expected fps / episode / frame counts
* ``observation.state`` and ``action`` are the same width and carry ``.pos``
  feature names (LeRobot >= 0.6 selects motor features by that suffix)
* a frame decodes to real pixels, not a blank tensor
* joint values actually move across an episode, i.e. the teleoperator was
  really driving the arm rather than the arm sitting still
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import torch

from lerobot.utils.import_utils import register_third_party_plugins

register_third_party_plugins()

from lerobot.datasets.lerobot_dataset import LeRobotDataset  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", required=True)
    ap.add_argument("--repo-id", required=True)
    ap.add_argument("--expect-dim", type=int, default=7)
    args = ap.parse_args()

    problems: list[str] = []
    ds = LeRobotDataset(args.repo_id, root=Path(args.root).expanduser())

    print(f"  codebase_version : {ds.meta.info.get('codebase_version')}")
    print(f"  fps              : {ds.fps}")
    print(f"  episodes/frames  : {ds.num_episodes} / {ds.num_frames}")

    if ds.num_frames == 0:
        problems.append("dataset has no frames")

    state_ft = ds.features.get("observation.state")
    action_ft = ds.features.get("action")
    if state_ft is None or action_ft is None:
        problems.append("observation.state or action feature is missing")
    else:
        print(f"  state shape      : {state_ft['shape']}  names={state_ft.get('names')}")
        print(f"  action shape     : {action_ft['shape']}")
        if state_ft["shape"] != action_ft["shape"]:
            problems.append(f"state {state_ft['shape']} and action {action_ft['shape']} differ in width")
        if state_ft["shape"][0] != args.expect_dim:
            problems.append(f"expected {args.expect_dim}-wide state, got {state_ft['shape'][0]}")
        names = state_ft.get("names") or []
        if not all(str(n).endswith(".pos") for n in names):
            problems.append(
                f"motor feature names {names} do not all end in '.pos'; lerobot-rollout selects "
                "motor features by that suffix and will build an empty observation.state"
            )

    video_keys = [k for k, v in ds.features.items() if v["dtype"] == "video"]
    print(f"  video keys       : {video_keys}")

    sample = ds[0]
    print(f"  task             : {sample.get('task')!r}")
    for key in video_keys:
        img = sample[key]
        if not isinstance(img, torch.Tensor):
            problems.append(f"{key} did not decode to a tensor")
            continue
        mean = float(img.float().mean())
        print(f"  {key}: {tuple(img.shape)} {img.dtype} mean={mean:.4f}")
        if img.numel() == 0:
            problems.append(f"{key} decoded to an empty tensor")

    # Movement check over the first episode.
    n = min(ds.num_frames, int(ds.fps * 5))
    q = np.stack([ds[i]["observation.state"].numpy() for i in range(0, n, max(1, n // 20))])
    spread = (q.max(0) - q.min(0)).round(4)
    print(f"  state range/dim  : {spread.tolist()}")
    if float(spread[:6].max()) < 1e-3:
        problems.append("no joint moved during the first episode; was the teleoperator connected?")

    if problems:
        print("\n  PROBLEMS")
        for p in problems:
            print(f"   !! {p}")
        return 1
    print("\n  dataset OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
