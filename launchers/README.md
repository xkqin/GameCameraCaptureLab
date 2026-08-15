# 启动入口 / Launchers

这里集中放置项目的启动入口，避免把批处理文件和核心代码混在仓库根目录。

| 文件 | 用途 |
|---|---|
| `启动多游戏采集中心.bat` | 打开多游戏适配器选择中心 |
| `启动统一游戏相机采集器.bat` | 自动识别正在运行的受支持游戏 |
| `launch_unified_capture_studio.ps1` / `.sh` | Windows / Linux-Proton 统一启动器 |
| `launch_hub.ps1` + `game_capture_hub.py` | 通用注册表与适配器中心 |

从仓库根目录执行：

```powershell
python launchers\game_capture_hub.py
```

```bash
bash launchers/launch_unified_capture_studio.sh <profile-id>
```

The launcher directory contains the same entry points for English-speaking environments. Use the `.ps1` or `.sh` file directly, or run the bilingual Windows `.bat` files. The launchers resolve the repository root themselves, so they can be started from any working directory.
