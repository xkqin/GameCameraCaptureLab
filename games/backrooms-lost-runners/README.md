# Backrooms Lost Runners 相机采集适配器

[English](README.en.md)

这是自研 UE Camera Runtime 的第二个游戏 Profile。目标构建为 UE 5.6：

- 进程：`BackroomsLostRunners-Win64-Shipping.exe`
- ProductVersion：`++UE5+Release-5.6-CL-44394996`
- 已确认三个 LWC `FMinimalViewInfo` 复制位置，ABI 与现有 Runtime Adapter 一致；
- Profile 要求恰好命中三个位置，否则拒绝安装 Hook；
- HUD Hook 尚未适配，Delete 隐藏 HUD 暂不可用。

2026-08-15 已完成首次实机 Runtime 验收：

- DLL 注入、三个 Hook、持续 Pose 和输入独占能力均正常；
- 绝对 `setPose` 位移约 206 UE 单位并旋转 20°，观测位置误差为 0；
- 相对移动 100 UE 单位、旋转 5°，观测结果与目标一致；
- 1.2 秒三关键帧进程内轨迹正常完成并保持终点，位置误差约 `1.74e-5` UE 单位；
- 每项测试后均成功恢复初始 Pose。

## 首次验收

1. 启动游戏并进入正常可渲染场景。
2. 从多游戏采集中心选择“打开 Backrooms 采集界面”。
3. 点击“注入 Camera Bridge”，先保持 Camera OFF，确认 Pose 持续更新。
4. 按 Insert 启用自由相机，验证鼠标、WASD/QE 和 Shift 加速。
5. 在界面记录当前 Pose，移动较远后执行绝对 `setPose`，确认画面直接返回目标。
6. 最后分别验收 22 方向静态采集和一条短轨迹。

Runtime Pose、绝对控制和轨迹已经通过；OBS 静态图片/录像与键鼠主观手感仍应由用户完成一次可见验收，因此当前成熟度为 `beta`。

游戏更新后应重新运行离线 Profile 检查；签名数量不再为三个时不得强制注入。
