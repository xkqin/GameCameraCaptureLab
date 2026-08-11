# 黑神话：悟空 UUU 相机采集工具

一个放在 UUU 外部的简洁采集界面。它不会修改你本机 `UUU_v5.8.21` 目录中的闭源文件。

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

UUU 5.8.21 没有向外公开绝对 `setPose` API。当前控制层使用 UUU 默认热键，并根据实时 Pose 做低频闭环，因此适合逐点照片和较稀疏轨迹样本采集。它还不是原生每帧绝对位姿写入，不能保证电影级平滑实时轨迹；这部分需要后续增加原生写入桥并在游戏内实测偏移。

默认键位方向、移动速度和角度方向仍需在当前黑神话版本中做一次实机校准。若抓图为黑屏，请把游戏切换为无边框窗口模式。

默认 UUU 键位为：`Insert` 开关相机、`Home` 锁定/解锁相机移动、数字键盘 `8/5/4/6/7/9` 移动、方向键旋转。程序读取的是 `IGCSClientSettings.ini` 当前默认绑定对应的虚拟键。

## 开发验证

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```
