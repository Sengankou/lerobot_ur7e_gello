#!/usr/bin/env bash
# Build the "ur7e" conda environment.
#
#   ./scripts/setup_env.sh
#
# What it does, in order:
#   1. clone lerobot core into $UR7E_ROOT/lerobot and pin it to $LEROBOT_REF
#   2. create (or update) the conda env from envs/ur7e.yaml
#   3. install the ffmpeg loader-path hook that torchcodec needs
#
# Dependencies live in envs/ur7e.yaml, NOT here. To add a package, edit that
# file and re-run this script (or `conda env update -f envs/ur7e.yaml --prune`),
# the same way envs/so101.yaml and envs/smolvla.yaml are maintained.
#
# Layout it assumes:
#   $UR7E_ROOT/lerobot/              lerobot core, pinned, UR7e ONLY
#   $UR7E_ROOT/lerobot_ur7e_gello/   this repository
#
# See docs/MIGRATION.md for the aarch64 delta (ur_rtde source build, no URSim).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UR7E_ROOT="${UR7E_ROOT:-$(dirname "$REPO_DIR")}"
LEROBOT_DIR="${LEROBOT_DIR:-$UR7E_ROOT/lerobot}"
LEROBOT_REF="${LEROBOT_REF:-v0.6.0}"
ENV_NAME="${ENV_NAME:-ur7e}"
ENV_FILE="$REPO_DIR/envs/ur7e.yaml"

echo "== repo        : $REPO_DIR"
echo "== ur7e root   : $UR7E_ROOT"
echo "== lerobot dir : $LEROBOT_DIR (ref $LEROBOT_REF)"
echo "== conda env   : $ENV_NAME"
echo "== env file    : $ENV_FILE"

# ---------------------------------------------------------------- lerobot core
#
# Guard rail. On the Spark there is a *shared* ~/lerobot that the SO-101,
# OpenArm, smolvla and pi environments all install editable from. Checking that
# clone out to our pinned ref would silently move those environments onto a
# different lerobot. So: refuse to touch a clone that is not already on our ref.
#
if [ -d "$LEROBOT_DIR/.git" ]; then
  current="$(git -C "$LEROBOT_DIR" rev-parse HEAD)"
  wanted="$(git -C "$LEROBOT_DIR" rev-parse "$LEROBOT_REF" 2>/dev/null || echo "")"
  if [ -n "$wanted" ] && [ "$current" != "$wanted" ] && [ "${LEROBOT_FORCE_CHECKOUT:-0}" != "1" ]; then
    cat >&2 <<EOF

ERROR: $LEROBOT_DIR is already a lerobot clone on a different commit.

  current : $current ($(git -C "$LEROBOT_DIR" describe --tags --always))
  wanted  : $wanted ($LEROBOT_REF)

Refusing to check it out. On the Spark the shared ~/lerobot is installed
editable by the so101 / openarm / smolvla / pi environments, and moving it
would change lerobot underneath all of them without warning.

Pick one:
  * give UR7e its own clone (recommended, and what envs/ur7e.yaml assumes):
        UR7E_ROOT=\$HOME/ur7e ./scripts/setup_env.sh
  * point at a different directory:
        LEROBOT_DIR=/path/to/ur7e-only/lerobot ./scripts/setup_env.sh
  * if you are certain this clone is UR7e-only:
        LEROBOT_FORCE_CHECKOUT=1 ./scripts/setup_env.sh

EOF
    exit 1
  fi
else
  echo "== cloning lerobot core"
  mkdir -p "$(dirname "$LEROBOT_DIR")"
  git clone https://github.com/huggingface/lerobot.git "$LEROBOT_DIR"
fi

git -C "$LEROBOT_DIR" fetch --tags --quiet
git -C "$LEROBOT_DIR" checkout --quiet "$LEROBOT_REF"
echo "== lerobot SHA : $(git -C "$LEROBOT_DIR" rev-parse HEAD)"

# ---------------------------------------------------------------- conda env
eval "$(conda shell.bash hook)"
if conda env list | awk '{print $1}' | grep -qx "$ENV_NAME"; then
  echo "== updating existing conda env from $ENV_FILE"
  conda env update -n "$ENV_NAME" -f "$ENV_FILE" --prune
else
  echo "== creating conda env from $ENV_FILE"
  conda env create -n "$ENV_NAME" -f "$ENV_FILE"
fi
conda activate "$ENV_NAME"

# ------------------------------------------------------- editable installs
# Paths are computed, not hard-coded, so the same repo works on both Sparks
# and on the x86 dev box. Third-party dependencies belong in envs/ur7e.yaml.
#
# NOTE: always `python -m pip` -- a bare `pip` under miniforge can resolve to
# the base environment's pip and silently install into base.
#
# ur7e_site goes first: the plugin configs import it at module load time to
# read their defaults out of config/site.yaml.
python -m pip install -e "$REPO_DIR/ur7e_site"

