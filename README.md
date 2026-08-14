<p align="center">
  <img src="docs/assets/hero-multigame.png" alt="Game Camera Capture Lab" width="100%">
</p>

<h1 align="center">Game Camera Capture Lab</h1>

<p align="center">
  <a href="README.md">中文</a> · <a href="README.en.md">English</a>
</p>

<p align="center">
  面向多款游戏的相机位姿、场景点、静态扫描与轨迹采集工具箱
</p>

这不再是一个只服务 RE9 的项目。仓库把每款游戏封装成独立适配器，同时保留统一的启动中心、JSON Schema 和数据组织方式。当前有 3 个适配器，但注册表会自动发现 `games/*/game.json`，未来可以持续扩展到更多游戏。

## Demo

<p align="center">
  <a href="docs/assets/game-camera-capture-demo.mp4">
    <img src="docs/assets/game-camera-capture-demo-preview.gif" alt="KCD2 camera trajectory demo" width="720">
  </a>
</p>

上方预览来自实际 KCD2 自由相机运镜。点击预览可打开 [H.264 完整 Demo](docs/assets/game-camera-capture-demo.mp4)。

## 当前游戏适配器

| 游戏 | 引擎 | 位姿读取 | 绝对位姿控制 | 点位/静态采集 | 轨迹能力 | 成熟度 |
|---|---|---|---|---|---|---|
| RE Engine / RE9 | RE Engine | 已验证 | 已验证 Lua `setPose` | 已验证 | 已验证回放 | 稳定 |
| Kingdom Come: Deliverance II | CryEngine | 已验证 | 游戏画面结果未确认 | 已实现，OBS 联动 | 已验证相对随机运镜；精确回放待确认 | Beta |
| Black Myth: Wukong | Unreal Engine 5 | 需要 UUU Connector 握手 | UUU 相对步进＋Pose 反馈闭环到绝对目标；非原子 `setPose`，游戏内验收待完成 | 已实现 | 原生 Pose 闭环回放，实验性 | 实验性 |

“能读到 pose”“内存写回值变化”和“游戏画面确实到达目标位姿”是三件不同的事。上表只把实际验证过的能力标为已验证，不把 RE9 的能力自动套到其他游戏。

## 界面、规划与数据输出

之前制作的界面图、流程图和数据预览继续作为项目设计的一部分保留。它们主要展示成熟 RE Engine 适配器的完整工作流，也作为后续游戏适配器统一交互的参考。

<table>
  <tr>
    <td width="50%"><img src="docs/assets/interface-overview.png" alt="Capture interface overview"></td>
    <td width="50%"><img src="docs/assets/capture-gui.png" alt="Still scan and trajectory capture GUI"></td>
  </tr>
  <tr>
    <td><b>采集系统总览</b><br>从自由相机、位姿读取到点位、截图和轨迹任务。</td>
    <td><b>静态扫描与轨迹界面</b><br>统一操作点位计划、任务状态和采集进度。</td>
  </tr>
  <tr>
    <td><img src="docs/assets/trajectory-replay.png" alt="Trajectory replay visualization"></td>
    <td><img src="docs/assets/pipeline.png" alt="Capture pipeline"></td>
  </tr>
  <tr>
    <td><b>轨迹回放设计</b><br>关键帧、相机路径和实际位姿反馈分开记录。</td>
    <td><b>完整数据管线</b><br>Pose、OBS、帧对齐、评分与数据集输出。</td>
  </tr>
</table>

![Dataset preview](docs/assets/dataset-preview.png)

旧版 RE9 轨迹动画和所有原始说明也仍保存在 [`docs/RE9_ORIGINAL_GUIDE.md`](docs/RE9_ORIGINAL_GUIDE.md)，没有删除。

## Windows 快速开始

1. 克隆仓库并安装 Python 3.10 或更高版本。
2. 双击 `启动多游戏采集中心.bat`。
3. 在启动中心选择游戏，查看能力状态、说明和示例文件。
4. 第三方相机工具按对应游戏说明放到本地目录；仓库不分发闭源 DLL、UUU、PAK、存档或游戏文件。

也可以直接启动：

```powershell
python launcher\game_capture_hub.py
```

