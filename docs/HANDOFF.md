# HANDOFF: UR7e × LeRobot × GELLO 一気通貫セットアップ（RTX 5080 機）

- 作成: 2026-08-03 / 更新: 2026-08-16（作業リポジトリ確定に伴い §0.5 を追加、§4・§5・§8 を改訂）/ Claude (claude.ai, Fable 5)
- **2026-08-16 追記: RTX 5080 機での Phase A〜D をすべて実施し、Phase C の 1〜5 が green になった。実測値・新たに踏んだ罠・確定した設計は §12 に集約した。§12 と本文が食い違う場合は §12 を正とする。**
- 宛先: RTX 5080 Ubuntu PC 上の Claude Code（このプロジェクトのコンテキストを一切持っていない前提で書く）
- 依頼者: Koya（PolarisAI）
- 置き場所: 本ファイルは作業リポジトリの `docs/HANDOFF.md` として git 管理する（実測結果を反映したら更新してコミットする）

**表記の凡例**: ✅実測 = 本日までに実機・実環境で確認済み / 📚一次 = 公式ドキュメント・公式リポジトリ・コード精読で確認済み / ⚠️未検証 = もっともらしいが確認していない / 💭推論 = 状況からの推測。**この資料と矛盾する古い記録（vault の AI Handoff 2026-08-03 エントリ等）が見つかった場合、本資料を正とする**（後述の「訂正履歴」参照）。

---

## 0. ミッション

この PC（RTX 5080, x86_64, Ubuntu）上に、**UR7e を LeRobot でテレオペ→データ収録→学習→ロールアウトする一連のスタックを、UR 実機なしで（URSim = UR 公式シミュレータを相手に）完成させる**。

ゴールは 2 つある。

1. **機能検証**: LeRobot の BYOH（自前 Robot/Teleoperator 実装）で `lerobot-teleoperate` → `lerobot-record` → `lerobot-train`（短時間）→ ロールアウト、が URSim 相手に一通り回ること
2. **可搬性**: UR7e 実機が使える日が来たら、このセットアップを**そのまま DGX Spark（aarch64）に持ち込み、その日のうちに実機作業を始められる**こと。x86→aarch64 の差分は既知（後述 §6）なので、最初から差分を設計に織り込むこと

この PC は最終環境ではない。**「ロジックの完成」と「移行パッケージの整備」が成果物**である。

## 0.5 作業リポジトリと初回タスク（まずこれを実行）

**このファイルが置かれているリポジトリが作業の本体**である。素性は以下。

- 実体: F-Fer/lerobot_ur5e_gello の **private import**（GitHub の Fork ボタンは不使用 — public リポジトリの fork は private 化できないため、履歴だけ引き継いだスタンドアロンとして作成済み）
- `origin` = Koya 個人アカウントの private リポジトリ（較正 JSON・社内 IP をコミットするため private を維持。後日、会社 org へ Transfer ownership で移管予定。URL が変わったら `git remote set-url origin` で追随）
- `upstream` = https://github.com/F-Fer/lerobot_ur5e_gello.git（読み取り専用。**push しない**。上流の更新は `git fetch upstream` → `git log main..upstream/main --oneline` で差分確認 → 必要なコミットのみ merge / cherry-pick）
- ローカル想定パス: `~/sandbox/lerobot_ur7e_gello`（異なる場合は読み替え。`pwd` と `git remote -v` で現物を確認すること）

### 初回タスク（この順で）

1. `git remote -v` で origin / upstream の向きを確認する
2. リポジトリ構成（`lerobot_robot_ur5e/` ほか 3 プラグイン、`scripts/`）と本ファイル全体、特に §3・§4・§9 を読む
3. `.gitignore` を確認・整備する（データセット・動画・W&B / HF トークン・`.env` は除外。`calibration/*.json`・`envs/`・`docs/`・`scripts/` はコミット対象）
4. lerobot 本体を `~/lerobot` に clone し、**その時点の main の SHA に固定**する（editable install 自体は Phase B で）。採用 SHA を本ファイル §4 の記録欄に追記してコミットする
5. Phase A（§5）から着手する。各 Phase 完了時に green / red と根拠を報告する
6. **vault 訂正**: この機体に Obsidian vault が同期されている場合、`10_context/AI Handoff.md` の 2026-08-03 エントリ直下に「訂正: URSim の arm64 対応・ur_rtde の aarch64 wheel の 2 点は誤り（詳細: 本リポジトリ docs/HANDOFF.md §10）」と追記する。vault が無ければスキップし、その旨だけ報告する

### 運用ルール

