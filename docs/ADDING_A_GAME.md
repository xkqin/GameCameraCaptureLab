# Adding a game adapter

启动中心按清单自动发现游戏，适配器数量没有上限。最小目录如下：

```text
games/my-game/
├─ game.json
├─ README.md
├─ pyproject.toml              # 如果使用独立 Python 包
├─ src/my_game_capture/
├─ tests/
├─ examples/
└─ launch_my_game.ps1
```

## 1. 先做能力分层验证

不要从“某个工具有自由相机”直接跳到“可以自动采集轨迹”。按顺序记录证据：

1. 游戏内自由相机能移动；
2. 外部程序能持续读取 XYZ、旋转和 FOV；
3. 写入目标位姿后，游戏最终画面确实变化；
4. 可稳定截图并把实际 pose 写进 metadata；
5. 点位和轨迹能够停止、恢复、重试；
6. 帧时间、图像与 pose 对齐达到数据集要求。

无法确认的能力使用 `experimental`、`requires_*`、`unverified_*` 或 `not_available` 等清楚状态，不要填 `verified`。

## 2. 编写 game.json

可复制已有清单作为起点。必要字段：

```json
{
  "schema_version": 1,
  "id": "my-game",
  "name": "My Game",
  "short_name": "My Game",
  "engine": "Engine Name",
  "maturity": "experimental",
  "summary": "适配器当前实际能力。",
  "documentation": "README.md",
  "examples": "examples",
  "capabilities": {
    "free_camera": "verified",
    "pose_read": "experimental",
    "absolute_pose_control": "not_available"
  },
  "actions": [
    {
      "id": "capture",
      "label": "打开采集界面",
      "description": "启动独立 UI。",
      "platforms": ["windows"],
      "working_directory": ".",
      "command": ["{python}", "-m", "my_game_capture"]
    }
  ]
}
```

支持的命令占位符：

- `{repo}`：仓库根目录；
- `{game}`：当前游戏适配器目录；
- `{python}`：启动中心正在使用的 Python。

所有相对路径按 `game.json` 所在目录解析，并且不得逃出仓库。

## 3. 隔离运行数据和第三方文件

运行截图、视频、日志、模型和缓存应进入适配器自己的忽略目录。闭源相机工具、注入器、游戏 Mod、PAK 和存档默认不提交；只有明确拥有再分发权时才单独评估。

## 4. 提供统一格式转换

原生读写可以保留，但建议至少提供导出到：

- `camera-pose/v1`；
- `camera-point-set/v1`；
- `camera-trajectory/v1`。

务必显式写坐标系和单位。转换示例见 [`FILE_FORMATS.md`](FILE_FORMATS.md)。

## 5. 验证

```powershell
$env:PYTHONPATH = "src"
python -m game_camera_capture_lab.validate
python -m compileall -q games\my-game
```

再运行适配器自己的单元测试和一次游戏内可见验收。只有游戏画面、输出文件和 pose metadata 同时正确，才把能力提升为 `verified`。
