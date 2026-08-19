# scen_1_heifengdong_dongwai

黑神话：悟空「黑风洞洞外」五层倾斜摄影密集点位图。

- 使用本地记录点位图中前 193 个有效空间点推断三维外包络。
- 排除重复且远离主体空间的记录 194、195，避免异常扩张边界。
- 在外包络内部建立 5 个错位高度层，共 980 个空间点。
- 每个空间点由采集器展开为 22 个视角，共计划采集 21,560 张图片。
- 所有生成点距离推断外包络至少 1.5 m。
- 原始 `x/y/z` 保留黑神话厘米单位，同时写入 `x_m/y_m/z_m` 便于分析。

文件：

- `scen_1_heifengdong_dongwai.json`：采集器可直接加载的点位图。
- `scen_1_heifengdong_dongwai.png`：五层空间铺点和外包络预览。
- `plan_summary.json`：层高、点数、图片总数和几何验证摘要。

采集时在统一采集器的「静态 22 方向采集」区域选择 JSON 文件。点位的分层平移提供重建视差，22 视角负责水平、上下倾斜及垂直方向覆盖。

## English

This is the five-layer dense oblique-capture point map for the exterior of Black Wind Cave in *Black Myth: Wukong*. It contains 980 spatial positions inferred from the first 193 validated recorded points. The capture studio expands every position into 22 views, producing a 21,560-image plan. Native coordinates remain in centimeters, while metric duplicates are included for analysis. Load `scen_1_heifengdong_dongwai.json` from the capture studio's **22-View Still Capture** section.
