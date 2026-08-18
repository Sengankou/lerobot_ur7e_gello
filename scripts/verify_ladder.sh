#!/usr/bin/env bash
# The Phase C verification ladder, bottom to top.
#
#   ./scripts/verify_ladder.sh              # all rungs
#   ./scripts/verify_ladder.sh 3 4 5        # only these rungs
#
# Each rung is independent enough to re-run alone, but they build on each
# other's artefacts (3 records the dataset 4 trains on, 4 produces the
# checkpoint 5 rolls out). Every rung prints GREEN or RED and the run stops at
# the first RED, because a failure lower down makes everything above it
# meaningless.
#
# Preconditions: conda env "ur7e" active, URSim up with the External Control
# program PLAYING (scripts/ursim_bringup.sh, then press play on the pendant --
# with Looping enabled it stays playing across runs).
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

DATA_HOME="${DATA_HOME:-$HOME/.cache/ur7e_lerobot}"
REPO_ID="${REPO_ID:-polarisai/ur7e_ursim_pipecheck}"
DS_ROOT="$DATA_HOME/datasets/$(basename "$REPO_ID")"
ACT_OUT="$DATA_HOME/train/act_ursim"
FPS="${FPS:-30}"

rungs=("$@")
[ ${#rungs[@]} -eq 0 ] && rungs=(0 1 2 3 4 5)
has() { printf '%s\n' "${rungs[@]}" | grep -qx "$1"; }

green() { echo "== RUNG $1: GREEN -- $2"; }
red()   { echo "== RUNG $1: RED   -- $2"; exit 1; }

# Retire a path without ever calling rm. Uses `trash` when it is installed
# (it is on the RTX 5080 box, it is not on the Sparks), otherwise renames the
# directory out of the way with a timestamp. Either way the old data is still
# on disk if it turns out we wanted it.
retire() {
  local path="$1"
  [ -e "$path" ] || return 0
  if command -v trash >/dev/null 2>&1; then
    trash put "$path"
    echo "   retired -> trash: $path"
  else
    local dest="${path%/}.bak-$(date +%Y%m%d-%H%M%S)"
    mv "$path" "$dest"
    echo "   retired -> $dest"
    echo "   (delete old .bak-* dirs yourself when you are sure)"
  fi
}

echo "repo      : $REPO_DIR"
echo "dataset   : $DS_ROOT"
echo "rungs     : ${rungs[*]}"

# ---------------------------------------------------------------- 0: env
if has 0; then
  echo; echo "#### RUNG 0 -- environment ####"
  python scripts/verify_env.py || red 0 "environment check failed"
  green 0 "env, plugins and site config are sane"
fi

# ------------------------------------------------------------ 1: connect
if has 1; then
  echo; echo "#### RUNG 1 -- RTDE both directions ####"
  python scripts/robot_state.py || red 1 "cannot read robot state"
  python scripts/smoke_rtde.py --servo-hz 125 || red 1 "smoke test failed"
  green 1 "RTDE receive + External Control control, moveJ and servoJ at 125 Hz"
fi

# --------------------------------------------------------- 2: teleoperate
if has 2; then
  echo; echo "#### RUNG 2 -- teleoperate ####"
  python scripts/bench_teleop.py --fps 125 --seconds 6 --no-cameras || red 2 "125 Hz bench failed"
  python scripts/bench_teleop.py --fps "$FPS" --seconds 6          || red 2 "$FPS Hz bench failed"
  green 2 "mock GELLO drives the arm; rates measured and attributed"
fi

# -------------------------------------------------------------- 3: record
if has 3; then
  echo; echo "#### RUNG 3 -- record ####"
  retire "$DS_ROOT" || red 3 "could not clear old dataset"
  lerobot-record \
    --robot.type=ur7e \
    --teleop.type=gello \
    --dataset.repo_id="$REPO_ID" \
    --dataset.single_task="move the ur7e arm through the gello sweep" \
    --dataset.root="$DS_ROOT" \
    --dataset.fps="$FPS" \
    --dataset.num_episodes="${NUM_EPISODES:-6}" \
    --dataset.episode_time_s="${EPISODE_TIME_S:-8}" \
    --dataset.reset_time_s=1 \
    --dataset.push_to_hub=false \
    --play_sounds=false > /tmp/ur7e_record.log 2>&1 || { tail -30 /tmp/ur7e_record.log; red 3 "record failed"; }
  python scripts/check_dataset.py --root "$DS_ROOT" --repo-id "$REPO_ID" || red 3 "dataset does not load back"
  green 3 "dataset recorded, finalized and read back with decodable video"
fi

# --------------------------------------------------------------- 4: train
if has 4; then
  echo; echo "#### RUNG 4 -- train ####"
  for policy in ${POLICIES:-act smolvla}; do
    out="$DATA_HOME/train/${policy}_ursim"
    retire "$out" || red 4 "could not clear $out"
    echo "-- training $policy"
    lerobot-train \
      --dataset.repo_id="$REPO_ID" --dataset.root="$DS_ROOT" \
      --policy.type="$policy" --policy.device=cuda --policy.push_to_hub=false \
      --output_dir="$out" --job_name="${policy}_ursim_pipecheck" \
      --steps="${STEPS:-300}" --batch_size="${BATCH_SIZE:-8}" \
      --log_freq=100 --save_freq="${STEPS:-300}" --num_workers=2 \
      --wandb.enable=false > "/tmp/ur7e_train_${policy}.log" 2>&1 \
      || { tail -30 "/tmp/ur7e_train_${policy}.log"; red 4 "$policy training failed"; }
    grep -E "^INFO.*step:" "/tmp/ur7e_train_${policy}.log" | tail -3
    [ -d "$out/checkpoints/last/pretrained_model" ] || red 4 "$policy produced no checkpoint"
  done
  green 4 "training loop closes and checkpoints are written"
fi

# ------------------------------------------------------------- 5: rollout
if has 5; then
  echo; echo "#### RUNG 5 -- rollout ####"
  CKPT="$ACT_OUT/checkpoints/last/pretrained_model"
  [ -d "$CKPT" ] || red 5 "no ACT checkpoint at $CKPT (run rung 4 first)"
  python scripts/robot_state.py
  lerobot-rollout \
    --robot.type=ur7e \
    --policy.path="$CKPT" --policy.device=cuda \
    --fps="$FPS" --duration="${ROLLOUT_S:-10}" \
    --task="move the ur7e arm through the gello sweep" \
    --play_sounds=false > /tmp/ur7e_rollout.log 2>&1 \
    || { tail -30 /tmp/ur7e_rollout.log; red 5 "rollout failed"; }
  python scripts/robot_state.py
  green 5 "policy checkpoint drives URSim through the standard rollout path"
fi

echo; echo "== ladder finished =="