- 改修は feature ブランチ（例: `feat/polyscopex-support`）で行い、動作確認後に main へマージする
- ⚠️未検証マークの項目は、実測でき次第**本ファイルを直接更新**（✅実測 + 日付に書き換え）してコミットする。本ファイルは生きたドキュメントとして扱う
- GELLO とカメラは物理制約（GELLO は spark-dadd に接続中）。ハード移設まで Phase C-1（connect）までを先行し、テレオペ以降は移設後に行う

## 1. プロジェクト背景（最小限）

- PolarisAI は Ryoden 社向けに UR7e + GELLO + VLA のデモを構築中。UR7e 実機は到着済みだが**架台待ちで稼働できない**。そこで実機なしで進められる範囲を最大化する
- **UR7e は UR5e の機械的に同一なリブランド機**（2025-05 発表、UR CPO 発言）📚一次。UR5e 向け資産（GELLO、OSS 実装、π0 の事前学習分布）はそのまま流用できる
- UR7e は新世代 OS **PolyScope X** を搭載（制御箱 CB5.6 は現物確認済み ✅）。世間の UR 情報の大半は旧世代 PolyScope 5 前提であり、**そのままでは通用しない**。差分は §3 に集約した
- GELLO（リーダーアーム）は組立・較正済み。ただし現物は **spark-dadd（別の DGX Spark）に接続中**。この PC でテレオペするには U2D2 ごと物理的に持ってくる必要がある（§5-B 参照）
- 学習・推論の本番機は DGX Spark（GB10, aarch64, CUDA 13）。x86 は本番選択肢にない（会社の機材制約）。**だが URSim が aarch64 で動かないことが判明した**（§3）ため、シミュレータ検証だけこの x86 PC で行う、という役割分担になった

## 2. 登場人物と通信経路

```
[この PC (x86_64)]
  ├─ URSim コンテナ (PolyScope X 10.12.1)   ← ロボットの分身。192.168.56.101
  │    └─ 内部で docker compose が動く（--privileged 必須）
  ├─ conda env "ur7e"
  │    ├─ lerobot 本体（editable）
  │    ├─ lerobot_robot_ur5e / lerobot_teleoperator_gello（editable, 社内fork）
  │    └─ ur_rtde（x86_64 は wheel あり）
  ├─ GELLO（U2D2 経由 USB。物理移設後）
  └─ カメラ（RealSense or Web カメラ。配管検証用）

通信（覚えるべきは「逆向き 2 本」）:
  PC → ロボット  192.168.56.101:30004  RTDE（状態を最大500Hzで読む）📚一次
  ロボット → PC  192.168.56.1:50002    External Control（指令を取りに来る）📚一次
  UI            http://192.168.56.101  PolyScope X 画面（Chrome推奨）
```

ポート一覧: `:30001` Primary / `:30004` RTDE / `:50002` External Control（逆方向・既定値）/ `:80` Web UI / `:54321` ToolComm Forwarder（**実機のみ**）/ `:63352` 旧 Robotiq ソケット（**PolyScope X では死んでいる**）。

## 3. 確定済みの技術事実（重要度順）

1. **ur_rtde は PolyScope X では素通しで繋がらない** 📚一次。ロボット側に **External Control URCapX**（要 PolyScope 10.8.0+）を入れ、PC 側は `RTDEControlInterface(ip, 500.0, RTDEControlInterface.FLAG_USE_EXT_UR_CAP)` で接続し、**ペンダントで ▶ 再生中のみ**制御が成立する。URCapX は Application ノード（Host IP/Port 入力）と Program ノード（ツリーに置く）の 2 部構成 ✅実測
2. **URSim は DGX Spark（aarch64）では動かない** ✅実測。外側イメージは arm64 マルチアーキだが、**内側の compose スタック（urservice, citadel）が amd64 専用**。QEMU 上で Go ランタイムが `taggedPointerPack` / `lfstack.push invalid packing` で SIGSEGV する（aarch64 の 256TB アドレス空間が Go の x86-64 前提 48bit を超えるため。binfmt/QEMU 更新でも直らないことを確認済み）。**x86_64 ではネイティブに動く** — この PC を使う理由がこれ
3. **Robotiq の旧経路 :63352 は PolyScope X で閉じている** 📚一次（UR フォーラムで確認）。実機では ToolComm Forwarder URCapX（RS-485⇄TCP:54321）+ socat + Modbus RTU 直叩きに迂回する。**URSim にツール I/O は無いので、グリッパは本フェーズのスコープ外**
4. **Dashboard Server :29999 は PolyScope X で廃止** 📚一次。後継は Robot API（10.11+）。旧世代向けコードの Dashboard 依存は削る
5. **ur_rtde の aarch64 wheel は PyPI に存在しない** ✅実測（PyPI API で配布物一覧を確認: i686/x86_64/win のみ）。Spark ではソースビルド（Boost 必要）。**x86_64 のこの PC では wheel が入るので何もしなくてよい**
6. **lerobot はプラグイン自動登録機構を持つ** 📚一次（0.5.1 の wheel を精読）。`register_third_party_plugins()` が、インストール済みパッケージのうち `lerobot_robot_` / `lerobot_teleoperator_` / `lerobot_camera_` / `lerobot_policy_` で始まる名前を自動 import する。標準 CLI（`lerobot-record` / `lerobot-train` / `lerobot-replay` / `lerobot-calibrate` / async の robot_client）はこれを呼ぶ。**つまり F-Fer 自作スクリプトは不要で、標準 CLI がそのまま使える**
7. **URSim は初期状態で RTDE / Primary が有効** ✅実測（10.12.1）。Security 画面（Admin パスワード保護）に触る必要はない。**実機では有効化が必要になる見込み** 💭推論
8. **URSim の起動は手動 docker run を推奨** ✅実測。UR 公式ラッパ `start_ursim.sh` は (a) trap が URCapX インストール前に仕掛けられており、待機中に Ctrl-C するとコンテナごと消える、(b) `HOST_ARCH` を渡さない、という罠がある。手動 run + REST での URCapX 投入が確実

