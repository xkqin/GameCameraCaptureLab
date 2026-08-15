# 游戏相机支持矩阵（2026-08-15）

[English](GAME_SUPPORT_MATRIX.en.md)

这份矩阵回答两个不同问题：

1. 网上是否有可靠证据证明该游戏能使用自由相机、Camera Path，并在路径节点保存相机位置、朝向和 FOV；
2. 本项目是否已经为该游戏完成 Pose 读取、原子绝对 `setPose`、轨迹和截图的运行时验收。

二者不能混为一谈。公开相机兼容记录可以证明游戏适合进入适配队列，但不能自动生成本项目需要的进程名、Hook 签名、寄存器契约和匹配数量。

## 现在可以确认到什么程度

| 层级 | 结论 | 游戏 |
|---|---|---|
| 本项目运行时已验证 | 已有本项目 profile，并完成实时 Pose/控制验收；截图链仍按各自 manifest 标注 | Black Myth: Wukong、Backrooms Lost Runners |
| 公开自由相机已验证 | 自由相机、Camera Path 和路径节点中的 Location/Orientation/FOV 有公开证据；本项目原生 profile 待做 | Hellblade II、Silent Hill 2 Remake、The Talos Principle 2、Layers of Fear (2023)、RoboCop: Rogue City、Still Wakes the Deep、Clair Obscur: Expedition 33、Immortals of Aveum、Nobody Wants to Die、Remnant II、The 7th Guest Remake、Lies of P、Hogwarts Legacy、Star Wars Jedi: Survivor |
| 明确不纳入 | 需要停用反作弊或属于在线/反作弊场景 | Lords of the Fallen (2023)、Fortnite |

## 最适合作为下一批原生适配的游戏

优先级不是按游戏名气排，而是按“离线单机、公开相机证据清楚、没有已知反作弊阻断、UE5 现有 ABI 复用概率”排序。

| 优先级 | 游戏 | 已确认 | 本项目还缺什么 |
|---|---|---|---|
| P0 | The Talos Principle 2 | UE5 公开兼容、自由相机、路径节点 Pose/FOV | 对目标版本 EXE 做只读签名扫描；验证是否复用 LWC ABI |
| P0 | Layers of Fear (2023) | UE5 公开兼容、自由相机、路径节点 Pose/FOV | 同上 |
| P0 | Still Wakes the Deep | UE5 公开兼容、自由相机、路径节点 Pose/FOV | 同上 |
| P0 | RoboCop: Rogue City | UE5 公开兼容；文档注明最低兼容版本 | 同上，并核对更新后的匹配数量 |
| P1 | Senua's Saga: Hellblade II | UE5 公开兼容；有镜头与画幅注意事项 | 相机复制 ABI、黑边/HUD 与画面验收 |
| P1 | Silent Hill 2 Remake | UE5 公开兼容；Actor Pose Editor 缺失不影响相机 Pose 结论 | 原生 Pose/绝对 setPose 仍需单独验证 |
| P1 | Clair Obscur: Expedition 33 | UE5 公开兼容；部分场景鼠标旋转受限 | 输入隔离、过场和战斗中的相机 Hook 验收 |
| P2 | Lies of P / Hogwarts Legacy / Jedi: Survivor | UE4 公开兼容、路径节点 Pose/FOV | 新增 UE4 非 LWC ABI adapter，不能直接套 UE5 profile |

## 证据边界

- UE5 公开兼容文档明确说明：工具并非对所有 UE5 游戏无条件生效，开发者修改引擎代码后，关键功能可能无法重新定位。
- 同一文档明确说明 Camera Path 节点记录 camera location、orientation 和 field of view；这证明可获得相机状态，但不等于本项目的共享内存协议已经接通。
- 公开 Connector 文档可以进一步证明实时 world position、quaternion、view matrix、Pitch/Yaw/Roll 能被导出，但该接口明确是只读。它不能证明存在外部 `setPose(x, y, z, yaw, ...)`；本项目的绝对 `setPose` 仍必须由自研运行时逐游戏验收。
- UE4 公开兼容文档对 Lies of P、Hogwarts Legacy、Star Wars Jedi: Survivor 标记为可用，并提供各自注意事项。
- GitHub 上存在 BSD-2-Clause 的游戏级相机参考源码，但主要覆盖旧游戏和旧版本。它适合研究 ABI 与 Hook 架构，不足以证明今天的零售版仍然匹配。
- 任何新游戏只有依次通过 `signature count -> live Pose -> rendered absolute setPose -> smooth trajectory -> OBS image/manifest`，才会从候选目录升级为本项目适配器。

## 在线来源

- [UE5 自由相机兼容表、Camera Path 与状态字段说明](https://opm.fransbouma.com/uuuv5.htm)
- [UE4 自由相机兼容表、Camera Path 与状态字段说明](https://opm.fransbouma.com/uuuv4.htm)
- [公开 Connector 的实时相机数据字段（只读）](https://github.com/FransBouma/IgcsConnector#camera-data-made-available-to-reshade-shaders)
- [BSD-2-Clause 游戏相机参考源码](https://github.com/FransBouma/InjectableGenericCameraSystem)

## 查询机器可读目录

```powershell
$env:PYTHONPATH = "src"
python -m game_camera_capture_lab.support_catalog
python -m game_camera_capture_lab.support_catalog --level public_free_camera_verified
python -m game_camera_capture_lab.support_catalog --json
```

数据源位于 [`catalogs/game_support_catalog_v1.json`](../catalogs/game_support_catalog_v1.json)。目录把“公开自由相机可用”和“本项目原生运行时已验证”做成两个互斥证据等级，避免后续 README、UI 或自动脚本把候选游戏误报成已经支持。
