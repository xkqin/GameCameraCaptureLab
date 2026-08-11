# KCD2 Camera Capture Studio

这是与 RE9 项目同级的独立 KCD2 相机采集项目：

```text
<仓库父目录>
├─ re9-freecam-aesthetic-pose-recorder
└─ kcd2-camera-capture-studio
```

项目使用原版 `KCD2CameraTools.dll` v1.0.5，但不修改这个闭源 DLL。程序自行完成 DLL 注入、pose 读取、点位管理、OBS 采集和已验证的随机运镜。

## 当前能力

| 链路 | 状态 |
|---|---|
| 从本项目界面注入相机 DLL | 已实现，运行时需游戏内复验 |
| 自由相机 Insert 切换与 WASD/方向键控制 | 已接入官方热键 |
| 实时 XYZ、四元数、yaw/pitch/roll、FOV | 已实现 |
| 连续 pose CSV | 已实现 |
| 场景点 Capture / Reset / 自动备份 | 已实现 |
| 点位边界、XYZ 网格、每点严格 22 方向计划 | 已实现 |
| OBS 当前机位截图 + pose metadata | 已实现，需 OBS 联调 |
| OBS 录像 + 连续 pose 时间清单 | 已实现，需 OBS 联调 |
| 20 Hz 平滑随机运镜、seed 复现、恢复起点 | 已实测 |
| 任意绝对 XYZ + yaw/pitch/roll 精确 setPose | 尚未打通 |
| 自动执行全部空间点 × 22 方向截图 | 等待绝对控制验收 |
| 通用 JSON 关键帧精确回放 | JSON 已可解析，精确回放待打通 |

“读取到 pose”不等于“能够写入 pose 并改变游戏画面”。直接写 CameraBase 字段的实验只出现内存读回变化，画面不变，因此界面不会把它冒充为可用的绝对 setPose。

## 启动

1. 启动《Kingdom Come: Deliverance II》，进入可操作画面。
2. 双击 `启动KCD2采集工具.bat`。
3. 在“系统与实时 Pose”页点击“一键注入 DLL”。
4. 点击“切换自由相机 (Insert)”。
5. 观察实时 pose，然后使用系统页按钮或游戏内 WASD 自由移动。

启动器按以下顺序选择 Python：

1. 本项目 `.venv`
2. RE9 项目现有 `.venv`（已经包含 `obsws-python`）
3. 系统 `py` / `python`

## OBS 设置

在 OBS 中打开“工具 → WebSocket 服务器设置”，启用服务器，默认端口为 `4455`。界面中的 OBS 密码只在当前进程内使用，不会写入 `capture_studio_settings.json`。

静态截图会生成图片、`samples.csv` 和 `samples.json`。录像会生成 `recording_manifest.json` 和连续 pose CSV，供后续按时间戳做视频帧对齐。

## 22 方向规则

每个空间点生成 22 个方向：

- pitch 0°，yaw 每 45°：8 张
- pitch +45°，yaw 每 60°：6 张
- pitch -45°，yaw 每 60°：6 张
- pitch +90°：1 张
- pitch -90°：1 张

这里的计划生成是可靠的；自动执行仍取决于 KCD2 任意绝对位姿控制。

## 项目结构

```text
kcd2-camera-capture-studio
├─ camera_tools\                 # 原版相机 DLL 与配置
├─ docs\                         # 逆向验证记录
├─ src\kcd2_capture_studio\
│  ├─ backend.py                 # 低层桥接与 pose logger
│  ├─ storage.py                 # 场景点和关键帧持久化
│  ├─ planner.py                 # 空间网格与 22 方向计划
│  ├─ obs_bridge.py              # OBS WebSocket
│  ├─ capture.py                 # 静态截图 metadata
│  ├─ recording.py               # OBS 录像 + pose 时间清单
│  ├─ trajectory.py              # 轨迹解析与随机运镜
│  └─ ui\
│     ├─ main_window.py
│     ├─ system_tab.py
│     ├─ points_tab.py
│     ├─ stills_tab.py
│     ├─ trajectory_tab.py
│     └─ common.py
├─ tests\
├─ kcd2_pose_control.py          # 已验证的 Windows 低层桥
├─ launch_kcd2_capture_studio.ps1
└─ 启动KCD2采集工具.bat
```

所有新数据默认写入：

```text
capture_studio_data\
├─ scene_points
├─ scan_plans
├─ stills
├─ pose_logs
├─ trajectories
├─ runs
├─ backups
└─ low_level
```

## 离线验证

在项目目录运行：

```powershell
python -m compileall -q kcd2_pose_control.py src tests
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

低层逆向边界和实测结果见 `docs\camera_reverse_engineering.md`。
