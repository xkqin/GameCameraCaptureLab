# 黑神话：悟空 UUU 相机采集工具

一个放在 UUU 外部的简洁采集界面。仓库直接维护可编译的 Native Bridge 源码，通过已验证的 UUU 5.8.21 进程内相机 ABI 控制相机；不会篡改或重新分发 UUU 闭源 DLL。

## 已实现

- 从 UUU 的 `CameraToolsData` 读取真实 XYZ、四元数、Yaw/Pitch/Roll、FOV。
- 记录当前点位，导出及 Load JSON/CSV 点位文件。
- 按 Load 的点位顺序移动相机，并截取游戏客户区原图。
- Load 轨迹文件，按轨迹样本顺序移动和采集。
- 每次采集输出图片、`manifest.json` 和 `manifest.csv`，同时保存目标位姿与实际位姿。

## 使用顺序

1. 启动《黑神话：悟空》，推荐使用无边框窗口模式。
2. 双击 `启动黑神话采集工具.bat`。
3. 点击“1 准备位姿桥”。
4. 点击“2 打开 UUU”，在 UUU 中选择游戏进程并点击 Inject。
5. 回到游戏按 `Insert` 启用自由相机。
6. 界面出现实时 Pose 后，即可记录点位、导出/Load，以及批量采集。

程序现在会验证 Connector 握手，而不只是检查两个 DLL 名称。如果检测到 UUU 早于位姿桥注入，会直接阻止补注入并提示彻底重启游戏，避免显示“已加载”但永远没有 Pose。采集按钮只有在桥版本、游戏 PID、UUU 握手、有效 Pose 和 Camera ON 全部通过后才会启用。

关闭游戏后必须等黑神话进程完全退出；仅回到主菜单、关闭 UUU Client 或重复按 `Insert` 不会重建 Connector 握手。

## 文件位置

- 点位：`capture_data\point_files`
- 轨迹：`capture_data\trajectory_files`
- 采集结果：`capture_data\captures`
- 位姿桥：`native\build\Release\IgcsConnector.addon64`

支持的 JSON 数组键包括 `points`、`keyframes`、`frames`、`samples`，CSV 使用同名字段。角度单位为度。

## 当前边界

UUU 5.8.21 没有公开单函数式的绝对 `setPose` API，但它导出截图会话控制，并在进程内提供完整相机虚函数。当前 Native Bridge 已锁定 5.8.21 的内部 ABI，直接调用前后、左右、上下、Yaw、Pitch、Roll、FOV 方法，再根据实时 Pose 闭环到绝对目标。这是“绝对目标＋相对步进”，不是一次内存写入的原子瞬移。当前闭环先收敛 XYZ，再收敛朝向和 FOV，并等待新 Pose 反馈后才发送下一步，以减少过冲和抖动。其他 UUU 版本会拒绝启用内部控制。代码、编译和离线测试已通过；在正式标记为稳定前，仍需完成游戏内远距离到达、最终画面稳定和轨迹平滑度验收。

当前源码已按本机 UUU 5.8.21 二进制核对 `GameSpecific::Camera` 的前后、左右、上下、Yaw、Pitch、Roll、FOV 虚函数槽位。控制不再发送数字键盘或方向键；`Insert` 和 `Home` 仍由用户负责切换 UUU Camera 与 Camera Lock。若抓图为黑屏，请把游戏切换为无边框窗口模式。

## 开发验证

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```
