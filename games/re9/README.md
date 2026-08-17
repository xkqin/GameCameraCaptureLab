# RE Engine / RE9 adapter

[中文](README.md) · [English](README.en.md)

这是 Game Camera Capture Lab 中最成熟的游戏适配器。为保持既有脚本、配置和长轨迹文件的兼容性，RE9 的实现仍位于仓库根目录：

- Python 源码：`core/src/re9_pose_recorder/`
- Lua 与平台配置：`core/configs/`、`data/scene_2_capture/`
- 静态扫描点位：`data/scene_points/`
- 轨迹与导出：`data/trajectories/`、`data/trajectory_exports/`
- Windows/Linux 启动脚本：`scripts/`

完整说明见 [`../../docs/USER_GUIDE_ZH.md`](../../docs/USER_GUIDE_ZH.md)，重构前的详细首页保存在 [`../../docs/RE9_ORIGINAL_GUIDE.md`](../../docs/RE9_ORIGINAL_GUIDE.md)。

此适配器已验证读取相机位姿、通过 Lua 设置绝对位姿、OBS 截图、分层扫描和轨迹回放。它的能力不能自动推定到其他游戏适配器。

## 可选逐像素深度采集（实验性）

静态采集 UI 已加入 `Capture per-pixel depth (3DGS)`。默认关闭时，原有 RGB 和 22 方向采集完全不变；开启后，每张 RGB 必须成功配对一张同分辨率的逐像素深度图，否则该张 RGB 会被删除且不会写入 `samples.csv`。

先构建并安装 REFramework D3D12 插件：

```powershell
cd games\re9\native\re9-depth-bridge
.\build.ps1
.\install.ps1 -GameDirectory "D:\steam\steamapps\common\RESIDENT EVIL requiem BIOHAZARD requiem"
```

重启 RE9 后，等待 UI 显示 `Depth plugin: ready (..., D3D12)` 再勾选深度采集。每张 RGB 会生成 `depth/*.npy`、原始 GPU 深度、预览图、有效像素 mask 和相机元数据；`.npy` 是以米为单位的 `height x width` 线性 view-space Z。旧 RGB-only 数据集断点续采时，程序会先备份并扩展 CSV 表头，不会删除旧记录。

源码、输出格式和实机验收步骤见 [`native/re9-depth-bridge/README.md`](native/re9-depth-bridge/README.md)。原生插件已离线构建和测试，但 RE9 版本更新后仍必须重新完成墙面 1-unit 位移与 RGB/深度边缘对齐验收。
