#!/usr/bin/env bash
# Build the "ur7e" conda environment from scratch.
#
#   ./scripts/setup_env.sh
#
# Idempotent-ish: if the env already exists it is reused and only the pip layer
# is re-run. Everything machine-specific comes from environment variables so the
# same script works on the RTX 5080 box and on the DGX Spark:
#
#   LEROBOT_DIR   where the lerobot core clone lives      (default ~/lerobot)
#   LEROBOT_REF   git ref of lerobot core to pin to       (default v0.6.0)
#   ENV_NAME      conda environment name                  (default ur7e)
#
# See docs/MIGRATION.md for the aarch64 delta (ur_rtde source build, no URSim).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LEROBOT_DIR="${LEROBOT_DIR:-$HOME/lerobot}"
LEROBOT_REF="${LEROBOT_REF:-v0.6.0}"
ENV_NAME="${ENV_NAME:-ur7e}"

# lerobot extras we depend on:
#   dataset/training -> LeRobotDataset v3.0 + wandb + accelerate (lerobot-train)
#   smolvla          -> transformers/num2words for the SmolVLA policy
#   dynamixel        -> dynamixel-sdk, needed by the GELLO teleoperator
#   hardware         -> pynput etc., needed by the keyboard teleoperators
LEROBOT_EXTRAS="${LEROBOT_EXTRAS:-dataset,training,smolvla,dynamixel,hardware}"

echo "== repo        : $REPO_DIR"
echo "== lerobot dir : $LEROBOT_DIR (ref $LEROBOT_REF)"
echo "== conda env   : $ENV_NAME"

# ---------------------------------------------------------------- lerobot core
if [ ! -d "$LEROBOT_DIR/.git" ]; then
  echo "== cloning lerobot core"
  git clone https://github.com/huggingface/lerobot.git "$LEROBOT_DIR"
fi
git -C "$LEROBOT_DIR" fetch --tags --quiet
git -C "$LEROBOT_DIR" checkout --quiet "$LEROBOT_REF"
echo "== lerobot SHA : $(git -C "$LEROBOT_DIR" rev-parse HEAD)"

# ---------------------------------------------------------------- conda env
eval "$(conda shell.bash hook)"
if ! conda env list | awk '{print $1}' | grep -qx "$ENV_NAME"; then
  echo "== creating conda env"
  conda env create -n "$ENV_NAME" -f "$REPO_DIR/envs/ur7e.yaml"
else
  echo "== conda env already exists, reusing"
fi
conda activate "$ENV_NAME"

# ---------------------------------------------------------------- pip layer
# NOTE: always call `python -m pip` -- a bare `pip` under miniforge can resolve
# to the base environment's pip and silently install into base (known trap).
python -m pip install -e "${LEROBOT_DIR}[${LEROBOT_EXTRAS}]"

# ur7e_site first: the plugin configs import it at module load time to read
# their defaults out of config/site.yaml.
python -m pip install -e "$REPO_DIR/ur7e_site"

# Plugin packages. The lerobot_robot_/lerobot_teleoperator_/lerobot_camera_
# name prefixes are what makes register_third_party_plugins() pick them up.
#
# NOTE: lerobot_camera_zmq is deliberately NOT installed. LeRobot 0.6.0 ships
# its own "zmq" camera; the in-tree plugin now registers as "zmq_legacy" to
# avoid the duplicate-choice crash, and the built-in supersedes it.
python -m pip install \
  -e "$REPO_DIR/lerobot_robot_ur5e" \
  -e "$REPO_DIR/lerobot_teleoperator_gello" \
  -e "$REPO_DIR/lerobot_teleoperator_keyboard_joint"

echo
echo "== done. verify with: conda activate $ENV_NAME && ./scripts/verify_env.py"