## 4. ベース実装の選定と必要な改修

### ベース: F-Fer/lerobot_ur5e_gello 📚一次（コード精読済み）

UR5e + Robotiq + GELLO + LeRobot + π0 という、本プロジェクトと同一構成の唯一の公開実装。`lerobot_robot_ur5e/`（Robot）、`lerobot_teleoperator_gello/`（Teleoperator）、`lerobot_camera_zmq/`（Camera）の 3 プラグインパッケージ構成で、上記 6 の命名規約に合致している。**本リポジトリはこの実装の private import であり（§0.5）、改修はここに積む**。

補助参照: `scy-v/lerobot_ur5e_isoteleop`（UR7e 明記、servoJ/servoL/forceMode 切替、master-slave 角度一致チェックあり）。

### 必要な改修（5 点）📚一次（main ブランチ精読、20260803 時点）

| # | 箇所 | 現状 | 改修 |
| --- | --- | --- | --- |
| 1 | `ur5e.py` `connect()` | `RTDEControlInterface(self.robot_ip)` | `FLAG_USE_EXT_UR_CAP` を第3引数に追加 |
| 2 | 同上 | `self.gripper.connect(self.robot_ip, 63352)` ベタ書き | Config に `use_gripper: bool = False` を追加して分岐 |
| 3 | `get_observation()` / `send_action()` | グリッパ読み書きが無条件 | 同フラグで分岐。**action/observation の次元は 7 を維持**し、無効時 `gripper=0.0`（実機接続時に dataset shape が変わらないようにするため） |
| 4 | `config_ur5e.py` | カメラ既定が ZED×4（ZMQ, `192.168.1.12` 固定） | この PC の実カメラ（RealSense / OpenCV）に差し替え。無ければ空 dict |
| 5 | 依存 | `lerobot>=0.4.0`、かつ本体を fork（`fix/video-batch-encoding`）にピン | 使用する lerobot バージョンで API 追随を確認（後述） |

### GELLO 較正の引き継ぎ ⚠️重要

- 社内較正値（gello_software 形式, spark-dadd 上, 2026-07-23 実測）: オフセット `[4,5,3,2,2,3]×π/2` rad、`joint_signs = 1 1 -1 1 1 1`、グリッパ open 16.24° / close -25.56°、U2D2 パス `/dev/serial/by-id/usb-FTDI_USB__-__Serial_Converter_FTAU58XG-if00-port0`（by-id は個体固有なので**この PC に移設しても同一パス**）
- **数値を直接移植しないこと**。F-Fer は `GelloCalibration.joint_offsets` を**モータカウント**で保持し、`angle_rad = sign * (raw - offset) * RAD_PER_COUNT + ref_pos_rad` という別の正規化式を使う 📚一次。**F-Fer 付属の `scripts/calibrate_gello_teleop.py` で取り直す**のが正
- 既存値は妥当性チェックに使う: `RAD_PER_COUNT = 2π/4096` より `k×π/2 rad ↔ k×1024 counts`。取り直した offsets が `[4,5,3,2,2,3]×1024` の近傍（±モータ取付誤差）に来なければ何かがおかしい。`joint_signs` はそのまま流用可

### lerobot 本体のバージョン方針（要決定・推奨あり）

