# Architecture

Game Camera Capture Lab 采用“共享契约 + 独立游戏适配器”的结构。共享层负责发现、展示和验证适配器，不负责假设任何游戏都支持同一种注入、坐标系或相机写入方式。

## 层次

```text
Multi-game launcher
        │ discovers games/*/game.json
        ▼
Game adapter boundary
        │
        ├─ RE Engine: REFramework Lua control/status files
        ├─ KCD2: Camera Tools + IGCS Client + Windows process bridge
        ├─ Black Myth: UUU + Connector shared memory + hotkey feedback
        └─ Future adapters: their own verified bridge
        │
        ▼
Versioned pose / point-set / trajectory files
        │
        ▼
OBS, screenshots, videos, manifests, analysis and datasets
```

## 动态注册表

`core/src/game_camera_capture_lab/registry.py` 扫描 `games/*/game.json`。启动中心不包含固定游戏列表，因此新增适配器无需修改 UI 源码。

一个清单必须声明：

- 稳定 ID、显示名称、游戏引擎与成熟度；
- 说明和示例路径；
- 每项能力的明确状态字符串；
- 一个或多个平台相关启动动作。

清单中的路径必须留在仓库内部，命令不通过 shell 字符串拼接执行。`{repo}`、`{game}` 和 `{python}` 是唯一支持的运行时占位符。

## 适配器隔离

KCD2 与黑神话都保留自己的 `pyproject.toml`、源码目录、测试和运行数据根目录。这样可以：

- 避免一款游戏的闭源工具路径污染其他适配器；
- 允许不同 Python 依赖和 Native Build；
- 分开验证“读取位姿”“相对控制”“绝对控制”和“数据采集”；
- 在某个游戏更新后单独降级其成熟度，而不影响其他游戏。

RE9 是历史例外：成熟实现暂留根目录，以保持已有配置、脚本和超长轨迹路径兼容。`games/re9/game.json` 只负责把它注册到统一入口。

## 能力状态原则

每个适配器至少应独立判断以下链路：

1. 自由相机可用；
2. 位姿可读且字段含义明确；
3. 绝对位姿写入能改变最终渲染画面；
4. 点位与静态截图可自动执行；
5. 轨迹能按预期时间和位姿回放；
6. 输出图像和 metadata 足以构成数据集。

任何一层未验证，都必须在 `game.json` 和游戏说明中保留边界，不能用下一层代码存在来替代游戏内验收。

## 运行数据

运行时生成的大文件留在每个适配器自己的忽略目录中。仓库只提交：

- 源码与测试；
- 配置模板；
- Native Bridge 源码；
- 少量去敏示例点位、扫描计划和轨迹；
- 版本化格式与文档。
