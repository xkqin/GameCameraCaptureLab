# Kingdom Come: Deliverance II adapter

[中文](README.md) · [English](README.en.md)

KCD2 适配器提供独立 Tkinter 界面，用于相机 DLL 注入顺序管理、实时 pose、场景边界点、分层扫描计划、OBS 采集与轨迹实验。

## 能力边界

| 能力 | 当前状态 |
|---|---|
| Camera Tools 自由相机 | 已在游戏中验证 |
| XYZ、四元数、Yaw/Pitch/Roll、FOV | 已验证可读 |
| 连续 pose CSV、点位与扫描计划 | 已验证 |
| OBS RGB + Pose 静态样本 | 已实现，需要 OBS WebSocket |
| 原始深度 `depth.npy` + 预览图 | 输出接口与离线转换已验证；自研原生后端待接入 |
| 20 Hz 相对随机运镜与 seed 复现 | 已验证 |
| 任意绝对 `setPose` 改变最终游戏画面 | 尚未确认 |
| 任意 JSON 关键帧精确回放 | 代码路径存在，仍取决于绝对控制验收 |

内存字段写回后读值变化，不等于渲染相机确实移动。因此适配器不会把绝对轨迹回放标成已完成。

## 统一采集入口

KCD2 现在可以从仓库统一入口选择，但不会被误当成 UE5 profile：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File launchers\launch_unified_capture_studio.ps1 -GameId kcd2
```

这条路由直接启动本适配器的 Camera Tools/IGCS 底层后端，并把运行数据写到
`capture_data/kcd2/`；`games/kcd2/capture_studio_data/` 的旧数据和直接启动方式仍保留。
统一入口只负责路由和路径注入，不会复制或上传闭源 DLL。若传入 `-TrajectoryFile`，
采集界面打开时会自动载入该 JSON 轨迹。

## 本地准备

闭源 Camera Tools 不在仓库中。统一启动器按“`-CameraToolsDir` 参数 →
`GAME_CAMERA_TOOLS_DIR` → 仓库目录 → 同级旧工程”自动发现，并在注入前检查
`KCD2CameraTools.dll`、`IGCSClient.exe` 和 v1.0.5 DLL 的固定 SHA256。也可以把合法取得的文件放到：

```text
games/kcd2/camera_tools/
├─ KCD2CameraTools.dll
├─ IGCSClient.exe
└─ 其余原工具文件
```

游戏进入可操作画面后，双击 `启动KCD2采集工具.bat`，再从“系统与实时 Pose”页执行一键准备与注入。IGCS Client 必须先于相机 DLL 启动；失败会话需要彻底重启游戏。

## RGB + Pose + Depth 静态采集

静态页新增默认关闭的可选项“同时采集原始深度”。勾选后每个样本写成：

```text
sample_000001/
├─ rgb.jpg
├─ depth.npy
├─ depth_preview.png
└─ metadata.json
```

RGB 仍由 OBS WebSocket 输出。仓库已用自研原生 D3D12 Runtime 替代外部深度
Add-on，但当前只完成《黑神话：悟空》的运行时接入；KCD2 还没有安全加载该后端，
所以本适配器目前不会启用或伪造深度。接入后仍将输出归一化 raw device depth，
并在取得可审计投影矩阵或完成近远平面标定前保持 `metric_depth=false`。

相机坐标按用户提供的尺度 `1 game unit = 1 m` 写入
`meters_per_unit=1.0`。点位、轨迹、Pose CSV 和静态样本会同时保留原始坐标与
`position_m`；这不会把 raw device depth 自动变成米制深度。

KCD2 的原生深度状态是“后端待接入”，不是“实现完成”。在完成实际 depth buffer、
像素对齐和 HUD/透明物体行为验收前，界面会明确阻止深度采集。

## 数据与示例

直接启动时运行数据写入忽略提交的 `capture_studio_data/`；从统一入口启动时写入
忽略提交的 `capture_data/kcd2/`。仓库保留少量可复用样例：

- `examples/scene_points/`：真实场景边界点；
- `examples/scan_plans/`：131 个空间位置、每点 22 视角的五层计划；
- `examples/trajectories/`：160 帧相对随机运镜样例。
- `examples/trajectory_sets/scene_1_fixed_global_max_1000/`：1000 条从低分真实控制点严格递增到同一最高分构图的离线规划集、验证结果和诊断图。

1000 条规划集不代表 KCD2 任意绝对关键帧回放已经完成游戏内验收。连续插值、碰撞安全和运行时帧分数仍需 smoke capture 验证。

## 离线测试

```powershell
cd games\kcd2
$env:PYTHONPATH = "src"
python -m compileall -q kcd2_pose_control.py src tests
python -m unittest discover -s tests -v
```

底层字段和实验记录见 [`docs/camera_reverse_engineering.md`](docs/camera_reverse_engineering.md)。迁移前原说明保存在 [`docs/ORIGINAL_GUIDE.md`](docs/ORIGINAL_GUIDE.md)。