- 会社の Spark 環境は conda env で `lerobot==0.6.0` を運用中（SO-101/OpenArm）。Koya 個人は最新機能志向で editable clone も併用（`-e ~/lerobot[...]`）
- **推奨**: この PC でも **editable clone + コミット固定**。`git clone https://github.com/huggingface/lerobot ~/lerobot && cd ~/lerobot && git checkout <commit>` とし、**採用 SHA を下の記録欄に追記**。Spark 移行時に同じコミットへ checkout する。dataset フォーマット（v3.0）と Robot API の互換性を機体間で揃えるのが目的
- **採用 lerobot コミット（記録欄）**: `v0.6.0 = 30da8e687a6dfc617fcd94afc367ac7071c376ce`（2026-07-06）✅実測 2026-08-16。会社 Spark 環境の `lerobot==0.6.0` と揃えるためタグに固定した。`register_third_party_plugins()` の存在も確認済み（対象 prefix は `lerobot_robot_` / `lerobot_camera_` / `lerobot_teleoperator_` / `lerobot_policy_` / `lerobot_env_` の 5 種に増えている）
- 自動登録機構（上記 6）は 0.5.1 で確認済み。採用コミットでの存在を最初に確認すること（`grep -r register_third_party_plugins`）
- F-Fer が本体を fork にピンしている理由は動画バッチエンコーディングのバグ 💭推論。採用コミットで `lerobot-record` の動画書き出しが壊れたらここを疑う

## 5. 実施フェーズ

### Phase A: URSim 起動と External Control 疎通

```bash
# 1) ネットワークとコンテナ（Linux では bridge がホストから直接ルーティング可能）
docker network create --subnet=192.168.56.0/24 ursim_net
docker run -d --name ursim \
  --net ursim_net --ip 192.168.56.101 \
  -e HOST_ARCH=amd64 -e ROBOT_TYPE=UR7e \
  --privileged \
  -v $HOME/.ursim/polyscopex/ur7e/programs:/ur/bin/backend/applications \
  universalrobots/ursim_polyscopex:10.12.1
docker logs -f ursim    # 内側 compose の起動を眺める。urservice-1 が上がれば OK
```

- `ROBOT_TYPE=UR7e` は大文字小文字までこの表記 📚一次（start_ursim.sh の変換ロジックより）
- `--rm` は付けない（事故時にログを残す）。停止 `docker stop ursim` / 破棄 `docker rm -f ursim`

```bash
# 2) External Control URCapX の投入（REST API）
cd ~/Downloads
wget https://github.com/UniversalRobots/Universal_Robots_ExternalControl_URCapX/releases/download/v1.1.0/external-control-1.1.0.urcapx
curl -X POST http://192.168.56.101/universal-robots/urservice/api/v1/urcaps \
  -F urcapxFile=@external-control-1.1.0.urcapx
docker restart ursim    # インストール直後は state=created。再起動で確実に反映 ✅実測
```

3) ブラウザ（Chrome）で `http://192.168.56.101`:
- 初回ウィザードを完了（パスワードを設定した場合は必ず記録）
- 電源 ON → ブレーキ解除（画面操作のみ・安全）
- **Application** グリッド → **External Control** タイル → Host IP `192.168.56.1` / Port `50002` → Confirm
- **Program** → コンテキストが **Main Program** であること（Global Functions ではない ✅実測ハマり）→ `+` → URCaps → External Control ノードを配置 → 保存
- UI に URCapX が出ない場合: ブラウザ強制リロード（Ctrl+Shift+R）→ ダメなら `docker restart ursim` ✅実測

4) 疎通スモークテスト（venv/conda どちらでも。`ROBOT_IP="192.168.56.101"`）:

```python
import numpy as np
from rtde_receive import RTDEReceiveInterface
from rtde_control import RTDEControlInterface

ROBOT_IP = "192.168.56.101"
rtde_r = RTDEReceiveInterface(ROBOT_IP)          # ▶ 前でも通る
print("q:", np.round(rtde_r.getActualQ(), 3))

rtde_c = RTDEControlInterface(ROBOT_IP, 500.0,
                              RTDEControlInterface.FLAG_USE_EXT_UR_CAP)  # ▶ 再生中のみ成立
q = rtde_r.getActualQ(); q2 = list(q); q2[5] += np.deg2rad(5)
rtde_c.moveJ(q2, 0.5, 0.5); rtde_c.moveJ(list(q), 0.5, 0.5)
dt = 1/125
for _ in range(int(3/dt)):
    t0 = rtde_c.initPeriod()
    rtde_c.servoJ(list(q), 0.5, 0.5, dt, 0.1, 300)
    rtde_c.waitPeriod(t0)
rtde_c.servoStop(); rtde_c.stopScript(); print("OK")
```

実行順: スクリプト起動 → ペンダントで ▶。RTDEReceive だけ通って Control で固まる場合は「▶ 未再生 / Host IP 間違い / フラグ忘れ」のどれか ✅実測。

### Phase B: 環境構築