# lerobot core. The extras are here rather than in the yaml because the path
# is machine-dependent; the packages they pull in are documented in the yaml.
python -m pip install -e "${LEROBOT_DIR}[dataset,training,smolvla,dynamixel,hardware,async]"

# ------------------------------------------------------------------ ur_rtde
# There is no aarch64 wheel, so pip builds ur_rtde from source. Two traps, and
# they only bite on aarch64:
#
#   1. Boost must ALREADY be installed. See docs/MIGRATION.md §2.
#
#   2. `cmake` on PATH is the PyPI cmake package's console-script shim -- pulled
#      in because lerobot core depends on `cmake`. It works fine in a normal
#      shell, but pip's build isolation hides the env's site-packages from the
#      build, so inside the isolated build the shim cannot import its own
#      module and dies with:
#           ModuleNotFoundError: No module named 'cmake'
#      which surfaces as: CalledProcessError: ['cmake', '--version'] ... status 1
#      --no-build-isolation lets the build see the env, so the shim resolves.
#
# Installed here, before the plugins, so that pip sees the requirement already
# satisfied when lerobot_robot_ur7e pulls it in.
if python -c "import rtde_control" 2>/dev/null; then
  echo "== ur_rtde already installed"
else
  echo "== installing ur_rtde"
  if ! python -c "import cmake" 2>/dev/null; then
    echo "   WARNING: the 'cmake' python module is not importable in this env." >&2
    echo "   The build will likely fail. Try: python -m pip install 'cmake>=3.29.0.1,<4.2.0'" >&2
  fi
  python -m pip install --no-build-isolation ur_rtde || {
    cat >&2 <<'HINT'

ur_rtde failed to build. In order of likelihood:

  1. Boost is missing. On Debian/Ubuntu:
       sudo apt install -y libboost-system-dev libboost-thread-dev \
                           libboost-program-options-dev cmake build-essential
  2. cmake is unusable from the build. Check BOTH of these:
       cmake --version
       python -c "import cmake; print(cmake.__file__)"
     If the module import fails, reinstall inside lerobot's allowed range:
       python -m pip install "cmake>=3.29.0.1,<4.2.0"
     Do NOT `pip install --force-reinstall cmake` unqualified -- it pulls a
     version above lerobot's `cmake<4.2.0` pin and leaves pip in conflict.
  3. As a last resort, force the system cmake and skip the shim entirely:
       CMAKE_EXECUTABLE=/usr/bin/cmake python -m pip install --no-build-isolation ur_rtde

HINT
    exit 1
  }
fi

# Plugin packages. The lerobot_robot_ / lerobot_teleoperator_ name prefixes are
# what makes register_third_party_plugins() discover them.
#
# lerobot_camera_zmq is deliberately NOT installed: lerobot 0.6.0 ships its own
# "zmq" camera and the in-tree plugin (now registering as "zmq_legacy") is
# superseded by it.
python -m pip install \
  -e "$REPO_DIR/lerobot_robot_ur7e" \
  -e "$REPO_DIR/lerobot_teleoperator_gello" \
  -e "$REPO_DIR/lerobot_teleoperator_keyboard_joint"

# --------------------------------------------------- ffmpeg on the loader path
# torchcodec dlopens libavutil/libavcodec by SONAME. Those ship inside this
# env's lib/, which is NOT on the default loader path, so without this hook the
# decoder fails at import with "libavutil.so.NN: cannot open shared object
# file" -- and only when *reading* a dataset back, because encoding goes
# through PyAV / the ffmpeg binary instead.
mkdir -p "$CONDA_PREFIX/etc/conda/activate.d" "$CONDA_PREFIX/etc/conda/deactivate.d"
cat > "$CONDA_PREFIX/etc/conda/activate.d/zz-ffmpeg-ldpath.sh" <<'HOOK'
export UR7E_SAVED_LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}"
export LD_LIBRARY_PATH="$CONDA_PREFIX/lib:${LD_LIBRARY_PATH:-}"
HOOK
cat > "$CONDA_PREFIX/etc/conda/deactivate.d/zz-ffmpeg-ldpath.sh" <<'HOOK'
export LD_LIBRARY_PATH="${UR7E_SAVED_LD_LIBRARY_PATH:-}"
unset UR7E_SAVED_LD_LIBRARY_PATH
HOOK

echo
echo "== done. verify with:"
echo "     conda activate $ENV_NAME && python scripts/verify_env.py"
