<p align="center">
  <img src="docs/assets/hero-multigame.png" alt="Game Camera Capture Lab" width="100%">
</p>

<h1 align="center">Game Camera Capture Lab</h1>

<p align="center">
  <strong>把离线游戏变成可控制、可复现、可批量采集的视觉数据环境</strong>
</p>

<p align="center">
  <a href="README.md">中文</a> · <a href="README.en.md">English</a>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-16A34A?style=flat-square" alt="MIT License"></a>
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/OBS-WebSocket-302E31?style=flat-square&logo=obsstudio&logoColor=white" alt="OBS WebSocket">
  <img src="https://img.shields.io/badge/UE-Camera_Runtime-0E1128?style=flat-square&logo=unrealengine" alt="UE Camera Runtime">
  <img src="https://img.shields.io/badge/Open_Source-Free-FF4B4B?style=flat-square" alt="Open Source and Free">
</p>

这是一个完整的多游戏相机数据采集项目，不只是自由相机，也不只是截图脚本。它把**相机控制、位姿读取、空间点位、22 方向静态扫描、连续轨迹、OBS 采集、进度恢复和数据清单**连成一条可复现的管线。

项目最初服务于 RE9，现在已重构为动态发现 `games/*/game.json` 的多游戏架构。新增的自研 **UE Camera Runtime** 可以向受支持的离线 UE 游戏注入自由相机、读取真实 Pose、执行绝对 `setPose`、隐藏 HUD，并在游戏进程内平滑播放轨迹。项目自研代码全部开源、免费。

## 它能完成什么

| 层级 | 能力 | 产物 |
|---|---|---|
| 相机层 | 自由移动、鼠标视角、Pose 读取、绝对 `setPose`、FOV、HUD | 可控制且可测量的相机 |
| 空间层 | 手工记录边界、3D 点位图、分层铺点、导入点位文件 | 可复用的场景扫描计划 |
| 静态采集 | 每个空间点自动采集 22 个方向，OBS 输出 1920×1080 JPG | 图片、目标/实测 Pose、manifest |
| 轨迹采集 | 自动加载轨迹、连续回放、录像、批次进度与断点继续 | 视频、关键帧、逐帧 Pose、时间线 |
| 工程层 | 多游戏注册表、统一 Schema、Windows/Linux、报警与恢复 | 可扩展、可审计的数据采集系统 |

## Demo

<p align="center">
  <a href="docs/assets/game-camera-capture-demo.mp4">
    <img src="docs/assets/game-camera-capture-demo-preview.gif" alt="KCD2 camera trajectory demo" width="720">
  </a>
</p>

上方预览来自实际 KCD2 自由相机运镜。点击预览可打开 [H.264 完整 Demo](docs/assets/game-camera-capture-demo.mp4)。

## 自研 UE 自由相机：采集链的新增核心能力

```text
游戏进程
  └─ UeCameraInjector.exe
      └─ UeCameraRuntime.dll
          ├─ Game Profile：进程名、签名、ABI、能力声明
          ├─ Camera Hook：读取 / 覆盖 FMinimalViewInfo
          ├─ Shared-memory ABI：Pose / setPose / HUD / Trajectory
          └─ Python Capture Studio：点位 / OBS / 数据集 / UI
```

### 原理与算法

1. **Profile 驱动定位**：注入器先按进程名选择游戏 profile；离线检查器和运行时只扫描 PE 可执行段，通过带通配符的字节签名寻找相机路径。
2. **安全匹配门**：每个 Hook 都声明允许的最少/最多匹配数。数量不符就拒绝安装，绝不在猜测地址上写内存。
3. **相机 Hook**：当前 UE5 LWC 适配器在已验证的 `FMinimalViewInfo` 复制路径安装 14-byte 绝对跳转，汇编适配器保留寄存器契约，同时观察或覆盖 double 精度 XYZ、Yaw/Pitch/Roll 与 FOV。
4. **无撕裂 Pose 发布**：相机覆盖值使用双缓冲原子切换，精确 Pose 使用序列锁发布，避免 Python/UI 读到一半新、一半旧的帧。
5. **相机局部坐标控制**：由 Yaw/Pitch/Roll 构造 `forward/right/up` 正交基，WASD/QE 的位移在相机坐标系中积分；Shift/Ctrl 改变速度倍率，鼠标增量控制朝向。
6. **原子绝对位姿**：`setPose(x, y, z, yaw, pitch, roll, fov)` 作为一个带序列号的命令提交，运行时在相机帧路径一次性采用完整目标，而不是让 Python 连续模拟按键走过去。
7. **进程内平滑轨迹**：Python 只提交关键帧。运行时使用高精度单调时钟推进时间，对位置、角度和 FOV 执行三次 Hermite 插值；角度先做最短弧展开，结束后保持终点，不回跳起点。