```yaml
# envs/ur7e.yaml
name: ur7e
channels: [conda-forge]
dependencies:
  - python=3.12
  - ffmpeg=7.1.1        # 8.x 非対応（社内実績値）
  - ipython
  - pip
  - pip:
      - --extra-index-url https://download.pytorch.org/whl/cu130
      - "-e /home/<user>/lerobot[smolvla,training]"   # コミット固定した clone
      - torch==2.10.0+cu130
      - torchvision==0.25.0+cu130
```

- RTX 5080 は sm_120。cu130 wheel は sm_120 のカーネルを含む 📚一次（Spark の GB10=sm_121 で問題になる PTX JIT ハングは**この PC では起きない**）
- プラグインは env 作成後に editable で:

```bash
conda activate ur7e
cd ~/sandbox/lerobot_ur7e_gello      # 本リポジトリのルート（clone 済み・§0.5）
pip install -e ./lerobot_robot_ur5e -e ./lerobot_teleoperator_gello
# x86_64 なので ur_rtde は wheel で入る（依存として自動解決）✅
```

- 登録確認:

```bash
python - <<'EOF'
import logging; logging.basicConfig(level=logging.INFO)
from lerobot.utils.import_utils import register_third_party_plugins
register_third_party_plugins()
from lerobot.robots import RobotConfig
print(sorted(RobotConfig.get_known_choices()))   # 'ur5e' が居れば成功
EOF
```

- **GELLO ハードウェア**: spark-dadd から U2D2 ごと移設。`sudo usermod -aG dialout $USER` + 再ログイン。by-id パスは移設先でも同一。移設まではキーボード teleop（lerobot 標準）で配管検証を先行してよい
- **カメラ**: RealSense があれば pyrealsense2（x86_64 は pip wheel あり ⚠️未検証）、無ければ OpenCV Web カメラで代用。URSim に視覚は無いので**配管検証用**（何を映しても良い）

### Phase C: 検証ラダー（下から順に。各段で shape と fps を確認）

1. **connect** — `Robot.connect()` / `get_observation()`。`joint_0..5` が URSim の姿勢と一致
2. **teleoperate** — `lerobot-teleoperate --robot.type=ur5e --robot.ip=192.168.56.101 --teleop.type=gello ...`（フィールド名は改修後の Config を正とする）。まず 30fps、次に 125fps で `send_action` が間に合うか
3. **record** — 数エピソード収録し **finalize まで通す**（v3.0 は finalize しないとロード不能 📚一次）。`LeRobotDataset` で読み戻して shape / fps / 動画を確認
4. **train** — SmolVLA を少数エピソード・短時間（学習ループが閉じることの確認。**精度は無意味** — URSim データに視覚的意味が無いため）
5. **rollout** — 学習チェックポイントで `policy → send_action → URSim が動く`。標準の eval/replay 経路と、async inference（policy_server / robot_client）+ RTC の両方
6. **（任意）切り分け用** — 詰まったら gello_software（ZMQ 系, LeRobot 非依存）で「Dynamixel→servoJ」だけを直接確認する手がある。その場合 `gello/robots/ur.py` に FLAG_USE_EXT_UR_CAP、`launch_nodes.py` に `no_gripper=True` の 2 パッチが必要 📚一次

### Phase D: 移行パッケージ化（このフェーズが本 PC の最終成果物）

- **設定の一元化**: `ROBOT_IP` / External Control の Host IP / カメラ構成 / fps / デバイスパスを 1 つの設定ファイル（.env or yaml）に集約し、コードにベタ書きしない。**URSim→実機の切替が「設定ファイルの書き換えのみ」になること**
- **リポジトリ**: 本リポジトリに `envs/ur7e.yaml`, `scripts/`（smoke test, bringup, 較正）, `calibration/*.json`（較正結果をコミット）, `docs/MIGRATION.md` を含める
- **バージョン凍結**: green になった時点で `pip freeze > docs/freeze-x86.txt`、lerobot のコミット SHA を記録
- **MIGRATION.md**: §6 の差分表 + Spark での day-1 チェックリストを書く

## 6. Spark（aarch64）移行時の差分 — 最初から織り込むこと

