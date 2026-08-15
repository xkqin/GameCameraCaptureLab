# OpenFly-inspired paper figures

This folder contains original figures for Game Camera Capture Lab. They borrow the high-level academic composition language of OpenFly (teaser mosaic, pale-blue modular pipeline, small-multiple statistics, and keyframe sequence) without copying OpenFly assets or data.

## Deliverables

- `openfly_01_multigame_teaser`: hybrid concept teaser. It mixes one real KCD2 frame, project UI visuals, and a clearly labelled concept multiverse image. Do not present it as an experimental result.
- `openfly_02_toolchain_framework`: pure-vector method overview.
- `openfly_03_kcd2_statistics`: pure-vector statistics computed from the committed KCD2 1000-trajectory CSV/JSON evidence.
- `openfly_04_layered_sampling`: pure-vector boundary-to-five-layer scan-plan figure using the committed Scene 1 plan.
- `openfly_05_recorded_frame_sequence`: hybrid layout using six real frames from the KCD2 demo video. The frames are illustrative and are not pose-synchronized.
- `openfly_06_pose_trajectory`: pure-vector path and pose table for trajectory 00750, the maximum-gain member of the 1000-trajectory set.

Every figure has an editable SVG, a PDF for paper layout, and a PNG preview. Figures 02, 03, 04, and 06 are verified to contain zero embedded raster images in their PDFs. Figures 01 and 05 intentionally embed raster content inside vector layouts.

## Evidence boundaries

- The 1000 offline KCD2 trajectories and their real scored keyframes pass the committed validation checks.
- Runtime interpolation frames are not scored.
- Collision clearance and precise in-game absolute playback are not established by these figures.
- The demo video does not include a synchronized pose log, so its six-frame sequence is not claimed to align with the offline trajectory JSON.

## Rebuild

```powershell
python build_openfly_figures.py
python render_openfly_figures.py
python verify_openfly_outputs.py
```
