# RE9 FreeCam 数据采集使用手册

本手册面向需要长时间无人值守采集静态图或轨迹视频的操作者。项目通过
REFramework Lua 文件通道控制 FreeCam，通过 OBS WebSocket 截图和录像。

## 核心功能：报警与自动调试

长时间采集发生异常时，系统可以执行完整的无人值守恢复链路：

```text
采集异常
    -> 本地自动重试最多 3 次
    -> Discord + 飞书群报警
    -> 后台启动 Codex 自动调试
    -> 定位根因、修改代码并运行相关测试
    -> 从第一个缺失轨迹断点续采
    -> 验证新视频后继续批量任务
```

- **双通道报警：** Discord 与飞书通知在后台发送，支持 @全体和网络失败重试，
  不阻塞当前采集线程。
- **自动调试：** 常规重试全部失败后，可选的 Codex 恢复任务会收到错误内容、
  日志路径、运行目录、完成进度和下一条轨迹索引。
- **安全续采：** 已完成索引和有效视频保持不变，只从第一个缺失索引继续；新视频
  必须通过文件大小和可解码帧检查才会计入完成列表。
- **运行保护：** 同一时间只允许一个恢复任务，触发带冷却时间，运行状态与日志只
  对当前用户可读，并由 Git 忽略。

报警和 Codex 自动恢复可以分别启用。Codex 自动恢复具有较高的本机访问权限，只应
在专用且可信的采集机上开启。具体配置见第 10 节和第 11 节。

## Windows 与 Linux 版本结构

两个平台共用同一套采集、录像校验、报警、自动调试和断点恢复代码；只有配置发现、
启动命令、OBS 进程管理和游戏文件路径由平台适配层处理。

| 项目 | Windows 原生版 | Linux + Steam Proton 版 |
| --- | --- | --- |
| 配置模板 | `core/configs/windows.yaml` | `core/configs/linux.yaml` |
| 本机配置 | `core/configs/windows.local.yaml` | `core/configs/linux.local.yaml` |
| GUI 启动器 | `scripts/scan_gui.ps1` | `scripts/scan_gui.sh` |
| OBS 进程 | `obs64.exe`，使用 `taskkill` | `obs`，使用 `pkill` |
| Codex 任务锁 | Windows `msvcrt` | POSIX `flock` |
| 完整功能 | 报警、自动 Debug、断点续采、视频验证、定期重启 OBS | 报警、自动 Debug、断点续采、视频验证、定期重启 OBS |

`core/configs/default.yaml` 仅作为旧版 Windows 配置兼容入口保留。新部署应复制明确的平台
模板到对应的 `*.local.yaml`；本机路径和凭据只写入 local 文件。

## 1. 安全原则

- 不要提交游戏文件、截图、视频、数据集、日志、Webhook 或签名密钥。
- Windows 使用 `core/configs/windows.local.yaml`，Linux 使用
  `core/configs/linux.local.yaml`；两个文件都被 Git 忽略。
- Discord Webhook、飞书 Webhook 和飞书签名 Secret 都应视为密码。
- 如果凭据被发到公共聊天、截图或日志中，请立即在对应平台重新生成。
- `outputs/`、`core/runtime/`、视频和截图目录默认不会进入 Git。

## 2. 安装

Linux：

```bash
bash scripts/setup_linux.sh
source .venv/bin/activate
```

Linux 和 Windows 安装脚本都会在对应 local 配置不存在时创建它，并且不会覆盖已有
的本机配置。

Windows：

```powershell
.\scripts\setup_windows.ps1
.\.venv\Scripts\activate
```