| 項目 | この PC（x86_64） | DGX Spark（aarch64, GB10） |
| --- | --- | --- |
| URSim | ネイティブ動作 ✅ | **動かない** ✅（実機がシミュレータの代わりになるので不要） |
| ur_rtde | pip wheel ✅ | **ソースビルド必須** ✅。Boost 要（apt: `libboost-{system,thread,program-options}-dev` / conda: `libboost-devel` + `CMAKE_PREFIX_PATH=$CONDA_PREFIX` ⚠️未検証） |
| torch | cu130 (sm_120 ネイティブ) | cu130 aarch64 wheel。**sm_121 の PTX JIT ハング既知** → 複雑推論で詰まったら NGC PyTorch コンテナへ 📚一次 |
| torchcodec | wheel あり | aarch64 未提供 → pyav フォールバック（動画書出しの癖に注意）📚一次 |
| pyrealsense2 | pip wheel ⚠️ | pip 不可 → apt(librealsense) + conda で解決済み（社内 Wiki に手順あり）✅ |
| 接続先 | URSim 192.168.56.101 | **実機 UR7e の IP**（有線直結・静的 IP。テレオペは有線必須） |
| External Control Host IP | 192.168.56.1 | **Spark の LAN IP** |
| 実機のみの追加作業 | — | URCapX 2 種を USB でインストール（External Control + ToolComm Forwarder）/ Services 有効化・Remote Control 💭 / Admin パスワード記録 / TCP・ペイロード・安全設定 / グリッパ経路（:54321 + socat + Modbus）/ 低速から |

**Spark day-1 チェックリスト（MIGRATION.md の骨子）**: ①conda env 再構築（yaml + Boost）→ ②fork clone + editable + lerobot 同一コミット → ③ur_rtde ソースビルド確認 → ④GELLO 移設（by-id 同一・dialout）→ ⑤実機ネットワーク疎通（RTDEReceive）→ ⑥URCapX 導入・▶ → smoke test の `ROBOT_IP` を差し替えて Phase C を上から再走。

## 7. スコープ外（URSim では原理的に確認できない）

グリッパ全般（ツール I/O が無い）/ カメラ映像の意味（視覚が無い）/ 制御周期のジッタ・実時間性 / 安全設定・物理干渉・TCP/ペイロード。**URSim で収録したデータは配管検証用であり、学習的価値はゼロ。実機で録り直す前提**。

## 8. 成功条件（Definition of Done）

1. Phase C の 1〜5 が URSim 相手にすべて green
2. URSim→実機の切替が設定ファイル書き換えのみで済む構造になっている
3. 本リポジトリに環境定義・較正・freeze・MIGRATION.md がコミットされ、**Spark 側の Claude Code がこのリポジトリを clone するだけで作業を再開できる**

## 9. 既知の罠クイックリファレンス（すべて今回実際に踏んだもの ✅）

- External Control の Host IP に `127.0.0.1`/`localhost` → コンテナ自身を指し永久に繋がらない。正解は `192.168.56.1`
- Program ノードを **Global Functions** の中で追加しようとして灰色 → コンテキストを Main Program に切り替える
- URCapX を REST で入れた直後は UI に出ない → 強制リロード or `docker restart ursim`
- `start_ursim.sh` 待機中の Ctrl-C → trap でコンテナごと消える（ログも消える）
- conda + `uv venv` 併用時、素の `pip` は miniforge の pip を拾い **base 環境に入る** → `uv pip` を使うか conda env に統一
- `RTDEControlInterface` が timeout → 「▶ 未再生 / Host IP / フラグ忘れ」の三択をこの順で疑う

## 10. 訂正履歴（古い記録を信用しないこと）

- ~~「URSim PolyScope X は arm64 ネイティブ Docker イメージがあり Spark で動く」~~ → **誤り**。外殻のみ arm64、内側 amd64 で Spark では動かない ✅
- ~~「ur_rtde は PyPI に aarch64 wheel あり」~~ → **誤り**。存在しない。Spark はソースビルド ✅
- vault の `10_context/AI Handoff.md` 2026-08-03 エントリにはこの誤りが含まれる。本資料が正
- vault への訂正追記は §0.5 初回タスク 6 として Claude Code に委任（claude.ai 側の obsidian コネクタ不調により未実施）

## 11. 参照

- URSim (PolyScope X): https://hub.docker.com/r/universalrobots/ursim_polyscopex
- External Control URCapX: https://github.com/UniversalRobots/Universal_Robots_ExternalControl_URCapX
- ToolComm Forwarder URCapX: https://github.com/UniversalRobots/Universal_Robots_ToolComm_Forwarder_URCapX
- ur_rtde: https://sdurobotics.gitlab.io/ur_rtde/
- ベース実装: https://github.com/F-Fer/lerobot_ur5e_gello / 補助: https://github.com/scy-v/lerobot_ur5e_isoteleop
- gello_software（較正値の出所）: https://github.com/wuphilipp/gello_software
- 社内 Notion Wiki: 「GELLO Setup手順」（較正値・U2D2 パス）/「Intel Realsense セットアップ手順」（Spark 側カメラ）/「URSim (PolyScope X) セットアップ手順」（本セッションで作成、Windows 版手順とトラブルシューティング）

---

