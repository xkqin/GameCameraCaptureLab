# KCD2 Camera Tools v1.0.5：同目录位姿与控制

本目录方案不修改原始 `KCD2CameraTools.dll`，也不依赖 ReShade、IGCS
Connector 或第三方 Python 包。全部新增文件均位于：

```text
D:\下载\KCD2_CameraTools_v105\KCD2PoseControl
```

## 已确认的闭源 DLL 接口

- `KCD2CameraTools.dll` v1.0.5 SHA-256：
  `9600C8CE3B32AE78177603695287126B05B3B165AD8820283544E8AD420B5D96`
- DLL 的四个公开导出：
  - `IGCS_StartScreenshotSession`
  - `IGCS_MoveCameraPanorama`
  - `IGCS_MoveCameraMultishot`
  - `IGCS_EndScreenshotSession`
- 四个导出都从 `module base + 0x15EF40` 读取同一个
  `CameraFeature` 指针。
- 该对象的 `+0x1C48` 保存 `CameraBase` 指针。
- IGCSClient 命名管道没有实时 pose 消息；只有设置、按键、动作和 Camera
  Path 状态。

本工具直接读取相机对象，并可在显式执行 `set-pose` 时写入已验证的相机
字段。它不会修改或覆盖原始 DLL 文件。

## 已确认的 pose 布局

在 `KCD2CameraTools.dll` v1.0.5 和 `KingdomCome.exe` 1.5.6.0 上已确认：

| 字段 | CameraBase 偏移 | 类型 |
| --- | ---: | --- |
| X | `0x18` | `double` |
| Y | `0x20` | `double` |
| Z | `0x28` | `double` |
| q0-q3 | `0x30`-`0x3C` | 4 × `float` |
| FOV | `0x40` | `float`，弧度 |
| pitch | `0x44` | `float`，弧度 |
| yaw | `0x48` | `float`，弧度 |
| roll | `0x4C` | `float`，弧度 |

实机测试已确认：

- XYZ、四元数、FOV、pitch/yaw/roll 可稳定读取和录制；
- 四元数模长保持约 `1.0`；
- 直接写 CameraBase 字段虽然能读回，但不会改变游戏画面，不能视为
  可用的绝对 `setPose`；
- 通过 DLL 自带的 Panorama/Multishot 导出函数可以实际移动游戏相机；
- 导出 Session 结束时会精确恢复 Session 起点。

## 使用前提

1. 启动游戏并进入实际场景。
2. 运行上级目录的 `IGCSClient.exe`，点击 `Inject DLL`。
3. 等待日志出现 `Camera found.`。
4. 按 `Insert` 启用自由相机。

## 命令

在 PowerShell 中：

```powershell
cd 'D:\下载\KCD2_CameraTools_v105\KCD2PoseControl'
```

检查运行状态：

```powershell
python .\kcd2_pose_control.py status
```

读取当前完整位姿：

```powershell
python .\kcd2_pose_control.py pose
```

实验性 CameraBase 缓存写入：

```powershell
python .\kcd2_pose_control.py set-pose --x 1500 --y 2300 --z 270
python .\kcd2_pose_control.py set-pose --dx 0.5
```

该命令用于逆向验证，写入值不会反映到当前游戏画面，不应当用于正式
相机控制。

调用 DLL 自带接口做可见移动并自动恢复：

```powershell
python .\kcd2_pose_control.py export-test --right 1 --hold-seconds 2
python .\kcd2_pose_control.py export-test --panorama-deg 5 --hold-seconds 2
```

生成并立即执行一条平滑随机运镜：

```powershell
python .\kcd2_pose_control.py random-trajectory --duration 8 --hz 20
```

指定 `--seed` 可复现完全相同的计划轨迹：

```powershell
python .\kcd2_pose_control.py random-trajectory `
  --duration 8 --hz 20 --seed 1435071599 --xy-scale 12
```

`--xy-scale` 只放大水平行程，不改变升降、旋转和 FOV 幅度。

轨迹 CSV 和随机参数 JSON 会保存在 `pose_data`。运镜结束后 Session
保持在最终机位；恢复到运镜起点：

```powershell
python .\kcd2_pose_control.py export-end
```

相机相对控制：

```powershell
python .\kcd2_pose_control.py control forward --duration-ms 300
python .\kcd2_pose_control.py control rotate_right --duration-ms 150
python .\kcd2_pose_control.py control fov_in
python .\kcd2_pose_control.py control path_play_pause
```

支持的动作可通过以下命令查看：

```powershell
python .\kcd2_pose_control.py control --help
```

保存一次相机内存快照：

```powershell
python .\kcd2_pose_control.py capture --label before
```

比较两次快照：

```powershell
python .\kcd2_pose_control.py diff <before.json> <after.json>
```

自动校准：

```powershell
python .\kcd2_pose_control.py calibrate
```

`calibrate` 会在确认日志最后状态为 `Camera enabled` 后：

1. 保存基线；
2. 让相机短暂向前；
3. 让相机短暂向后返回；
4. 让相机短暂向右旋转；
5. 输出 `position_candidate` 和 `orientation_candidate`。

结果位于：

```text
KCD2PoseControl\pose_data
```

## CSV 记录

正式偏移已写入 `pose_offsets.json`。运行：

```powershell
python .\kcd2_pose_control.py record `
  --seconds 60 `
  --hz 30
```

CSV 包含时间戳、XYZ、原始四元数、FOV、pitch、yaw 和 roll。
`pose_offsets.example.json` 仅保留为重新逆向其他版本时的模板。

## 版本边界

偏移和写入只针对当前 `KCD2CameraTools.dll` v1.0.5。相机工具更新后，
脚本会通过 SHA-256 检查并拒绝用旧偏移写入。相对控制功能沿用
`IGCSClientSettings.ini` 的当前键位，因此用户更改键位后也要同步修改脚本。