对相邻关键帧，核心插值为：

```text
p(u) = h00(u)p0 + h10(u)Δt·m0 + h01(u)p1 + h11(u)Δt·m1,  u ∈ [0, 1]
```

这让控制循环留在游戏进程中，Python 负责规划和记录，避免逐帧 IPC、脚本调度和磁盘 I/O 把抖动带进运镜。

### 为什么容易扩展到更多 UE5 游戏

我们复用的是整套**注入器、运行时、共享内存协议、轨迹引擎、采集 UI、OBS 管线和数据 Schema**，新游戏只补最薄的一层：

- 如果目标游戏共享现有相机 ABI，通常只需增加一个 profile：进程名、签名、匹配数量和坐标声明；
- 如果相机结构或寄存器契约不同，再增加一个紧凑的 ABI adapter，其余采集系统全部复用；
- 每个 profile 都必须重新通过 Pose、绝对 `setPose`、轨迹画面和截图验收，不能因为“同为 UE5”就盲目启用。

所以，对共享 ABI 的 UE5 游戏，适配可能真的只是改一点点配置；对自定义相机管线的游戏，工作集中在一个小适配层，而不是重写整个项目。当前仓库登记并完成运行时 Pose 验证的首个 UE profile 是《黑神话：悟空》。本功能只面向离线单机和获授权的研究环境，不支持在线或反作弊场景。

## 当前游戏适配器

| 游戏 | 引擎 | 位姿读取 | 绝对位姿控制 | 静态/轨迹采集 | 成熟度 |
|---|---|---|---|---|---|
| RE Engine / RE9 | RE Engine | 已验证 | 已验证 Lua `setPose` | 已验证 | 稳定 |
| Kingdom Come: Deliverance II | CryEngine | 已验证 | 画面结果待完整确认 | OBS 与批量采集已实现 | Beta |
| Black Myth: Wukong | Unreal Engine 5 | 自研 Runtime 已实机验证 | 原子 `setPose` 已实机验证 | 22 方向静态与进程内轨迹已接入 | Beta |
| Backrooms Lost Runners | Unreal Engine 5.6 | 三处 Hook 与实时 Pose 已验证 | 相对控制与原子 `setPose` 已验证 | 进程内轨迹已验证；OBS 等待可见验收 | Beta |

“读到 Pose”“命令被运行时接收”和“游戏画面到达目标”是三层不同的验收。表格不会把一个游戏的结果自动套到另一个游戏。

公开资料已确认另外 14 款热门 UE4/UE5 单机游戏具备自由相机、Camera Path，以及路径节点中的位置/朝向/FOV 信息。它们已经进入严格的候选池，但在完成目标版本签名扫描和本项目运行验收前，不会伪装成已支持适配器。完整分级、风险和下一批优先级见 [游戏相机支持矩阵](docs/GAME_SUPPORT_MATRIX.md)。

## 界面、规划与数据输出

原项目的界面图、轨迹图、流程图和数据预览全部保留，它们仍然是这套采集工具主线的一部分。

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
    <td><b>轨迹回放设计</b><br>关键帧、相机路径和实测 Pose 分开记录。</td>
    <td><b>完整数据管线</b><br>Pose、OBS、帧对齐、评分与数据集输出。</td>
  </tr>
</table>

![Dataset preview](docs/assets/dataset-preview.png)

旧版 RE9 轨迹动画和详细说明继续保存在 [RE9_ORIGINAL_GUIDE.md](docs/RE9_ORIGINAL_GUIDE.md)。

## 快速开始

```powershell
git clone https://github.com/xkqin/GameCameraCaptureLab.git
cd GameCameraCaptureLab
python launchers\game_capture_hub.py
```

Windows 可以直接双击 `launchers\启动多游戏采集中心.bat` 选择任意适配器；UE 游戏也可以双击 `launchers\启动统一游戏相机采集器.bat`，它会自动识别唯一正在运行的已支持游戏。未检测到游戏时，采集器进入统一自动识别等待模式；同时检测到多个游戏时，才要求用户明确选择。命令行可使用 `launchers\launch_unified_capture_studio.ps1 -GameId <profile-id>`。各游戏的准备条件、快捷键和验证状态见文末适配器指南。

Linux/Proton 可用 `bash launchers/launch_unified_capture_studio.sh <profile-id>` 启动同一套采集 UI，并支持点位/轨迹文件、OBS WebSocket 和回环 Relay；注入器与 Runtime 仍需在游戏所属 Proton 前缀中运行。未配置实时链路时，界面只进入离线/等待状态，不会伪造已连接。

## 采集界面控制