## 12. 実測結果と確定事項（2026-08-16 / RTX 5080 機 / Claude Code）

**この節が最新の正**。§1〜§11 は着手前の想定であり、食い違う箇所は本節を採る。

### 12.1 Definition of Done の達成状況

| DoD | 結果 |
| --- | --- |
| ① Phase C 1〜5 が URSim 相手に green | ✅ 全段 green（12.3） |
| ② URSim→実機の切替が設定ファイル書き換えのみ | ✅ `config/site.yaml` 1 ファイル。コード側に IP・デバイスパス・カメラ番号のベタ書きは残っていない |
| ③ 環境定義・較正・freeze・MIGRATION.md がコミット済み | ✅ `envs/ur7e.yaml` / `calibration/*.json` / `docs/freeze-x86.txt` / `docs/MIGRATION.md` |

### 12.2 確定した環境

| 項目 | 値 |
| --- | --- |
| lerobot | `v0.6.0` = `30da8e687a6dfc617fcd94afc367ac7071c376ce`（editable, `~/lerobot`） |
| python / conda env | 3.12.13 / `ur7e` |
| torch | `2.10.0+cu130`、CUDA 13.0、**sm_120 ネイティブカーネル同梱**（PTX JIT なし）✅ |
| torchvision | `0.25.0+cu130` |
| torchcodec | **`0.10.0` に明示ピン**（12.4-B） |
| ffmpeg | 7.1.1（conda-forge） |
| URSim | `universalrobots/ursim_polyscopex:10.12.1`, `ROBOT_TYPE=UR7e`, `192.168.56.101` |
| External Control URCapX | `1.1.0`（**リリースタグに `v` は付かない**。§5 の URL は 404） |

### 12.3 検証ラダーの結果（`scripts/verify_ladder.sh`）

| 段 | 内容 | 結果 |
| --- | --- | --- |
| 0 | 環境・プラグイン自動登録 | ✅ `ur5e` / `gello` / `keyboard_joint` が登録される |
| 1 | RTDE 双方向 + moveJ + servoJ | ✅ moveJ 誤差 0.00°、servoJ 125 Hz 実測 125.0 Hz / p95 8.00 ms |
| 2 | teleoperate | ✅ 125 fps→**124.1 Hz**（work 0.030 ms/tick、余裕 7.97 ms）／30 fps→**29.9 Hz**（work 22.5 ms、カメラ律速） |
| 3 | record → finalize → 読み戻し | ✅ v3.0 / 6 ep / 1440 frames / state・action とも 7 次元 / 動画デコード可 |
| 4 | train | ✅ ACT 300 steps（loss 13.7→3.2、22 step/s）、SmolVLA 200 steps（loss 2.3→0.13、14 step/s、100M params） |
| 5 | rollout | ✅ sync+ACT / RTC+SmolVLA / `lerobot-replay` / async（policy_server + robot_client, chunk `[1,50,7]` を 6.8 ms）|

**送信レートの結論**: `send_action`（servoJ）は 0.019 ms、`get_observation`（RTDE のみ）は 0.009 ms。**制御経路は 125 Hz に対して 2 桁の余裕がある**。30 fps でのボトルネックは UVC カメラ（22.5 ms）であって RTDE ではない。URSim の RTDE 配信周期は実測 2.000 ms（500.0 Hz）。

**未達 1 件**: `--inference.type=rtc` + **ACT** は upstream 側の制約で動かない（`ACTPolicy.predict_action_chunk()` が `inference_delay` を受け取らない）。RTC は flow-matching 系ポリシー向けであり、SmolVLA では正常動作する。実機で ACT を使う場合は sync エンジンを使う。

### 12.4 新たに踏んだ罠（§9 に追加）

**A. External Control URCapX 1.1.0 は「再生時に URScript を取りに来ない」** ✅実測
URCap 自身の設定画面に書いてある: *"The URScript is not fetched when playing the program. Use the 'update program' button in the program node."* つまり手順は
①PC 側でリスナを起動 → ②Program ノードを**開いた状態で** "Update program" を押す（ノードが valid になる）→ ③▶ 再生、の順。
さらに **プログラムを編集すると（Loop Program のトグルですら）キャッシュが無効化され、ノードが黄色に戻る**。「Program is not finished. Complete the yellow program-nodes」はこれ。①〜③をやり直す。

