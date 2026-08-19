# scene_2_huaguoshan_jianlulin

《黑神话：悟空》Scene 2「花果山剑林」五层倾斜摄影密集点位图。

- 使用当前记录的 716 个空间边界点推断三维外包络。
- 自动识别 3 段独立边界记录，并共同构建三维凸包。
- 在外包络内部建立 5 个错位高度层，共 2,500 个空间点。
- 每个空间点由采集器展开为 22 个方向，共计划采集 55,000 张图片。
- 所有生成点均在推断外包络内，并保留至少 5 m 安全边距。
- 原始 `x/y/z` 保留《黑神话：悟空》的厘米单位，同时提供 `x_m/y_m/z_m` 便于分析。

文件：

- `scene_2_huaguoshan_jianlulin.json`：统一采集器可直接加载的点位图。
- `scene_2_huaguoshan_jianlulin.png`：五层空间铺点和外包络预览。
- `scene_2_huaguoshan_jianlulin.html`：可旋转、缩放和分层查看的三维点位图。
- `plan_summary.json`：层高、点数、图片总数及几何验证摘要。

在统一采集器的「静态 22 方向采集」区域选择 JSON 文件。多高度错位铺点提供重建所需的平移视差，22 方向模式负责水平、上下倾斜和垂直视角覆盖。

> 几何边界来自记录点的三维凸包，并不等同于游戏碰撞体。建议正式全量采集前先运行少量点位进行可达性检查。

## English

This is the five-layer dense oblique-capture point map for Scene 2, `huaguoshan_jianlulin`, in *Black Myth: Wukong*. It contains 2,500 spatial positions inferred from 716 recorded boundary points. The capture studio expands every position into 22 views, producing a 55,000-image plan. Every generated point lies inside the inferred 3-D envelope with at least 5 m of clearance. Native coordinates remain in centimeters, with metric duplicates included for analysis. Load `scene_2_huaguoshan_jianlulin.json` from the capture studio's **22-View Still Capture** section.
