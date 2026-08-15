# Paper figure candidates

这组图围绕 **Game Camera Capture Lab** 的同一核心概念制作：一个可扩展的多游戏相机位姿、点位、分层扫描与轨迹采集框架。当前交付分为两类：

- **电影级概念图**：高分辨率 PNG，用于论文 Teaser、封面、海报和项目主页；
- **原生矢量论文图**：可编辑 SVG、无嵌入位图的矢量 PDF，以及用于快速查看的 PNG 预览。

## 快速选片

- [全部 01–12 候选总览](candidate_contact_sheet.jpg)
- [电影级 07–12 选片表](cinematic_contact_sheet_07_12.jpg)
- [矢量论文图 01–06 选片表](vector/vector_contact_sheet.jpg)
- [从最终 PDF 本体渲染的 QA 总览](vector/vector_pdf_verification.jpg)

## 电影级候选图

| 编号 | 文件 | 风格 | 推荐用途 |
|---|---|---|---|
| 01 | `candidate_01_scientific_white.png` | 白底科学总览 | Method overview、双栏宽图 |
| 02 | `candidate_02_dark_digital_twin.png` | 深色数字孪生 | Teaser、论文首页 |
| 03 | `candidate_03_isometric_system.png` | 等距 3D 系统图 | Pipeline、系统实现 |
| 04 | `candidate_04_blueprint.png` | 深蓝技术蓝图 | Architecture、补充材料 |
| 05 | `candidate_05_abstract_manifold.png` | 抽象相机流形 | 共享表示、跨域泛化叙事 |
| 06 | `candidate_06_cinematic_multiverse.png` | 电影化多世界 | 论文 Teaser、封面 |
| 07 | `candidate_07_cinematic_ringworld.png` | 环形连续世界 | 项目主页、跨游戏总览 |
| 08 | `candidate_08_cinematic_rainstorm.png` | 暴雨夜景多世界 | 深色封面、海报主视觉 |
| 09 | `candidate_09_cinematic_vertical_city.png` | 垂直巨城与深渊 | 封面、竖向裁切、空间层级表达 |
| 10 | `candidate_10_cinematic_golden_dawn.png` | 黄金晨曦连续地貌 | **亮色论文 Teaser 首选** |
| 11 | `candidate_11_cinematic_icefire.png` | 冰火连续地貌 | **强对比论文首页首选** |
| 12 | `candidate_12_cinematic_floating_archipelago.png` | 悬浮遗迹与群岛 | **未来可扩展叙事首选** |

电影图共同约束：无文字、无 Logo、无人物；展示多于三类环境；保留相机视锥、位姿点、轨迹和坐标轴；顶部留有论文标题空间。它们属于概念视觉，不能作为实验定量结果或真实系统截图引用。

## 原生矢量论文图

每个编号都提供三个同名文件：

- `.svg`：主编辑源，可在 Illustrator、Inkscape、Figma 或浏览器中编辑；
- `.pdf`：论文排版用矢量 PDF；
- `.png`：1600×900 快速预览。

| 编号 | 文件前缀 | 内容 | 推荐章节 |
|---|---|---|---|
| 01 | `vector_01_system_overview` | 多游戏适配器、共享 schema、采集核心与数据产品 | Overview / Method |
| 02 | `vector_02_pose_contract` | 坐标系显式的 pose、point-set 与 trajectory 契约 | Representation |
| 03 | `vector_03_layered_scan` | 边界点、4–6 层空间铺点、视角分配与静态采集 | Sampling / Data Collection |
| 04 | `vector_04_trajectory_feedback` | 轨迹插值、原子 setPose、相对控制与 pose 反馈闭环 | Camera Control |
| 05 | `vector_05_adapter_architecture` | `games/*/game.json` 动态发现与未来游戏扩展 | System Architecture |
| 06 | `vector_06_dataset_lifecycle` | 点位/轨迹计划、RGB-pose 同步与可审计数据集 | Dataset Pipeline |

矢量交付已完成以下检查：

- 6 个 SVG 均可通过 XML 解析；
- SVG 源码不含 `<image>`，没有嵌入 PNG/JPEG；
- 6 个 PDF 均为 16:9（1152×648 pt）；
- `pdfimages -list` 对每个 PDF 均返回 **0 个嵌入位图**；
- PDF 中保留字体对象和矢量图元，并已从 PDF 本身重新渲染检查。

## 复现与修改

```powershell
python -m pip install -r vector\requirements.txt
python vector\build_vector_figures.py
python vector\render_vector_figures.py
python vector\verify_vector_outputs.py
python make_contact_sheet.py
```

`build_vector_figures.py` 是六张论文图的可复现源码；修改文案、颜色、位置或模块后重新运行即可。`render_vector_figures.py` 使用本机 Chrome 将 SVG 导出为 PDF 和 PNG，不会把 PNG 嵌回 PDF。`verify_vector_outputs.py` 会再次解析 SVG、使用 `pdfimages` 检查 PDF 的嵌入图像数量，并从最终 PDF 本体生成 QA 总览；因此完整验证还需要系统能找到 Chrome 和 Poppler/MiKTeX 的 `pdfimages`。

选定最终候选后，建议再按投稿模板统一字体、图号和术语，并在 LaTeX 中优先插入对应 PDF；若投稿系统支持 SVG，也可直接使用 SVG 源文件。
