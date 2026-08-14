# Black Myth: Wukong adapter

[中文](README.md) · [English](README.en.md)

黑神话适配器是在 UUU 外部运行的轻量采集界面。它读取 Connector 共享内存中的相机位姿，管理 JSON/CSV 点位与轨迹，并通过游戏进程内的 Native Bridge 直接调用 UUU 5.8.21 相机方法完成闭环移动；静态原图统一由 OBS WebSocket 的 Program/Source 截图接口写入，不使用窗口截屏回退。

## 能力边界

| 能力 | 当前状态 |
|---|---|
| UUU 自由相机 | UUU 正确注入后可用 |
| XYZ、四元数、Yaw/Pitch/Roll、FOV | 需要 Native Bridge、Connector 握手和有效 Pose |
| 点位图记录、Load/Export | 已实现，点位图保留 XYZ、方向与 FOV |
| 每个空间点自动静态 22 方向采集 | 已实现，一键自动跨点运行，与 RE9/KCD2 共用“水平 8 + 上 6 + 下 6 + 顶/底”规范 |
| 单条/批量轨迹 OBS 录像 | 已实现，含任务/样本双进度 |
| 未完成批次自动续采 | 已实现，按实际产物检查后跳过完整轨迹 |
| 无声录像保护 | 录制前静音 OBS 音频输入，完成后恢复原状态 |
| 绝对 Pose 目标 | 已实现 UUU 5.8.21 相对步进＋Pose 反馈闭环；不是一条命令瞬移的原子 `setPose`，仍需游戏内画面验收 |
| 电影级逐帧精确轨迹 | 原生控制后端已实现；逐帧平滑度和画面结果仍待游戏内验收 |

仅看到 DLL 已加载并不代表位姿链路已经打通。界面只有在 Bridge 版本、游戏 PID、UUU 握手、有效 Pose 和 Camera ON 全部通过后才启用采集。

## 使用顺序

1. 启动游戏并进入画面，推荐无边框窗口模式。
2. 双击 `启动黑神话采集工具.bat`。
3. 在界面中选择本机 UUU 目录并执行“准备位姿桥”。
4. 再打开 UUU、选择当前游戏进程并 Inject。
5. 回到游戏按 `Insert` 启用自由相机，等待界面出现实时 Pose。

如果 UUU 先于位姿桥注入，当前会话无法补握手；需要彻底退出游戏后按正确顺序重试。

## Linux/Proton 启动与 Bridge Relay

```bash
cd games/black-myth-wukong
sudo apt install python3 python3-venv python3-tk  # Debian/Ubuntu 缺少依赖时执行
chmod +x launch_bmw_capture_studio.sh
./launch_bmw_capture_studio.sh
```

使用 `./launch_bmw_capture_studio.sh --trajectory-file /path/to/file.json` 可以预选轨迹。配置 Proton Relay 后，Linux 界面可使用与 Windows 相同的点位/轨迹文件、OBS WebSocket 采集、Pose 记录、原生轨迹播放和 Pose 反馈定位。游戏与 UUU/Bridge 仍是运行在 Proton 内的 Windows 二进制；Linux 界面通过注入到游戏内的 Bridge DLL 提供的本机回环 TCP Relay 访问它们。

启动游戏和采集器前，让两侧使用同一个端口：

```bash
# 黑神话：悟空的 Steam/Proton 启动选项
BMW_BRIDGE_PORT=28791 %command%

# 启动 Linux 采集器的终端
export BMW_BRIDGE_ENDPOINT=127.0.0.1:28791
./launch_bmw_capture_studio.sh
```

Bridge DLL 仍需通过 UUU 流程加载到 Proton 游戏进程。如果 UUU Client 需要指定 Wine/Proton 启动方式，设置 `BMW_UUU_COMMAND`；未设置时程序使用 `wine`。状态栏会先显示“Linux/Proton Bridge Relay”等待，只有 Relay 发布有效元数据、Pose、原生控制能力和轨迹状态后才启用采集。Relay 只绑定回环地址。Linux 不启用全局 F8，请使用界面中的记录点位按钮。

## 飞书报警与自动修复

