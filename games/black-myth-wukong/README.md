# 统一游戏相机采集器 ·《黑神话：悟空》适配器

[中文](README.md) · [English](README.en.md)

这是 **统一游戏相机采集器 / Unified Game Camera Capture Studio** 的《黑神话：悟空》适配器。统一界面、OBS、点位、轨迹、通知和数据格式可复用于其他游戏；本目录只保留该游戏的 profile、运行时兼容入口和采集数据目录。它默认使用项目自研的通用 `UeCameraRuntime.dll`，直接挂接该游戏的 UE5 LWC 相机复制路径，并提供自由相机、精确位姿、绝对 `setPose`、22 方向静态扫描和进程内平滑轨迹。旧 `bmw_capture_studio` Python 包名与 `BmwCameraBridge` 文件名只作为兼容接口保留，不再代表产品名称。

## 当前能力

| 能力 | 状态 |
|---|---|
| WASD/QE 自由移动（Camera ON 时独占，不再驱动角色） | 已实现 |
| 鼠标自由观察（Yaw/Pitch） | 已实现 |
| Shift 5× 加速移动 | 已实现 |
| Delete / 界面按钮隐藏与恢复 HUD | 已实现，需游戏内视觉验收完整覆盖范围 |
| double 精度 XYZ、Yaw/Pitch/Roll、FOV | 已实现 |
| 原子绝对 `setPose` | 已实现 |
| 进程内连续 Hermite 轨迹 | 已实现；结束后保持终点 |
| 点位文件、自动 22 方向静态采集 | 已接入 |
| OBS 1920×1080 JPG 截图 | 已接入 |
| 单条/批量轨迹录像、继续采集 | 已接入 |
| 每 30 秒重启 OBS 分段释放显存 | 已接入 |
| Windows 直接注入 | 已实现 |
| Linux/Proton 回环 Relay | 已实现；需要同一 Proton 前缀启动 Injector |

源码构建和离线协议测试已经通过；自研 Runtime 也已在黑神话进程中完成签名定位、Hook 安装和真实渲染 Pose 读取。最终 v1 产物的自由移动、HUD 完整覆盖、远距离 `setPose` 和轨迹画面仍需一次干净游戏会话验收，因此当前保持“实验性”，不把实现完成误写成最终稳定。

## Windows 使用顺序

1. 完全退出游戏、其他相机工具和 IGCSClient，确认旧游戏进程已经消失。
2. 只启动《黑神话：悟空》，进入正在渲染的场景；建议使用无边框窗口。
3. 在仓库的 `launchers/` 目录运行 `启动统一游戏相机采集器.bat`；也可执行 `launchers\\launch_unified_capture_studio.ps1 -GameId black-myth-wukong`。旧适配器启动脚本继续兼容。
4. 点击“注入 Camera Bridge”。界面默认调用通用 `UeCameraInjector.exe`，并拒绝与其他相机运行时/旧 Connector 叠加 Hook。
5. 状态显示 Pose、绝对 `setPose` 和轨迹能力就绪后开始采集。

手动控制：

- `Insert`：开关自由相机
- `Home`：锁定/解锁手动移动
- `Delete`：隐藏/恢复 HUD
- 鼠标移动：自由控制 Yaw/Pitch；灵敏度可由 `BMW_CAMERA_MOUSE_SENSITIVITY` 调整
- `W/S`、`A/D`、`Q/E`：前后、左右、下上
- 方向键：Yaw/Pitch
- `Z/C`：Roll
- 小键盘 `+/-`：FOV
- `Shift`：5× 加速；`Ctrl`：慢速

自动点位和轨迹采集会主动启用相机，不要求先按 `Insert`。

## 飞书与 Discord 通知

采集界面的“通知与自动修复 / Notifications & Recovery”区域提供中英文设置指南和两种通知的独立测试按钮。推荐复制 `core/configs/windows.yaml` 为不会提交的 `core/configs/windows.local.yaml`，只在本机写入真实密钥：

```yaml
notifications:
  discord:
    webhook_url: "https://discord.com/api/webhooks/..."
    mention: ""
    username: "Unified Camera Capture"
    timeout_sec: 5
  feishu:
    webhook_url: "https://open.feishu.cn/open-apis/bot/v2/hook/..."
    secret: ""
    mention_open_id: ""
    timeout_sec: 5
```

也可使用 `UNIFIED_DISCORD_WEBHOOK_URL`、`UNIFIED_FEISHU_WEBHOOK_URL` 和 `UNIFIED_FEISHU_SECRET`。如果配置文件放在其他位置，用 `UNIFIED_CAMERA_CONFIG` 指定。旧变量只作为向后兼容保留，所有真实 Webhook 和 Secret 都不能提交到 GitHub。

## 构建自研 Bridge

需要 Visual Studio 2022 Build Tools、CMake 和 x64 MASM：

```powershell
cd games\black-myth-wukong\native
.\build_standalone.ps1
```

产物：

```text
native/build_standalone_v1/Release/
├─ UeCameraRuntime.dll          # 默认通用运行时
├─ UeCameraInjector.exe         # 默认通用注入器
├─ BmwCameraBridge.dll          # 兼容名称
└─ BmwCameraInjector.exe        # 兼容名称
```

仓库不包含或分发第三方闭源二进制。

## Linux/Proton

先让游戏进程继承 Relay 端口：

```bash
# Steam 启动选项
BMW_BRIDGE_PORT=28791 %command%
```

Linux 采集界面连接同一端口：

```bash
export BMW_BRIDGE_ENDPOINT=127.0.0.1:28791
export BMW_PROTON_COMMAND="/path/to/Proton/proton"
./launch_bmw_capture_studio.sh
```

`BMW_PROTON_COMMAND` 必须使用与游戏相同的 Proton 前缀环境。复杂启动方式可以改用：

```bash
export BMW_CAMERA_INJECT_COMMAND='"/path/to/Proton/proton" run {injector}'
```

Relay 只监听 `127.0.0.1`，协议支持读取状态、相对控制、绝对 `setPose`、启动轨迹和停止轨迹。Linux 全局 F8 不可用，请使用界面按钮记录点位。

## 采集产物

静态采集写入 `capture_data/still_captures/`，每个空间点展开为 22 个方向，并保存目标 Pose、实际 Pose、JPG 路径和 manifest。

轨迹采集写入：

```text
capture_data/trajectory_captures/<scene>/<run>/
├─ run_manifest.json
├─ trajectory_index.csv
├─ trajectory_set_source.json/.csv
└─ traj_0001/
   ├─ raw/segment_0001/video.*
   ├─ source_keyframes.csv
   ├─ playback_plan.csv
   ├─ observed_pose.csv
   ├─ trajectory_timing.csv
   └─ recording_manifest.json
```

## 测试

```powershell
$env:PYTHONPATH = "src;games/black-myth-wukong/src"
python -m unittest discover -s games/black-myth-wukong/tests -v
```

游戏更新可能改变相机指令签名。若界面显示 `hook_unavailable`，应停止采集并重新定位签名，不能盲目写内存。