统一采集器启动后，运行时控制区提供一个语言下拉框，可在 `中文` 和 `English` 之间直接切换，不需要重启。主界面、动态状态、进度文本以及“通知与自动修复”设置指南会同步切换到所选语言。

同一区域保留“置顶采集窗口”按钮，默认关闭；需要边玩边操作时再手动开启，状态会写入本地设置。`Delete` 可切换游戏 HUD，WASD/QE、鼠标和 `Shift` 等相机控制仍由各适配器按键表定义。没有检测到游戏时，统一入口会停留在自动识别等待模式；检测到多个受支持游戏时才要求选择目标。

## 飞书与 Discord 设置

统一采集界面的通知区内置中文/English 设置指南，以及“测试飞书”和“测试 Discord”按钮。推荐把 `core/configs/windows.yaml` 复制成 Git 忽略的 `core/configs/windows.local.yaml`，然后填写：

```yaml
notifications:
  discord:
    webhook_url: "https://discord.com/api/webhooks/..."
    mention: ""
    username: "Unified Camera Capture"
    timeout_sec: 5
  feishu:
    webhook_url: "https://open.feishu.cn/open-apis/bot/v2/hook/..."
    secret: ""
    mention_open_id: ""
    timeout_sec: 5
```

也可用 `UNIFIED_DISCORD_WEBHOOK_URL`、`UNIFIED_FEISHU_WEBHOOK_URL`、`UNIFIED_FEISHU_SECRET`；自定义 YAML 路径用 `UNIFIED_CAMERA_CONFIG`。旧 RE9/BMW 变量继续兼容，但 Unified 变量优先。真实 Webhook、Secret、token 和本地配置禁止提交。

## 数据与仓库结构

统一格式位于 [`core/schemas/`](core/schemas/)：

- `camera-pose/v1`：XYZ、旋转、FOV、坐标系与单位；
- `camera-point-set/v1`：空间点位、场景和采集元数据；
- `camera-trajectory/v1`：带时间的轨迹关键帧；
- `ue_camera_profile_v1`：UE 进程、Hook 签名、ABI 和能力声明。
- `game_support_catalog/v1`：公开相机证据、本项目运行时验收和排除风险分层。

```text
GameCameraCaptureLab/
├─ core/                        # 共享代码、配置、Schema、Runtime、测试和第三方缓存
│  ├─ src/                      # 启动中心、注册表与成熟 RE9 采集实现
│  ├─ configs/                  # 平台、OBS、报警与自动恢复模板
│  ├─ schemas/                  # 跨游戏 Pose、Point、Trajectory、UE Profile
│  ├─ catalogs/                 # 游戏支持证据目录
│  ├─ runtime/                  # UE profile、扫描器和通用注入器源码
│  └─ tests/                    # 共享层离线测试
├─ games/                       # RE9、KCD2、Black Myth 与未来适配器
├─ data/                        # 点位、扫描计划和代表性轨迹
├─ docs/                        # 架构、格式、图片和历史指南
├─ launchers/                   # 统一入口、自动识别和多游戏中心
├─ scripts/                     # 采集、分析、媒体和维护脚本
└─ outputs/                    # 可复现的轻量分析输出
```

新增普通游戏适配器见 [ADDING_A_GAME.md](docs/ADDING_A_GAME.md)；UE 相机 profile 见 [UE Camera Runtime](core/runtime/ue-camera-runtime/README.md)。

## 开发与验证

```powershell
$env:PYTHONPATH = "core/src"
python -m game_camera_capture_lab.validate
python -m unittest discover -s core/tests -v

$env:PYTHONPATH = "core/src;games\black-myth-wukong\src"
python -m unittest discover -s games\black-myth-wukong\tests -v
```

发布前还会检查 profile Schema、Hook 匹配数量、原生构建和 Git diff。游戏升级后若签名失效，Runtime 会拒绝 Hook，必须重新审计 profile。

## 开源、免费与分发边界

本项目自研源码以 [MIT License](LICENSE) 开源，任何人都可以免费学习、修改和扩展。仓库只提交自主编写的源码、配置模板、Schema、测试和小型示例，不包含商业游戏文件、存档、第三方闭源相机工具、未经许可的 Mod/PAK、密钥、运行日志或完整采集数据集。

## 适配器指南

- [RE9 / RE Engine](games/re9/README.md) · [English](games/re9/README.en.md)
- [天国拯救 2 / KCD2](games/kcd2/README.md) · [English](games/kcd2/README.en.md)
- [黑神话：悟空 / Black Myth: Wukong](games/black-myth-wukong/README.md) · [English](games/black-myth-wukong/README.en.md)
- [Backrooms Lost Runners](games/backrooms-lost-runners/README.md) · [English](games/backrooms-lost-runners/README.en.md)
