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
x86_64 and win). pip therefore builds from source, which needs Boost -- and hits
a second, much less obvious trap.

```bash
# 1. Boost, FIRST. Without it the source build dies halfway.
sudo apt install -y libboost-system-dev libboost-thread-dev \
                    libboost-program-options-dev cmake build-essential

# 2. Then ur_rtde, WITHOUT build isolation (see below).
python -m pip install --no-build-isolation ur_rtde
```

Verify with `python -c "import rtde_control; print(rtde_control.RTDEControlInterface.FLAG_USE_EXT_UR_CAP)"`.
`scripts/verify_env.py` checks exactly this.

### Why `--no-build-isolation`

Hit on spark-3e31, 2026-08-18. lerobot core depends on the PyPI `cmake`
package, which installs a console-script **shim** at `$CONDA_PREFIX/bin/cmake`
whose real body is a Python module. That directory precedes `/usr/bin` on PATH,
so it shadows the system cmake. In a normal shell the shim works. Inside pip's
build isolation the env's site-packages are hidden from the build, the shim
cannot import its own module, and the build dies:

```text
File ".../envs/ur7e/bin/cmake", line 3, in <module>
    from cmake import cmake
ModuleNotFoundError: No module named 'cmake'
...
subprocess.CalledProcessError: Command '['cmake', '--version']' returned non-zero exit status 1.
ERROR: Failed building wheel for ur-rtde
```

Note the shape of this failure: **`cmake --version` succeeds when you type it by
hand**, so the obvious check does not reproduce it. Only the isolated build sees
the broken shim. `--no-build-isolation` lets the build see the env, so the shim
resolves.

### Do NOT `pip install --force-reinstall cmake`

Unqualified, it pulls a version above lerobot's pin and leaves pip in permanent
conflict:

```text
lerobot 0.6.0 requires cmake<4.2.0,>=3.29.0.1, but you have cmake 4.4.2 which is incompatible.
```

Harmless at runtime -- lerobot never imports cmake, the dependency exists for
building opencv from source, which does not happen on aarch64 (wheel available).
But a later `pip install` may silently pull it back into range at an
inconvenient moment, and the env no longer matches `docs/freeze-x86.txt`.

Stay inside the range instead:

```bash
python -m pip install "cmake>=3.29.0.1,<4.2.0"
pip check   # silent == clean
```

### If it still fails

```bash
# force the system cmake and bypass the shim entirely
CMAKE_EXECUTABLE=/usr/bin/cmake python -m pip install --no-build-isolation ur_rtde

# or move the shim out of the way for the duration of the build
mv "$CONDA_PREFIX/bin/cmake" "$CONDA_PREFIX/bin/cmake.bak"
python -m pip install ur_rtde
mv "$CONDA_PREFIX/bin/cmake.bak" "$CONDA_PREFIX/bin/cmake"
```

`scripts/setup_env.sh` installs ur_rtde this way before the plugins, and prints
this triage if it still fails.

---

## 3. torch / torchcodec on aarch64

**Settled 2026-08-18 on spark-3e31.** These two must move together, and the
pair that works on aarch64 is *not* the pair that works on x86_64.

| | x86_64 (RTX 5080) | aarch64 (GB10) |
| --- | --- | --- |
| torch | `2.10.0+cu130` | `2.11.0+cu130` |
| torchvision | `0.25.0+cu130` | `0.26.0+cu130` |
| torchcodec | `0.10.0` | `0.11.1` |

The forcing constraint: **torchcodec has no aarch64 wheel before 0.11.0**
(verified against the PyPI file list), and 0.11 requires torch >= 2.11. So the
x86 pin cannot be reused. 2.11 is still inside lerobot 0.6.0's `torch<2.12.0`.

`envs/ur7e.yaml` selects per architecture with pip environment markers, so one
file serves both. Nothing to edit by hand.

**The mismatch error does not mention versions.** A torchcodec built for a
different torch fails at *import* with `undefined symbol: torch_dtype_...`,
which reads like a corrupt install. If you see that, check the pairing first.

### GB10 is sm_121 and torch may not ship kernels for it

`torch 2.10+cu130` reports:

```text
UserWarning: Found GPU0 NVIDIA GB10 which is of cuda capability 12.1.
Minimum and Maximum cuda capability supported by this version of PyTorch is (8.0) - (12.0)
built for: sm_80, sm_90, sm_100, sm_110, sm_120, compute_120
```

**This is a note, not a blocker.** `compute_120` PTX forward-compiles to sm_121
(same 12.x family), so the cost is a one-off JIT delay at first kernel launch,
not a failure. Evidence that it works in practice: the **so101 / openarm /
smolvla environments run this same torch 2.10+cu130 on GB10 on the Sparks
today**, and their wiki pages record no such problem.

`scripts/verify_env.py` reports it under NOTES rather than PROBLEMS for exactly
this reason -- an earlier version failed the check here, which would have turned
rung 0 of the ladder RED on every Spark.

Check whether 2.11 ships sm_121 natively once installed:

```bash
python -c "import torch; print(torch.cuda.get_arch_list())"
```

Escalate to the NGC PyTorch container only if you actually observe a hang, not
because of the warning alone.

### RealSense

`pyrealsense2` is pip-installable on x86_64 but **not** on aarch64 at the
version `lerobot[intelrealsense]` pins. Use apt `librealsense2` +
`conda install pyrealsense2`; see the Intel Realsense wiki page.

### A `pip check` warning you can ignore

```text
nvidia-cusparselt-cu13 0.8.0 is not supported on this platform
```

Seen on spark-3e31. `nvidia-cusparselt-cu13` is a declared dependency of
torch's cu130 build (the same package is installed and clean on x86_64), and
cuSPARSELt is only used by `torch.sparse.to_sparse_semi_structured` -- the 2:4
structured-sparsity path, which none of ACT / SmolVLA / the dataset pipeline
touches. It is the only line `pip check` reports; torch and lerobot are clean.

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
run needs a manual play press. With looping the program restarts itself, the
injected script survives, and unattended runs work. On the real cell, weigh
that against wanting an explicit human play press before the arm can be driven.

**Do not press stop or pause in the UI.** That is a different kind of stop: it
returns the program to DRAFT (visible in the logs as
`PATCH /…/program/execution/0/0/DRAFT`) and **discards the injected script**,
so both nodes go yellow and you are back to the handshake above. A
`stopScript()` from LeRobot does not do this. Treat the pendant/browser as a
window to look through, not a control panel to drive.

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
| `RTDEControlInterface` times out | `scripts/ursim_state.py` first: `1:STOPPED` / `4:PAUSED` just needs play. If it says `2:PLAYING` and it still fails, check the node colour in the UI -- a playing program is not proof the injected script is there |
| Program node yellow / "not finished" | `python scripts/ursim_reconnect.py`, then Update program + play while it waits (§4) |
| `lerobot-*` reports an unknown robot type | `scripts/verify_env.py`; a plugin import failed, or pip installed into `base` |
| `observation.state` missing at rollout | motor features must end in `.pos` |
| dataset records but will not load | torchcodec/torch/ffmpeg triangle (§3) |
| teleop rate far below target | `scripts/bench_teleop.py` -- it attributes the loss |
