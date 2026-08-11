# Black Myth: Wukong adapter

[中文](README.md) · [English](README.en.md)

黑神话适配器是在 UUU 外部运行的轻量采集界面。它读取 Connector 共享内存中的相机位姿，管理 JSON/CSV 点位与轨迹，并通过 UUU 默认热键做低频闭环移动和截图。

## 能力边界

| 能力 | 当前状态 |
|---|---|
| UUU 自由相机 | UUU 正确注入后可用 |
| XYZ、四元数、Yaw/Pitch/Roll、FOV | 需要 Native Bridge、Connector 握手和有效 Pose |
| 点位 Load/Export、当前画面截图 | 已实现 |
| 按点位/轨迹文件采集 | 已实现控制流程，需当前游戏版本实测校准 |
| 原生绝对 `setPose` | UUU 5.8.21 未公开，当前不可用 |
| 电影级逐帧精确轨迹 | 尚未实现；当前为热键反馈式相对控制 |

仅看到 DLL 已加载并不代表位姿链路已经打通。界面只有在 Bridge 版本、游戏 PID、UUU 握手、有效 Pose 和 Camera ON 全部通过后才启用采集。

## 使用顺序

1. 启动游戏并进入画面，推荐无边框窗口模式。
2. 双击 `启动黑神话采集工具.bat`。
3. 在界面中选择本机 UUU 目录并执行“准备位姿桥”。
4. 再打开 UUU、选择当前游戏进程并 Inject。
5. 回到游戏按 `Insert` 启用自由相机，等待界面出现实时 Pose。

如果 UUU 先于位姿桥注入，当前会话无法补握手；需要彻底退出游戏后按正确顺序重试。

## Native Bridge

仓库只提交 Bridge 源码：

```text
native/
├─ BmwUuuPoseBridge.cpp
├─ CMakeLists.txt
└─ build_bridge.ps1
```

构建产物 `native/build/Release/IgcsConnector.addon64` 不会提交。UUU 本体与 Connector 也不随仓库分发。

## 数据与测试

运行数据写入忽略提交的 `capture_data/`，公开的点位和轨迹格式示例位于 `examples/`。

```powershell
cd games\black-myth-wukong
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

迁移前原说明保存在 [`docs/ORIGINAL_GUIDE.md`](docs/ORIGINAL_GUIDE.md)。