Windows 安装脚本会在文件不存在时创建 `core/configs/windows.local.yaml`。也可以手动
执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item core\configs\windows.yaml core\configs\windows.local.yaml
```

然后安装并启动：

1. Resident Evil Requiem。
2. REFramework。
3. RE9 FREECAM CED。
4. OBS Studio，并启用 OBS WebSocket，默认端口为 `4455`。

## 3. 配置本机路径

编辑当前平台的 local 配置：

- Windows：`core/configs/windows.local.yaml`
- Linux：`core/configs/linux.local.yaml`

Windows 路径示例：

```yaml
game:
  lua_path: "D:/steam/steamapps/common/RESIDENT EVIL requiem BIOHAZARD requiem/reframework/autorun/RE9FreeCam.lua"
  reframework_dir: "D:/steam/steamapps/common/RESIDENT EVIL requiem BIOHAZARD requiem/reframework"
  reframework_data_dir: "D:/steam/steamapps/common/RESIDENT EVIL requiem BIOHAZARD requiem/reframework/data"

lua_logger:
  control_file: "D:/steam/steamapps/common/RESIDENT EVIL requiem BIOHAZARD requiem/reframework/data/re9_pose_control.json"
  status_file: "D:/steam/steamapps/common/RESIDENT EVIL requiem BIOHAZARD requiem/reframework/data/re9_pose_status.json"
  pose_log_file: "D:/steam/steamapps/common/RESIDENT EVIL requiem BIOHAZARD requiem/reframework/data/re9_freecam_pose_log.csv"
```

Linux/Proton 路径示例：

```yaml
game:
  lua_path: "/absolute/path/to/reframework/autorun/RE9FreeCam.lua"
  reframework_dir: "/absolute/path/to/reframework"
  reframework_data_dir: "/absolute/path/to/reframework/data"

lua_logger:
  control_file: "/absolute/path/to/reframework/data/re9_pose_control.json"
  status_file: "/absolute/path/to/reframework/data/re9_pose_status.json"
  pose_log_file: "/absolute/path/to/reframework/data/re9_freecam_pose_log.csv"

trajectory:
  capture_root: "/absolute/path/to/trajectory/captures"

obs:
  host: "localhost"
  port: 4455
  password: ""
  recording_output_dir: "data/videos"
```

`trajectory`、`obs`、通知和自动恢复配置在 Windows 与 Linux 上使用相同字段。
`trajectory.capture_root` 用于注册在 GUI 中的轨迹集。相对路径从项目根目录解析，
绝对路径适合把大量录像写到独立磁盘。

检查 OBS：

```powershell
.\.venv\Scripts\python.exe -m re9_pose_recorder.cli obs-test `
  --config core\configs\windows.local.yaml `
  --obs-password YOUR_PASSWORD
```

Linux：

```bash
.venv/bin/python -m re9_pose_recorder.cli obs-test \
  --config core/configs/linux.local.yaml \
  --obs-password YOUR_PASSWORD
```

## 4. 安装或更新 Lua 控制块

修改 Lua 前会自动创建备份：

```powershell
.\.venv\Scripts\python.exe -m re9_pose_recorder.cli check-lua `
  --config core\configs\windows.local.yaml
.\.venv\Scripts\python.exe -m re9_pose_recorder.cli backup-lua `
  --config core\configs\windows.local.yaml
.\.venv\Scripts\python.exe -m re9_pose_recorder.cli patch-lua-logger `
  --config core\configs\windows.local.yaml
.\.venv\Scripts\python.exe -m re9_pose_recorder.cli verify-lua-patch `
  --config core\configs\windows.local.yaml
```

Linux：

```bash
.venv/bin/python -m re9_pose_recorder.cli check-lua \
  --config core/configs/linux.local.yaml

.venv/bin/python -m re9_pose_recorder.cli backup-lua \
  --config core/configs/linux.local.yaml

.venv/bin/python -m re9_pose_recorder.cli patch-lua-logger \
  --config core/configs/linux.local.yaml

.venv/bin/python -m re9_pose_recorder.cli verify-lua-patch \
  --config core/configs/linux.local.yaml
