# MIGRATION: this x86_64 box -> DGX Spark (aarch64) + real UR7e

- Status: written 2026-08-16 from a fully green URSim run on the RTX 5080 box.
- Audience: whoever (human or Claude Code) sets this up on the Spark on day 1.
- Companion docs: [HANDOFF.md](HANDOFF.md) (why this project exists, what was
  already known) and [../README.md](../README.md) (what the repo contains).

The claim this repo is trying to make good on: **on the Spark, the software
delta is one config file; everything else is host setup.** This document is the
host-setup list, plus the two places where aarch64 genuinely differs.

---

## 0. The software delta, in full

```bash
cp config/site.real.example.yaml config/site.yaml
$EDITOR config/site.yaml     # fill in the two <FILL IN> values
```

| Field | This box (URSim) | Spark + real UR7e |
| --- | --- | --- |
| `robot.ip` | `192.168.56.101` (container) | the controller's static IP |
| `robot.external_control.host_ip` | `192.168.56.1` (docker bridge) | the Spark's wired LAN IP |
| `robot.use_gripper` | `false` | `false` until the ToolComm path exists (§5) |
| `robot.servoj.time_s` | `0.033` (30 fps) | `0.008` (125 fps teleop) |
| `teleop.gello.mock` | `true` (leader is elsewhere) | `false` |
| `teleop.gello.port` | unchanged | **unchanged** -- `by-id` paths follow the FTDI chip |
| `record.dataset_prefix` | `ur7e_ursim` | `ur7e_real` |

Nothing else in the repo mentions an IP, a serial path or a camera index.

---

## 1. Day-1 checklist

1. **Clone and pin.** `git clone <this repo>` then `./scripts/setup_env.sh`.
   It clones lerobot to `~/lerobot`, checks out the pinned ref (default
   `v0.6.0`, see [HANDOFF.md §4](HANDOFF.md)), builds the conda env and installs
   the four in-tree packages editable.
2. **Boost, before ur_rtde** (§2) -- otherwise the pip install of `ur_rtde`
   fails halfway through a source build.
3. **`./scripts/verify_env.py`** -- confirms torch sees the GPU with *native*
   kernels, that the three plugins registered, and which site config is active.
4. **Move the GELLO.** `sudo usermod -aG dialout $USER`, log out and back in.
   The `by-id` path is unchanged. Set `teleop.gello.mock: false`.
5. **Network to the robot.** Wired, static IPs on the same subnet. Teleop over
   Wi-Fi is not acceptable -- servoJ is a hard-real-time stream.
6. **URCapX on the robot** (§4), from USB; there is no REST endpoint to POST to
   on a real controller the way there is on URSim.
7. **`python scripts/smoke_rtde.py --read-only`** first. It exercises only RTDE
   receive, which cannot move the arm, and isolates network problems from
   External Control problems.
8. **`python scripts/smoke_rtde.py`** with the program playing. THE ARM WILL
   MOVE: wrist_3 by 5 degrees and back. Clear the cell, hand on the e-stop.
9. **`./scripts/verify_ladder.sh`** from rung 1 upward, re-recording real data.

---

## 2. ur_rtde: source build on aarch64

There is no aarch64 wheel on PyPI (verified against the PyPI API: only i686,
x86_64 and win). pip will therefore try to build from source, which needs Boost.

```bash
# apt route
sudo apt install -y libboost-system-dev libboost-thread-dev \
                    libboost-program-options-dev cmake build-essential
pip install ur_rtde

# conda route (if you keep everything inside the env)
conda install -c conda-forge libboost-devel cmake
CMAKE_PREFIX_PATH="$CONDA_PREFIX" pip install --no-binary :all: ur_rtde
```

Verify with `python -c "import rtde_control; print(rtde_control.RTDEControlInterface.FLAG_USE_EXT_UR_CAP)"`.
`scripts/verify_env.py` checks exactly this.

---

## 3. torch / torchcodec on aarch64

**torch.** Use a cu130 aarch64 wheel. GB10 is `sm_121`; if the wheel does not
ship `sm_121` kernels it will PTX-JIT at first launch, which is known to hang on
complex graphs. `scripts/verify_env.py` prints `torch.cuda.get_arch_list()` and
flags the mismatch -- do not ignore that warning. Fall back to the NGC PyTorch
container if no wheel has native `sm_121`.

**torchcodec is version-locked to torch, and the error message does not say so.**
This bit us on x86 and will bite harder on aarch64:

- torchcodec ships prebuilt `libtorchcodec_coreN.so` per FFmpeg major (N = 4..8)
  and dlopens the first one that loads. A wrong *torch* version shows up as
  `undefined symbol: torch_dtype_...`; a missing *ffmpeg* shows up as
  `libavutil.so.NN: cannot open shared object file`. Both are reported as
  "Could not load this library", stacked one per FFmpeg major, and the real
  cause is only visible if you read every block.