程序复用 RE9 的配置查找顺序和字段：`configs/linux.local.yaml` → `configs/linux.yaml` → `configs/default.yaml`。在 `notifications.feishu` 中配置 `webhook_url`、`secret`、`mention_open_id` 后，界面错误会异步发送飞书文本报警；也可以设置 `RE9_FEISHU_WEBHOOK_URL`、`RE9_FEISHU_SECRET` 和 `RE9_FEISHU_MENTION_OPEN_ID` 覆盖配置。报警默认关闭，失败日志只记录异常类型，不记录 Webhook 或签名密钥。

`automation.codex_recovery.enabled` 默认是 `false`。只有明确启用并配置 `codex_bin`（或 `RE9_CODEX_BIN`）后，错误才会排队启动独立修复 worker；它使用 RE9 的 `RE9_CODEX_*` 字段、冷却锁和私有状态文件，修复日志位于 `capture_data/logs/`。修复提示默认先做离线检查，不会自动启动游戏或采集；不要把 `*.local.yaml`、Webhook、密钥、日志或数据集提交到 GitHub。

## Native Bridge

仓库只提交 Bridge 源码：

```text
native/
├─ BmwUuuPoseBridge.cpp
├─ CMakeLists.txt
└─ build_bridge.ps1
```

构建产物 `native/build/Release/IgcsConnector.addon64` 不会提交。UUU 本体与 Connector 也不随仓库分发。

## 数据与测试

运行数据写入忽略提交的 `capture_data/`，公开的点位和轨迹格式示例位于 `examples/`。

静态点位图采集写入 `capture_data/still_captures/`。每个空间点展开为 22 张游戏客户区原图，`manifest.json/.csv` 同时保存空间点编号、方向组、目标 Pose、实际 Pose 和图片路径；界面可从指定空间点继续执行。

轨迹录像统一写入：

```text
capture_data/trajectory_captures/<scene-id>/<batch-id>/
├─ run_manifest.json
├─ trajectory_index.csv
├─ trajectory_set_source.json/.csv
└─ traj_0001/
   ├─ raw/segment_0001/video.*
   ├─ raw/segment_0002/video.*  # 长轨迹按 OBS 重启分段，数量按需增加
   ├─ obs_restart.log
   ├─ source_keyframes.csv
   ├─ playback_plan.csv
   ├─ observed_pose.csv
   ├─ trajectory_timing.csv
   └─ recording_manifest.json
```

轨迹界面使用文件下拉菜单，选中即自动 Load；主按钮从指定编号连续采集到文件末尾，并可自动继续未完成批次。静态 22 方向采集会先解析 OBS 当前 Program 场景，再逐张通过 OBS WebSocket 保存原图，并在清单中记录截图来源。续采不是只看清单状态，而是检查每条轨迹的视频、四类 CSV 和完成清单是否齐全。OBS 密码只在当前界面内存中使用，也可通过 `BMW_OBS_PASSWORD` 环境变量注入，不写入 `settings.json`。

`playback_plan.csv` 是提交给 UUU 5.8.21 进程内原生相机控制的绝对目标样本；实际回读位姿单独保存在 `observed_pose.csv`。实现以真实 Pose 闭环收敛，不再依赖游戏窗口焦点或模拟按键。该后端锁定 UUU 5.8.21，其他版本会拒绝内部调用。

轨迹录像默认在 Windows 和 Linux 上每 30 秒按 RE9 的安全顺序滚动一次 OBS：结束当前分段、恢复音频状态、关闭 WebSocket、终止并重新启动本机 OBS、轮询 WebSocket 健康状态；Linux 在安装 `wmctrl` 或 `xdotool` 时还会恢复 Proton 游戏窗口焦点，再开始下一段录像。原生 UUU 轨迹控制不会被 Python 每帧接管，因此相机继续按原生轨迹运行；OBS 重启期间可能出现一个可审计的录像间隔。每条轨迹的 `recording_manifest.json` 会保存 `video_segments`、每段起止时间、`obs_restart_events` 和 `video_paths`。可在 `settings.json` 中设置 `trajectory_obs_restart_interval_sec`，设为 `0` 可关闭；若 OBS 不在本机，需要显式配置 `obs_restart_command`，避免误杀本机 OBS。

```powershell
cd games\black-myth-wukong
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

迁移前原说明保存在 [`docs/ORIGINAL_GUIDE.md`](docs/ORIGINAL_GUIDE.md)。
