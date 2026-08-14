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

## Linux 启动与能力边界

```bash
cd games/black-myth-wukong
sudo apt install python3 python3-venv python3-tk  # Debian/Ubuntu 缺少依赖时执行
chmod +x launch_bmw_capture_studio.sh
./launch_bmw_capture_studio.sh
```

使用 `./launch_bmw_capture_studio.sh --trajectory-file /path/to/file.json` 可以预选轨迹。Linux 支持 Tk 界面、JSON/CSV 点位和轨迹文件管理、离线数据处理、打开输出目录，以及 OBS WebSocket 连接；但黑神话实时相机链路仍不支持 Linux：UUU 5.8.21 注入、Windows Native Bridge、Connector Pose 共享内存和游戏内相机控制都需要 Windows。Linux 下全局 F8 不启用，请使用界面按钮；状态栏会明确显示兼容模式。

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
   ├─ raw/video.*
   ├─ source_keyframes.csv
   ├─ playback_plan.csv
   ├─ observed_pose.csv
   ├─ trajectory_timing.csv
   └─ recording_manifest.json
```

轨迹界面使用文件下拉菜单，选中即自动 Load；主按钮从指定编号连续采集到文件末尾，并可自动继续未完成批次。静态 22 方向采集会先解析 OBS 当前 Program 场景，再逐张通过 OBS WebSocket 保存原图，并在清单中记录截图来源。续采不是只看清单状态，而是检查每条轨迹的视频、四类 CSV 和完成清单是否齐全。OBS 密码只在当前界面内存中使用，也可通过 `BMW_OBS_PASSWORD` 环境变量注入，不写入 `settings.json`。

`playback_plan.csv` 是提交给 UUU 5.8.21 进程内原生相机控制的绝对目标样本；实际回读位姿单独保存在 `observed_pose.csv`。实现以真实 Pose 闭环收敛，不再依赖游戏窗口焦点或模拟按键。该后端锁定 UUU 5.8.21，其他版本会拒绝内部调用。

```powershell
cd games\black-myth-wukong
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

迁移前原说明保存在 [`docs/ORIGINAL_GUIDE.md`](docs/ORIGINAL_GUIDE.md)。
