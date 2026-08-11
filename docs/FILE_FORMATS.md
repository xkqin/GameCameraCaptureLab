# Shared camera file formats

统一格式用于跨游戏的数据交换和后续数据集工具，不要求每个适配器立即放弃自己的原生格式。

## 设计规则

- `schema_version` 必须存在，当前格式不会靠字段猜版本；
- `game_id` 必须与游戏清单一致；
- 坐标系必须明确记录手性、竖直轴、角度单位和位置单位；
- 统一旋转字段使用 `yaw`、`pitch`、`roll`，角度单位由坐标系决定；
- FOV 固定写作 `fov_degrees`，避免与位置旋转的单位推断混在一起；
- 四元数可选，但只要存在就使用 `x/y/z/w`；
- 游戏特有字段放入 `metadata`，不要污染共享字段。

## camera-pose/v1

单个位姿文档：

```json
{
  "schema_version": "camera-pose/v1",
  "game_id": "example-game",
  "coordinate_system": {
    "handedness": "unknown",
    "vertical_axis": "z",
    "angle_unit": "degrees",
    "position_unit": "game_units"
  },
  "pose": {
    "position": {"x": 1.0, "y": 2.0, "z": 3.0},
    "rotation": {"yaw": 10.0, "pitch": -2.0, "roll": 0.0},
    "fov_degrees": 63.0
  }
}
```

完整定义：[`../schemas/camera_pose_v1.schema.json`](../schemas/camera_pose_v1.schema.json)。

## camera-point-set/v1

点位集合包含 `scene_id` 和 `points[]`。每个点至少有稳定 `id` 与 `pose`，可以附带标签、时间和 metadata。

完整定义：[`../schemas/point_set_v1.schema.json`](../schemas/point_set_v1.schema.json)。

## camera-trajectory/v1

轨迹包含 `trajectory_id` 和按 `time_sec` 排列的 `keyframes[]`。关键帧的 `index` 从 0 开始；转换器应保证时间非递减。

格式只表示目标路径，不代表所有适配器都能绝对回放。KCD2 或 UUU 适配器可以读取同一轨迹做规划或相对控制，同时在运行清单中记录实际达到的 pose。

完整定义：[`../schemas/trajectory_v1.schema.json`](../schemas/trajectory_v1.schema.json)。

## 原生格式兼容

现有适配器仍接受历史字段：

- RE9：`yaw/pitch/fov` 和 `trajectories[].keyframes`；
- KCD2：扁平 `x/y/z`、`q0..q3`、`*_degrees`；
- Black Myth：`points/keyframes/frames/samples` 以及 `qx/qy/qz/qw`。

转换时必须显式指定四元数顺序和角度单位，不能只根据数值大小永久推断。

## 示例

跨游戏示例位于 [`../schemas/examples/`](../schemas/examples/)。游戏原生、可直接供现有 UI 读取的示例则放在各自的 `games/<id>/examples/` 或 RE9 根目录 `data/` 下。
