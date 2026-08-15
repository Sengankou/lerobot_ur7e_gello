# LeRobot × UR7e × GELLO (PolarisAI fork)

![Thumbnail](./assets/images/banner.png)

A [LeRobot](https://github.com/huggingface/lerobot) integration for **Universal
Robots e-Series** arms with a **GELLO** leader arm: teleoperate, record
datasets, train policies, roll them out.

This is PolarisAI's private import of
[F-Fer/lerobot_ur5e_gello](https://github.com/F-Fer/lerobot_ur5e_gello),
retargeted at the **UR7e on PolyScope X** and at LeRobot **v0.6.0**. UR7e is a
mechanical rebrand of the UR5e, so the `ur5e` plugin name is kept; the
controller generation is what changed, and PolyScope X breaks several
assumptions that older UR integrations rely on.

**Verified end to end against URSim (PolyScope X 10.12.1) on an RTX 5080 box.**
See [docs/HANDOFF.md](docs/HANDOFF.md) for the project background and
[docs/MIGRATION.md](docs/MIGRATION.md) for moving to the DGX Spark and the real
robot.

---

## What is different from upstream

| | upstream | here |
| --- | --- | --- |
| Controller | PolyScope 5 | **PolyScope X** -- `FLAG_USE_EXT_UR_CAP`, no Dashboard :29999, no Robotiq :63352 |
| lerobot | `>=0.4.0`, forked | **pinned v0.6.0**, unmodified upstream |
| Config | IPs and device paths in code | **one `config/site.yaml`** -- sim/real switch is a file edit |
| Gripper | always on | opt-in; 7-wide action/observation kept either way |
| GELLO absent | no path | **simulated bus** exercising the real calibration code |
| Keyboard jog | none | `keyboard_joint` teleoperator, seeded from the arm's real pose |
| Feature names | `joint_0` | **`joint_0.pos`** -- required by lerobot >= 0.6 rollout |

---

## Architecture

```text
        this machine                                        controller
 ┌──────────────────────────────┐                    ┌─────────────────────┐
 │ lerobot-teleoperate/record/  │                    │  URSim (PolyScope X)│
 │ rollout   (unmodified CLI)   │                    │  or a real UR7e     │
 │            │                 │   RTDE :30004      │                     │
 │   ┌────────▼─────────┐  read state ──────────────►│  urcontrol @ 500 Hz │
 │   │  UR5E  (Robot)   │                            │                     │
 │   │  lerobot_robot_  │  servoJ  ◄──────────────── │  External Control   │
 │   │  ur5e            │   :50002 (robot dials out) │  URCapX (must be    │
 │   └────────▲─────────┘                            │  PLAYING)           │
 │            │ 7-wide action dict                   └─────────────────────┘
 │   ┌────────┴─────────┐
 │   │ Gello  (Teleop)  │──► DynamixelMotorsBus ──► U2D2 ──► GELLO leader
 │   │  or KeyboardJoint│    or MockDynamixelBus (no hardware)
 │   └──────────────────┘
 │            ▲
 │   config/site.yaml ── ur7e_site ──► every default above
 └──────────────────────────────┘
```

Two directions, and the second one surprises people: **RTDE is PC -> robot, but
External Control is robot -> PC.** The robot dials back to `host_ip:50002`,
which must be this machine's address *as the robot sees it* -- never a loopback
address.

Plugins are discovered by name: LeRobot's `register_third_party_plugins()`
imports every installed distribution starting with `lerobot_robot_`,
`lerobot_teleoperator_` or `lerobot_camera_`. That is why the stock
`lerobot-record` / `lerobot-train` / `lerobot-rollout` commands work unmodified.

---

## Install

```bash
./scripts/setup_env.sh          # conda env "ur7e" + lerobot v0.6.0 + plugins
conda activate ur7e
python scripts/verify_env.py    # GPU, plugin registration, site config
```

Pins and their reasons are documented in [envs/ur7e.yaml](envs/ur7e.yaml);
exact resolved versions are in [docs/freeze-x86.txt](docs/freeze-x86.txt).

### Simulator (x86_64 only)

```bash
./scripts/ursim_bringup.sh      # docker network + URSim + External Control URCapX
```

then finish the pendant steps it prints (power on, brake release, place the
External Control node, **Update program**, play). URSim does not run on aarch64
-- see [docs/MIGRATION.md §6](docs/MIGRATION.md).

---

## Use

```bash
# prove both RTDE directions (the arm moves 5 degrees and back)
python scripts/smoke_rtde.py

# teleoperate -- no IP on the command line, it comes from config/site.yaml
lerobot-teleoperate --robot.type=ur5e --teleop.type=gello --fps=30

# jog joints from the keyboard instead (q/a w/s e/d r/f t/g y/h, space = home)
lerobot-teleoperate --robot.type=ur5e --teleop.type=keyboard_joint --fps=30

# record / train / roll out
lerobot-record  --robot.type=ur5e --teleop.type=gello \
                --dataset.repo_id=<user>/<name> --dataset.single_task="..." \
                --dataset.push_to_hub=false
lerobot-train   --dataset.repo_id=<user>/<name> --policy.type=act --policy.device=cuda
lerobot-rollout --robot.type=ur5e --policy.path=<ckpt>/pretrained_model --duration=30

# the whole ladder, with pass/fail per rung
./scripts/verify_ladder.sh
```

> `--dataset.single_task` must not contain a colon: draccus parses `a: b` as a
> mapping and the task silently becomes a dict.

---

## Repository layout

```
config/site.yaml              THE file to edit when changing machine or robot
config/site.real.example.yaml real-UR7e template
ur7e_site/                    loader for the above; never raises, so --help works
lerobot_robot_ur5e/           Robot plugin (RTDE, servoJ, External Control)
lerobot_teleoperator_gello/   GELLO leader + simulated Dynamixel bus
lerobot_teleoperator_keyboard_joint/  keyboard joint jog, same action schema
lerobot_camera_zmq/           SUPERSEDED by lerobot's built-in "zmq" camera
calibration/                  GELLO calibration, committed
envs/ur7e.yaml                conda environment
scripts/                      setup, bring-up, smoke tests, benchmarks, ladder
docs/HANDOFF.md               why this project exists; confirmed technical facts
docs/MIGRATION.md             x86 -> DGX Spark / URSim -> real robot
docs/freeze-x86.txt           pip freeze of the green environment
```

`openpi_client/` and `pi_streamer/` are inherited from upstream and are not part
of the verified path.

## License

Apache License 2.0 — see [LICENSE](LICENSE).