```

更新 Lua 后需要重启游戏或重新加载 REFramework 脚本。

新版控制通道会为命令写入唯一 `command_id` 和 `issued_at`，并等待 Lua 状态文件返回
同一命令的确认。它可以拒绝延迟到达的旧命令，避免旧状态被误认为当前轨迹已启动。
Linux 上位于 NTFS/ntfs3 的控制文件使用带超时的辅助进程写入，防止文件系统卡顿冻结
整个 GUI。开始新轨迹前如果 Lua 仍在记录旧 session，程序会先等待旧 session 明确停止；
单个 `start`/`stop` 命令最多保留 8 秒，避免游戏线程短暂停顿时连续覆盖尚未消费的命令。

## 5. OBS 建议

- OBS Program 画面应只包含游戏，不要使用会录入控制窗口的全屏显示器捕获。
- NVIDIA 推荐 NVENC；AMD/Intel 可使用 VAAPI；也可以使用 x264。
- 确认 WebSocket 已开启、端口和密码正确。
- 长时间运行前先手动录一段视频，确认目录、编码器和磁盘空间正常。
- 不要在录像期间手动改变 OBS 输出目录。

## 6. 启动采集 GUI

Windows：

```powershell
.\scripts\scan_gui.ps1
```

Linux：

```bash
bash scripts/scan_gui.sh
```

两个启动器使用相同的环境变量。指定轨迹集：

```powershell
$env:TRAJECTORY_SET = "scene_2_again_true_gain2_distance4_step4_singleanchor_balanced_fast64_13000"
.\scripts\scan_gui.ps1
```

```bash
TRAJECTORY_SET=scene_2_again_true_gain2_distance4_step4_singleanchor_balanced_fast64_13000 \
bash scripts/scan_gui.sh
```

加载自定义轨迹：

```powershell
$env:TRAJECTORY_JSON = "D:\captures\trajectories.json"
$env:TRAJECTORY_OUTPUT_DIR = "D:\captures\my_scene"
$env:TRAJECTORY_LABEL = "my trajectory set"
$env:TRAJECTORY_SESSION_PREFIX = "my_scene"
.\scripts\scan_gui.ps1
```

```bash
TRAJECTORY_JSON=/path/to/trajectories.json \
TRAJECTORY_OUTPUT_DIR=/path/to/captures \
TRAJECTORY_LABEL="my trajectory set" \
TRAJECTORY_SESSION_PREFIX=my_scene \
bash scripts/scan_gui.sh
```

默认静态图稳定等待时间为 `0.6` 秒。可用 `SETTLE_SECONDS` 覆盖：

```powershell
$env:SETTLE_SECONDS = "0.8"
.\scripts\scan_gui.ps1
```

```bash
SETTLE_SECONDS=0.8 bash scripts/scan_gui.sh
```

## 7. 静态图扫描

1. 启动游戏并启用 FreeCam。
2. 在 OBS Program 中确认画面干净。
3. 关闭 REFramework 菜单。
4. 在 GUI 点击 `Start Still Scan`。
5. 需要停止时点击 `Stop After Current Shot`。

扫描器在每次截图前等待 Lua 确认新的唯一姿态。连续三次没有确认时会停止并报告错误，
不会继续保存姿态不正确的截图。

扫描结束后可以点击 `Delete Broken Capture Images`。该操作只删除明显损坏、无法读取或
近乎全黑/全白的截图，并在 `qa/` 下写入审计 CSV。

## 8. 轨迹视频采集

GUI 支持：

- 录制选中的单条轨迹。
- 从低分到高分顺序录制整个轨迹集。
- 从最新运行目录的第一个缺失轨迹继续。
- 为注册轨迹集使用 `trajectory.capture_root` 指定的独立磁盘。

每条轨迹的执行顺序是：

1. Lua 日志启动并返回当前命令确认。
2. FreeCam 准备姿态返回唯一分段确认。
3. OBS 切换到轨迹输出目录。
4. OBS 确认已进入录像状态。
5. Lua 确认并播放关键帧。
6. OBS 停止录像并等待文件完成写入。
7. GUI 验证视频文件存在、大小有效且包含可读取帧。
8. 成功后才把该索引写入完成列表。

单条轨迹失败会自动重试最多三次。配置了 OBS 重启命令时，失败重试前会重启 OBS，
清理残留的录像状态。

## 9. 断点续采与自动恢复

每个运行目录包含：

```text
trajectory_run_state.json
```

主要字段包括：

- `status`
- `planned_total`
- `completed_total`
- `remaining_total`
- `current_index`
- `next_index`
- `completed_indices`
- `last_video`
- `error`

手动恢复：点击 `Resume Latest Run`。

无人值守自动恢复：

```powershell
$env:RE9_TRAJECTORY_AUTO_RESUME = "1"
.\scripts\scan_gui.ps1
```

```bash
RE9_TRAJECTORY_AUTO_RESUME=1 bash scripts/scan_gui.sh
```

每 30 条安全重启 OBS：

```powershell
$env:RE9_OBS_RESTART_EVERY_N = "30"
$env:RE9_OBS_RESTART_WAIT_SEC = "30"
$env:RE9_TRAJECTORY_AUTO_RESUME = "1"
.\scripts\scan_gui.ps1
```

```bash
RE9_OBS_RESTART_EVERY_N=30 \
RE9_OBS_RESTART_WAIT_SEC=30 \
RE9_TRAJECTORY_AUTO_RESUME=1 \
bash scripts/scan_gui.sh
```

Windows 会自动查找 `obs64.exe`，Linux 会自动查找 `obs`。仅在非标准安装路径或需要
其他 OBS collection/profile 时设置 `RE9_OBS_RESTART_COMMAND`。

OBS 只会在一条轨迹已经完整写入后重启。GUI 会等待 WebSocket 重新连接后再开始下一条。

安全重启 GUI 时，应先点击 `Stop After Current Trajectory`，等待状态变成 `stopped`，
再关闭并使用 `RE9_TRAJECTORY_AUTO_RESUME=1` 重新启动。

## 10. Discord 与飞书错误通知

通知是后台发送的，不会阻塞采集。网络发送失败会自动重试三次。

触发范围：

- 静态图扫描最终失败。
- 轨迹录像在本地重试三次后仍失败。
- 坏图 QA 运行失败。

无法通知的情况包括整机断电、操作系统崩溃、Python 进程被强制终止，或机器完全断网。

### Discord

在当前平台的 `core/configs/*.local.yaml` 中添加：

```yaml
notifications:
  discord:
    webhook_url: "https://discord.com/api/webhooks/..."
    mention: "@everyone"
    username: "RE9 Capture Monitor"
    timeout_sec: 5
```

也可以使用环境变量：

```powershell
$env:RE9_DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/..."
$env:RE9_DISCORD_MENTION = "@everyone"
$env:RE9_DISCORD_USERNAME = "RE9 Capture Monitor"
.\scripts\scan_gui.ps1
```

```bash
RE9_DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..." \
RE9_DISCORD_MENTION="@everyone" \
RE9_DISCORD_USERNAME="RE9 Capture Monitor" \
bash scripts/scan_gui.sh
```

### 飞书

在目标群添加自定义机器人，推荐开启签名校验：

```yaml
notifications:
  feishu:
    webhook_url: "https://open.feishu.cn/open-apis/bot/v2/hook/..."
    secret: "机器人签名密钥"
    mention_open_id: "all"
    timeout_sec: 5
```

环境变量方式：

```powershell
$env:RE9_FEISHU_WEBHOOK_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/..."
$env:RE9_FEISHU_SECRET = "机器人签名密钥"
$env:RE9_FEISHU_MENTION_OPEN_ID = "all"
.\scripts\scan_gui.ps1
```

```bash
RE9_FEISHU_WEBHOOK_URL="https://open.feishu.cn/open-apis/bot/v2/hook/..." \
RE9_FEISHU_SECRET="机器人签名密钥" \
RE9_FEISHU_MENTION_OPEN_ID="all" \
bash scripts/scan_gui.sh
```

`mention_open_id` 可设为：

- `"all"`：@所有人。
- `""`：不 @。
- `"ou_xxx"`：@指定用户的飞书 `open_id`。

GUI 为 Discord 和飞书分别提供 `Send Test Alert` 按钮。正式采集前应分别测试一次。

发送失败日志：

```text
outputs/discord_notifications.log
outputs/feishu_notifications.log
```

日志只记录异常类型，不记录 Webhook 或签名密钥。

## 11. Codex 自动修复并续采

GUI 可以在静态图扫描、轨迹录像或坏图 QA 进入最终错误处理后，自动启动本机
Codex。程序自己的三次重试仍会先执行；只有最终失败才会触发 Codex。

在当前平台被 Git 忽略的 `core/configs/*.local.yaml` 中配置：

```yaml
automation:
  codex_recovery:
    enabled: true
    codex_bin: "/absolute/path/to/codex"
    proxy_url: "http://127.0.0.1:7890"
    prompt: "请修复问题并且重新开始采集"
    cooldown_sec: 900
    timeout_sec: 3600
```

也可以使用环境变量：

```powershell
$env:RE9_CODEX_RECOVERY_ENABLED = "1"
$env:RE9_CODEX_BIN = "C:\path\to\codex.exe"
$env:RE9_CODEX_PROXY_URL = "http://127.0.0.1:7890"
$env:RE9_CODEX_RECOVERY_PROMPT = "请修复问题并且重新开始采集"
$env:RE9_CODEX_RECOVERY_COOLDOWN_SEC = "900"
$env:RE9_CODEX_RECOVERY_TIMEOUT_SEC = "3600"
$env:RE9_TRAJECTORY_AUTO_RESUME = "1"
.\scripts\scan_gui.ps1
```

```bash
RE9_CODEX_RECOVERY_ENABLED=1 \
RE9_CODEX_BIN="/absolute/path/to/codex" \
RE9_CODEX_PROXY_URL="http://127.0.0.1:7890" \
RE9_CODEX_RECOVERY_PROMPT="请修复问题并且重新开始采集" \
RE9_CODEX_RECOVERY_COOLDOWN_SEC=900 \
RE9_CODEX_RECOVERY_TIMEOUT_SEC=3600 \
RE9_TRAJECTORY_AUTO_RESUME=1 \
bash scripts/scan_gui.sh
```

启用前，应先在运行采集 GUI 的同一系统用户下完成 Codex 登录，并确认
`codex exec` 可以访问本
仓库。自动任务会收到错误内容、日志路径、运行目录、当前进度和第一个缺失轨迹，
并被要求：

- 定位和修复根因，运行相关测试。
- 保留已完成的视频，从第一个缺失索引继续。
- 验证至少一条新轨迹完整落盘。
- 保持每 30 条重启 OBS、Discord/飞书通知和 @全体配置。
- 不输出、提交或上传本机配置、Webhook、签名密钥、GitHub token、日志和数据集。

设置 `proxy_url` 后，启动新会话时会删除 `ALL_PROXY` 和 `all_proxy`，并把
`HTTP_PROXY`、`HTTPS_PROXY` 及其小写形式统一设为该地址。Codex 同时使用
`--sandbox danger-full-access --ask-for-approval never -c features.apps=false`。
这些覆盖值只传给本次自动恢复的 Codex 子进程，不会执行 `export`，不会改变
GUI、OBS、系统代理、其他终端或其他 Codex 会话的网络环境。

自动恢复使用无交互 Codex 和完整本地文件访问权限，属于高信任功能，只应在专用
采集机启用。程序使用全局文件锁避免同时运行多个 Codex，并在每次触发后冷却 15
分钟。单次任务默认最长运行 60 分钟。

状态和日志位置：

```text
core/runtime/re9_pose_codex_recovery_state.json
outputs/codex_recovery.log
```

这两个文件不会进入 Git；Linux 会设置仅当前用户可读的 POSIX 权限，Windows 应
依赖采集账号和工作目录的 NTFS 权限保护。断电、系统崩溃、GUI 被强制结束或机器
完全断网时，Codex 自动恢复无法触发。

## 12. 常见错误与恢复

| 现象 | 处理 |
| --- | --- |
| Lua 没有确认日志启动或准备姿态 | 确认 FreeCam 已启用；重新运行 Lua patch；重启游戏或 REFramework |
| 控制文件写入超时 | 检查 Proton/NTFS 磁盘状态；确认游戏盘可写；不要删除运行中的辅助进程 |
| OBS 没有进入录像状态 | 检查编码器、输出目录、磁盘空间和 WebSocket |
| `StopRecord` 返回 501 | 新版会检查 OBS 是否已经停止并继续查找已完成文件；仍失败时重启 OBS |
| 某条轨迹没有有效视频 | GUI 不会标记完成，并会重试；之后可使用 `Resume Latest Run` |
| OBS GPU 内存持续增长 | 开启 `RE9_OBS_RESTART_EVERY_N=30` |
| 通知测试失败 | 检查 Webhook、签名、群机器人安全规则和网络；查看脱敏日志 |
| UI 重启后没有自动继续 | 确认设置了 `RE9_TRAJECTORY_AUTO_RESUME=1`，并检查最新状态文件 |

## 13. 测试

使用项目虚拟环境运行完整测试：

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m unittest discover -s core/tests -v
.\.venv\Scripts\python.exe -m compileall -q core/src core/tests
```

Linux：

```bash
PYTHONPATH=core/src .venv/bin/python -m unittest discover -s core/tests -v
PYTHONPATH=core/src .venv/bin/python -m compileall -q core/src core/tests
bash -n scripts/*.sh
```

## 14. 批量转码与上传脚本

`scripts/media/` 包含已用于现有数据集的批处理脚本：

```text
scripts/media/transcode_3000_hevc_nvenc.sh
scripts/media/transcode_4000_hevc_nvenc.sh
scripts/media/transcode_10000_hevc_nvenc.sh
scripts/media/upload_4000_to_h_ceph.sh
scripts/media/upload_scene_1_2_10000_half_to_h_ceph.sh
scripts/media/upload_scene_1_3_3000_to_h_ceph.sh
```

这些脚本不包含本机路径、内网地址或 rclone 凭据。通过环境变量提供运行参数。

转码示例：

```bash
SOURCE_ROOT=/path/to/source \
OUTPUT_ROOT=/path/to/output \
FFMPEG=/path/to/ffmpeg \
FFPROBE=/path/to/ffprobe \
bash scripts/media/transcode_10000_hevc_nvenc.sh
```

上传示例：

```bash
SOURCE=/path/to/dataset \
DESTINATION=my-rclone-remote:path/to/dataset \
ENDPOINT_HOST=optional.internal.endpoint \
bash scripts/media/upload_scene_1_2_10000_half_to_h_ceph.sh
```

`EXPECTED_VIDEOS`、`EXPECTED_FILES`、码率、日志路径和锁文件路径也可以通过同名
环境变量覆盖。rclone 的远端访问凭据只保存在操作者本机的 rclone 配置中。

转码脚本会：

- 使用文件锁避免重复运行。
- 检查输入视频总数。
- 使用 NVENC HEVC 转码并删除音频。
- 验证输出编码和视频有效性。
- 支持跳过已经完成的有效文件。

上传脚本会：

- 检查本地文件总数。
- 使用 rclone 断点续传。
- 上传完成后执行远端大小校验。
- 写入本机日志和状态文件；这些运行文件不会进入 Git。
