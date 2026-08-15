#!/usr/bin/env bash
# Bring up URSim (PolyScope X) with the External Control URCapX installed.
#
#   ./scripts/ursim_bringup.sh          # create network + container + URCapX
#   ./scripts/ursim_bringup.sh --reset  # destroy and rebuild from scratch
#
# x86_64 ONLY. URSim's outer image is arm64-capable but the compose stack it
# runs inside (urservice, citadel) is amd64-only and SIGSEGVs under QEMU on
# aarch64 -- see docs/MIGRATION.md. On the DGX Spark the real robot replaces
# the simulator, so this script is simply not used there.
#
# Deliberately does NOT use UR's start_ursim.sh: that wrapper installs its
# SIGINT trap before the URCapX step, so a Ctrl-C while waiting deletes the
# container and its logs, and it does not pass HOST_ARCH.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

URSIM_VERSION="${URSIM_VERSION:-10.12.1}"
URSIM_IMAGE="universalrobots/ursim_polyscopex:${URSIM_VERSION}"
NET_NAME="${NET_NAME:-ursim_net}"
NET_SUBNET="${NET_SUBNET:-192.168.56.0/24}"
ROBOT_IP="${ROBOT_IP:-192.168.56.101}"
ROBOT_TYPE="${ROBOT_TYPE:-UR7e}"          # exact casing matters
CONTAINER="${CONTAINER:-ursim}"
PROGRAMS_DIR="${PROGRAMS_DIR:-$HOME/.ursim/polyscopex/ur7e/programs}"

# NOTE: the release tags on this repo have no leading "v".
URCAPX_VERSION="${URCAPX_VERSION:-1.1.0}"
URCAPX_FILE="external-control-${URCAPX_VERSION}.urcapx"
URCAPX_URL="https://github.com/UniversalRobots/Universal_Robots_ExternalControl_URCapX/releases/download/${URCAPX_VERSION}/${URCAPX_FILE}"
DOWNLOAD_DIR="${DOWNLOAD_DIR:-$HOME/Downloads}"

if [ "$(uname -m)" != "x86_64" ]; then
  echo "ERROR: URSim PolyScope X only runs on x86_64 (see docs/MIGRATION.md)." >&2
  exit 1
fi

if [ "${1:-}" = "--reset" ]; then
  echo "== removing existing container and program store"
  docker rm -f "$CONTAINER" 2>/dev/null || true
  rm -rf "${PROGRAMS_DIR:?}"/*
fi

# ------------------------------------------------------------------- network
if ! docker network inspect "$NET_NAME" >/dev/null 2>&1; then
  echo "== creating docker network $NET_NAME ($NET_SUBNET)"
  docker network create --subnet="$NET_SUBNET" "$NET_NAME"
else
  echo "== docker network $NET_NAME already exists"
fi

# ----------------------------------------------------------------- container
if docker inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "== container $CONTAINER exists; starting it if stopped"
  docker start "$CONTAINER" >/dev/null
else
  echo "== starting $CONTAINER from $URSIM_IMAGE"
  mkdir -p "$PROGRAMS_DIR"
  # --privileged is required: URSim runs its own docker daemon inside.
  # No --rm, so a crashed run leaves its logs behind for inspection.
  docker run -d --name "$CONTAINER" \
    --net "$NET_NAME" --ip "$ROBOT_IP" \
    -e HOST_ARCH=amd64 -e "ROBOT_TYPE=$ROBOT_TYPE" \
    --privileged \
    -v "$PROGRAMS_DIR:/ur/bin/backend/applications" \
    "$URSIM_IMAGE"
fi

echo "== waiting for the web UI on http://$ROBOT_IP ..."
for _ in $(seq 1 120); do
  if curl -sf -o /dev/null "http://$ROBOT_IP/universal-robots/urservice/api/v1/urcaps"; then
    echo "   up."
    break
  fi
  sleep 2
done

# -------------------------------------------------------------------- URCapX
if curl -sf "http://$ROBOT_IP/universal-robots/urservice/api/v1/urcaps" | grep -q '"external-control"'; then
  echo "== External Control URCapX already installed"
else
  echo "== installing External Control URCapX $URCAPX_VERSION"
  mkdir -p "$DOWNLOAD_DIR"
  [ -f "$DOWNLOAD_DIR/$URCAPX_FILE" ] || curl -sSL -o "$DOWNLOAD_DIR/$URCAPX_FILE" "$URCAPX_URL"
  curl -sS -X POST "http://$ROBOT_IP/universal-robots/urservice/api/v1/urcaps" \
    -F "urcapxFile=@$DOWNLOAD_DIR/$URCAPX_FILE" -o /dev/null -w '   HTTP %{http_code}\n'
  # Right after the REST install the URCap sits in state=created and does not
  # appear in the UI. A restart is the reliable way to activate it.
  echo "== restarting $CONTAINER so the URCapX activates"
  docker restart "$CONTAINER" >/dev/null
  sleep 20
fi

cat <<EOF

== URSim is up: http://$ROBOT_IP  (Chrome recommended)

Remaining steps are UI-only. They persist in $PROGRAMS_DIR (host-mounted), so
they survive docker restart and are needed ONCE per program store:

  1. Operator -> power icon -> "Power On" -> "Unlock" (brake release)
  2. Application -> URCaps -> External Control
       Host IP defaults to $(ip -4 route list "$NET_SUBNET" 2>/dev/null | awk '{print $NF}' | head -1) : 50002, which is already correct here.
       It must be THIS machine as the robot sees it, never 127.0.0.1.
  3. Program -> select context "Main Program" (NOT Global Functions)
       -> "+" -> External Control Program
  4. On the Main Program node set Looping: Enabled. Without it the program
       stops when the client calls stopScript(), and every later run needs a
       manual play press.
  5. Start a listener on this machine, then press "Update program" in the
       node: URCapX 1.1.0 fetches the URScript at edit time, NOT at play time.
         python -c "import rtde_control as c; \\
           c.RTDEControlInterface('$ROBOT_IP',500.0,c.RTDEControlInterface.FLAG_USE_EXT_UR_CAP)"
  6. Press play.

Then:  python scripts/smoke_rtde.py
EOF
