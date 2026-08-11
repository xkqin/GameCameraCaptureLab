# Kingdom Come: Deliverance II adapter

KCD2 适配器提供独立 Tkinter 界面，用于相机 DLL 注入顺序管理、实时 pose、场景边界点、分层扫描计划、OBS 采集与轨迹实验。

## 能力边界

| 能力 | 当前状态 |
|---|---|
| Camera Tools 自由相机 | 已在游戏中验证 |
| XYZ、四元数、Yaw/Pitch/Roll、FOV | 已验证可读 |
| 连续 pose CSV、点位与扫描计划 | 已验证 |
| OBS 当前位姿截图和录制清单 | 已实现，需要 OBS WebSocket |
| 20 Hz 相对随机运镜与 seed 复现 | 已验证 |
| 任意绝对 `setPose` 改变最终游戏画面 | 尚未确认 |
| 任意 JSON 关键帧精确回放 | 代码路径存在，仍取决于绝对控制验收 |

内存字段写回后读值变化，不等于渲染相机确实移动。因此适配器不会把绝对轨迹回放标成已完成。

## 本地准备

闭源 Camera Tools 不在仓库中。把你合法取得的 v1.0.5 文件放到：

```text
games/kcd2/camera_tools/
├─ KCD2CameraTools.dll
├─ IGCSClient.exe
└─ 其余原工具文件
```

游戏进入可操作画面后，双击 `启动KCD2采集工具.bat`，再从“系统与实时 Pose”页执行一键准备与注入。IGCS Client 必须先于相机 DLL 启动；失败会话需要彻底重启游戏。

## 数据与示例

运行数据只写入忽略提交的 `capture_studio_data/`。仓库保留少量可复用样例：

- `examples/scene_points/`：真实场景边界点；
- `examples/scan_plans/`：131 个空间位置、每点 22 视角的五层计划；
- `examples/trajectories/`：160 帧相对随机运镜样例。

## 离线测试

```powershell
cd games\kcd2
$env:PYTHONPATH = "src"
python -m compileall -q kcd2_pose_control.py src tests
python -m unittest discover -s tests -v
```

底层字段和实验记录见 [`docs/camera_reverse_engineering.md`](docs/camera_reverse_engineering.md)。迁移前原说明保存在 [`docs/ORIGINAL_GUIDE.md`](docs/ORIGINAL_GUIDE.md)。