RE9 的完整依赖较多，首次使用请在启动中心运行“安装 RE9 Python 环境”，或执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
```

KCD2 与黑神话适配器仍可使用各自目录内的独立启动脚本。

## Linux 兼容版本

黑神话适配器现在提供 Linux 启动脚本，可运行采集界面的跨平台部分：

```bash
cd games/black-myth-wukong
sudo apt install python3 python3-venv python3-tk  # Debian/Ubuntu 缺少依赖时执行
chmod +x launch_bmw_capture_studio.sh
./launch_bmw_capture_studio.sh
```

也可以在启动时预选轨迹：

```bash
./launch_bmw_capture_studio.sh --trajectory-file /path/to/trajectory.json
```

Linux 版本支持启动界面、JSON/CSV 点位和轨迹管理、离线数据处理、打开输出目录，以及在 Linux 本机运行 OBS 时使用 OBS WebSocket。黑神话的实时 Pose 读取、UUU 注入、Native Bridge 和游戏内相机控制仍需要 Windows，因为 UUU 5.8.21 与当前 Bridge 依赖 Windows 进程注入和命名共享内存。Linux 界面会明确显示兼容模式，不会把界面启动误报为游戏相机已连接；Windows 版本仍使用 `launch_bmw_capture_studio.ps1`。

## 仓库结构

```text
GameCameraCaptureLab/
├─ games/
│  ├─ re9/                       # 清单与说明；成熟实现保留在根目录
│  ├─ kcd2/                      # 独立源码、UI、测试和可公开示例
│  └─ black-myth-wukong/         # UUU 适配器与 Native Bridge 源码
├─ src/
│  ├─ game_camera_capture_lab/   # 动态注册表与多游戏启动中心
│  └─ re9_pose_recorder/         # 既有 RE Engine 采集实现
├─ schemas/                      # 统一 Pose、Point Set、Trajectory JSON Schema
├─ launcher/                     # 无需安装即可运行的启动入口
├─ data/                         # RE9 点位、扫描计划与代表性轨迹
├─ configs/                      # RE9 平台与扫描配置
├─ docs/                         # 架构、格式、扩展和原始 RE9 指南
└─ tests/                        # 根项目与注册表测试
```

每个游戏适配器都拥有自己的源码、测试、运行数据目录和能力声明。启动中心不会写死游戏数量，也不会通过名称分支判断游戏。

## 统一数据格式

仓库定义了三个可跨游戏交换的版本化格式：

- `camera-pose/v1`：单个位姿；
- `camera-point-set/v1`：场景点位集合；
- `camera-trajectory/v1`：带时间的轨迹关键帧。

Schema 位于 [`schemas/`](schemas/)，可直接查看的跨游戏示例位于 [`schemas/examples/`](schemas/examples/)。各适配器可以继续读取原生格式，再通过转换器映射到统一格式；坐标系、角度单位和游戏 ID 必须显式记录，禁止默默猜测。

更多说明见 [文件格式](docs/FILE_FORMATS.md)。

## 新增游戏

新增适配器不需要修改启动中心：

1. 创建 `games/<game-id>/game.json`；
2. 放入独立源码、启动脚本、测试和少量可公开示例；
3. 如实声明 pose、绝对控制、静态采集和轨迹回放的验证状态；
4. 运行注册表检查和测试。

完整约定见 [ADDING_A_GAME.md](docs/ADDING_A_GAME.md) 和 [ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 开发与验证

```powershell
$env:PYTHONPATH = "src"
python -m game_camera_capture_lab.validate
python -m compileall -q src launcher games
python -m unittest discover -s tests -v
python -m unittest discover -s games\kcd2\tests -v
python -m unittest discover -s games\black-myth-wukong\tests -v
```

KCD2 和黑神话测试运行时，需要分别把对应 `games/<id>/src` 加入 `PYTHONPATH`；仓库验证脚本会在发布前按适配器隔离运行。

## 分发边界

本仓库只提交自主编写的源码、配置模板、格式定义、测试和小型示例数据。以下内容被明确排除：

- 商业游戏文件、个人存档与完整进度存档；
- KCD2 Camera Tools、UUU 等闭源第三方二进制；
- 游戏 Mod/PAK，除非后续确认有明确再分发许可；
- 截图、长视频、运行日志、模型缓存和完整采集数据集。

Demo 视频由项目实际采集流程生成，是文档素材，不是游戏或第三方工具的再分发包。

## 历史兼容

仓库由原 `RE9_Still_Scan` 演进而来。为避免破坏已有脚本和 Windows 长路径，RE9 的成熟实现暂时保留在根目录；旧版详细首页归档在 [RE9_ORIGINAL_GUIDE.md](docs/RE9_ORIGINAL_GUIDE.md)。新功能与项目品牌统一使用 **Game Camera Capture Lab**。

## 适配器指南

- [RE9 / RE Engine](games/re9/README.md) · [English](games/re9/README.en.md)
- [天国拯救 2 / KCD2](games/kcd2/README.md) · [English](games/kcd2/README.en.md)
- [黑神话：悟空 / Black Myth: Wukong](games/black-myth-wukong/README.md) · [English](games/black-myth-wukong/README.en.md)
