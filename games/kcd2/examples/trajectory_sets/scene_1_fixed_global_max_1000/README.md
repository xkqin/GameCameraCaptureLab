# KCD2 Scene 1 — 1000 条固定最高点离线轨迹规划集

这个目录单独保存 KCD2 Scene 1 的 1000 条低分到高分轨迹。每个规划控制点都对应原始扫描中的真实评分 pose，控制点分数严格递增，所有轨迹最终到达同一个全局最高分 pose。

## 结果摘要

- 1000/1000 条规划验证通过；
- 1000 个唯一物理 XYZ 几何签名；
- 100 个物理起点、8 个最终入口；
- 固定终点：sample 1805 / point 83；
- 固定终点分数：7.101753；
- score gain：最低 2.457797、平均 3.776140、最高 4.532177；
- 每条 5–10 个真实评分控制点，共 8152 个控制点；
- 精确最坏路线体素重叠 0.897059；5 万对抽样平均重叠 0.089593、P95 0.538929。

## 文件选择

- `kcd2_scene1_1000_capture_import_sparse_controls.json`：仅包含数值 pose 的 1000 条稀疏控制轨迹容器。
- `20260810_191147_scene_1_auto22_true_gain2_no_backtrack_balanced_1000_trajectories.json`：完整离线规划证据，含真实控制点分数、源 sample/point 和路径指标。
- `kcd2_scene1_1000_capture_source_mapping.json`：采集轨迹 ID 到源 node、sample、point 和测量分数的映射。
- `validation.json`、`capture_import_validation.json`、`generation_diagnostics.json`：规划、导入合同和搜索/选择诊断。
- `trajectory_summary.csv`、`capture_trajectory_summary.csv`：逐轨迹表格摘要。
- `TRAJECTORY_VISUAL_REPORT_CN.md`：所有诊断图的中文解释。
- `visualization_manifest.json`：图表清单和代表轨迹清单。

## KCD2 回放边界

这是离线规划集，不是“游戏内精确回放已验收”的证明。当前 KCD2 适配器可以读取这个集合 JSON，但 `TrajectoryService.load_external_json()` 对包含 `trajectories[]` 的文件默认选择第一条；批量 1000 条任务仍需要采集界面增加逐条选择/调度，或在外部先拆分。

相邻控制点之间需要平滑插值，建议位置使用 smootherstep，yaw 使用最短角路径。KCD2 使用 z 轴作为竖直轴。连续插值的碰撞安全、游戏画面是否精确到达绝对 pose，以及运行时插值帧的美学单调性都尚未证明，正式全量采集前必须先做 10–20 条 smoke capture。

## 公开数据边界

原始 2882 张扫描图、六张源图 storyboard、模型权重和完整采集数据集未提交。完整规划 JSON 中的 `image_path` 已改为不可解析的相对证据路径，仅保留源图文件名用于本地对照。

本目录提交的 PNG 只包含轨迹和统计图，不包含原始游戏截图。