**B. torchcodec は torch と ABI で結合しており、エラーメッセージがそれを言わない** ✅実測
lerobot の制約は `torchcodec<0.12` と広いので、resolver は 0.11.1 を引く。しかし 0.11 は torch≥2.11 向けで、torch 2.10 では `undefined symbol: torch_dtype_float4_e2m1fn_x2` で落ちる。**torch 2.10 ↔ torchcodec 0.10.0** が正しい対。
加えて **conda の ffmpeg は `$CONDA_PREFIX/lib` にあり loader path に載っていない**ため `libavutil.so.59: cannot open shared object file` も同時に出る。`activate.d` フックで `LD_LIBRARY_PATH` を通す（`scripts/setup_env.sh` が設置する）。
**症状の特徴**: 収録は成功し、**読み戻しで初めて壊れる**（エンコードは PyAV / ffmpeg バイナリ経由、デコードは torchcodec 経由で別経路）。

**C. lerobot 0.6 はモータ特徴名が `.pos` で終わることを要求する** ✅実測
`lerobot/rollout/context.py` が `k.endswith(".pos")` でモータ特徴を選別する。上流由来の `joint_0` 命名のままだと **teleoperate と record は通るのに rollout だけ `KeyError: 'observation.state'`** で落ちる。本リポジトリは `joint_0.pos` … `gripper.pos` に統一した。

**D. lerobot 0.6 は ZMQ カメラを内蔵した** ✅実測
`lerobot.cameras.zmq` が `"zmq"` を登録するため、同名で登録する `lerobot_camera_zmq` プラグインを入れると **`register_third_party_plugins()` が両方を import した時点で全 `lerobot-*` コマンドが落ちる**。本リポジトリでは `"zmq_legacy"` に改名し、既定ではインストールしない。

**E. `--dataset.single_task` にコロンを入れてはいけない** ✅実測
draccus が `a: b` を dict として解釈し、task 文字列が `{'a': 'b'}` になる。言語条件付けに効くので実機収録では特に注意。

**F. GELLO の非同期読み取りスレッドは自前ではペーシングしない** ✅実測（本リポジトリ側のバグ）
`Gello._read_loop` は sleep 無しの `while` ループで、実機では serial 往復が律速になって成立している。モック実装で即返すと **GIL を占有して robot 側の呼び出しに ~5 ms/tick 乗り、125 Hz が 86 Hz に落ちた**。モックは実機同等の ~3 ms を意図的に消費する。実機の U2D2 が遅い個体に当たった場合も同じ形で teleop 全体が遅くなる、という一般則として憶えておく。

**G. UVC カメラの `exposure_dynamic_framerate`** ✅実測
暗所だと自動的にフレームレートを落とす。既定のままだと 640x480 が 15 fps になり `get_observation` が 65 ms になった。`v4l2-ctl --set-ctrl=auto_exposure=1 --set-ctrl=exposure_dynamic_framerate=0` で 30.1 Hz に回復。

**H. Main Program の Loop Program を有効にすると無人運用ができる** ✅実測
LeRobot セッション終了時の `stopScript()` でプログラムは停止する（runtime state 1=STOPPED）。Loop を有効にすると自動で再起動し、次の ur_rtde クライアントが UI 操作なしで接続できる。**実機では「人が ▶ を押すまで動かない」ことの価値と天秤にかけること。**

### 12.5 設計上の確定

- **設定の単一の源**: `config/site.yaml` を `ur7e_site` パッケージが読み、各 Config の `default_factory` から参照する。したがって `lerobot-teleoperate --robot.type=ur5e` のように **IP を CLI に書かなくても動く**。CLI フラグは常に上書きとして機能する。読み込みは**絶対に例外を投げない**（import 時に評価されるため、失敗すると `--help` すら死ぬ）。
- **グリッパは無効でも 7 次元を維持**: `gripper.pos` は `use_gripper: false` のとき 0.0 固定で存在する。URSim で録ったデータと実機データの schema が一致する。
- **モック GELLO は「下の層だけ」差し替える**: `MockDynamixelBus` は `DynamixelMotorsBus` のドロップイン。較正計算・7 次元 action 組み立て・非同期読み取り・EMA 平滑はすべて実機と同じコードが走る。モックの home counts は社内較正値 `[4,5,3,2,2,3]×1024` に合わせてあるので、`_process_action` を通すと `calibration_position` にぴったり一致する。
- **`keyboard_joint` は RTDE receive で現在姿勢を seed する**: teleoperator が起動時に勝手な姿勢を出力してアームが飛ぶのを防ぐ。実機のブリングアップでもそのまま使える。
- **較正の妥当性チェックを実装**: 取り直した offsets が `k×1024` から ±200 counts 以上ずれたら警告する（§4 のチェックをコード化）。

### 12.6 スコープ外のまま残ったもの

グリッパ実経路（PolyScope X の ToolComm Forwarder + socat + Modbus は未実装。`use_gripper: false` が実機でも当面正）／カメラ映像の意味（URSim に視覚なし）／実機の制御周期ジッタ・実時間性／安全設定・TCP・ペイロード。詳細は `docs/MIGRATION.md`。
