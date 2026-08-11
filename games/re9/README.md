# RE Engine / RE9 adapter

[中文](README.md) · [English](README.en.md)

这是 Game Camera Capture Lab 中最成熟的游戏适配器。为保持既有脚本、配置和长轨迹文件的兼容性，RE9 的实现仍位于仓库根目录：

- Python 源码：`src/re9_pose_recorder/`
- Lua 与平台配置：`configs/`、`data/scene_2_capture/`
- 静态扫描点位：`data/scene_points/`
- 轨迹与导出：`data/trajectories/`、`data/trajectory_exports/`
- Windows/Linux 启动脚本：`scripts/`

完整说明见 [`../../docs/USER_GUIDE_ZH.md`](../../docs/USER_GUIDE_ZH.md)，重构前的详细首页保存在 [`../../docs/RE9_ORIGINAL_GUIDE.md`](../../docs/RE9_ORIGINAL_GUIDE.md)。

此适配器已验证读取相机位姿、通过 Lua 设置绝对位姿、OBS 截图、分层扫描和轨迹回放。它的能力不能自动推定到其他游戏适配器。