- Pairing used here: **torch 2.10 -> torchcodec 0.10.0**. (0.11 needs torch
  >= 2.11, 0.12 needs 2.12.) If you move torch, move torchcodec with it.
- aarch64 has no torchcodec wheel before 0.11.0, which needs torch >= 2.11. So
  on the Spark either move the pin to torch 2.11 + torchcodec 0.11, or accept
  the PyAV decoder fallback (`lerobot[av-dep]` is already installed).
- The env's ffmpeg libraries live in `$CONDA_PREFIX/lib`, which is not on the
  loader path. `scripts/setup_env.sh` installs a conda `activate.d` hook that
  prepends it to `LD_LIBRARY_PATH`; without it torchcodec cannot find
  `libavutil.so.*` no matter which version you install.

**Symptom to recognise:** recording works fine (encoding goes through PyAV /
the ffmpeg binary) and then *reading the dataset back* explodes. Encode and
decode take different paths.

---

## 4. Real controller: what URSim could not tell us

| | URSim | Real UR7e |
| --- | --- | --- |
| URCapX install | `POST /universal-robots/urservice/api/v1/urcaps` | USB stick via the pendant |
| Services (Primary/RTDE) | enabled out of the box | **likely need enabling** in Settings -> Security -> Services |
| Admin password | none | set one and record it |
| Payload / TCP / CoG | irrelevant | must be correct or the arm faults or moves badly |
| Safety planes | none | configure before any autonomous motion |
| Tool I/O | absent | present (gripper, §5) |

**The URCapX quirk that costs an hour if you do not know it** (URCapX 1.1.0,
confirmed on 10.12.1): the External Control *program node* does not fetch the
URScript when you press play. It caches it when you press **"Update program"**
inside the node, and it can only do that while a ur_rtde client is already
listening on `<host_ip>:50002`. So the order is:

1. start a listener on the PC:
   `python -c "import rtde_control as c; c.RTDEControlInterface('<ROBOT_IP>',500.0,c.RTDEControlInterface.FLAG_USE_EXT_UR_CAP)"`
2. open the node on the pendant, press **Update program** (node turns valid),
3. press play, and the listener returns.

Editing the program afterwards (even just toggling *Loop Program*) invalidates
the cache and the node goes yellow again -- redo steps 1-3. "Program is not
finished. Complete the yellow program-nodes" is what that looks like.

**Set *Loop Program* on the Main Program node.** `stopScript()` at the end of
every LeRobot session stops the program, and without looping each subsequent
run needs a manual play press. With looping the program restarts itself and
unattended runs work. On the real cell, weigh that against wanting an explicit
human play press before the arm can be driven.

---

## 5. Gripper (still not done, deliberately)

PolyScope X closed the legacy Robotiq socket on `:63352`. `robotiq_gripper.py`
in this repo speaks exactly that protocol, so it cannot work as-is. The
supported route is the **ToolComm Forwarder URCapX**, which bridges the tool
RS-485 line to TCP `:54321`; from there it is Modbus RTU over a socat pipe.

`use_gripper: false` is therefore correct on the real cell too until someone
writes that transport. Note the shape contract: the 7th action/observation
element (`gripper.pos`) exists either way and is pinned to `0.0` when disabled,
so datasets recorded before and after the gripper works stay compatible.

---

## 6. Things that will NOT transfer

- **URSim itself.** The outer image is arm64-capable but the compose stack
  inside it (`urservice`, `citadel`) is amd64-only, and Go binaries SIGSEGV
  under QEMU on aarch64 (`lfstack.push invalid packing` -- Go's tagged pointers
  assume a 48-bit address space, aarch64 gives 256 TB). Not fixable from
  outside. The real robot is the replacement.
- **The recorded URSim dataset.** It has no visual semantics. Re-record on the
  real cell; only the *schema* carries over.
- **Timing numbers.** 124 Hz teleop measured here says the software path is not
  the bottleneck; it says nothing about real-controller jitter.
- **`pyrealsense2`** is pip-installable on x86_64 but not on aarch64: use the
  apt `librealsense` + conda route (internal wiki has the procedure).

---

## 7. Fast triage

| Symptom | First thing to check |
| --- | --- |
| `RTDEReceiveInterface` times out | ping the robot; RTDE service enabled? |
| `RTDEControlInterface` times out | is the program **playing**? then Host IP in the External Control tile, then `use_external_control_urcap`. NOTE: a playing program is not proof the node is valid -- the URScript cache also expires when the controller sits idle. Check the node colour in the UI |
| Program node yellow / "not finished" | `python scripts/ursim_reconnect.py`, then Update program + play while it waits (§4) |
| `lerobot-*` reports an unknown robot type | `scripts/verify_env.py`; a plugin import failed, or pip installed into `base` |
| `observation.state` missing at rollout | motor features must end in `.pos` |
| dataset records but will not load | torchcodec/torch/ffmpeg triangle (§3) |
| teleop rate far below target | `scripts/bench_teleop.py` -- it attributes the loss |
