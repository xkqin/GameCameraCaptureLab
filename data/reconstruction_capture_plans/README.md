# RE9 3DGS Reconstruction Capture Plans

This directory contains dense, translated camera routes for rebuilding all six recorded RE9 spaces with 3D Gaussian Splatting. These plans are separate from the existing 22-view aesthetic-anchor captures: no old scene plan or image is overwritten.

## Capture totals

| Scene | Layers | Positions | Preserved 22-view anchors | New positions | Route bridge points | Reconstruction screenshots | Max within-layer step |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| scene_1.1 | 4 | 1,620 | 420 | 1,200 | 0 | 4,860 | 1.883 |
| scene_1.2 | 4 | 1,260 | 640 | 620 | 0 | 3,780 | 1.291 |
| scene_1.3 | 3 | 1,242 | 54 | 1,188 | 0 | 3,726 | 1.770 |
| scene_2 | 6 | 567 | 567 | 0 | 0 | 1,701 | 3.429 |
| scene_3.1 | 5 | 4,856 | 520 | 4,336 | 39 | 16,405 | 1.868 |
| scene_3.2 | 5 | 7,527 | 451 | 7,076 | 63 | 24,957 | 1.956 |
| **Total** |  | **17,072** |  |  |  | **55,429** |  |

## Capture convention

- Capture still screenshots at every ordered position; do not record these routes as video.
- The default profile captures three route-relative views per position: left `-35 deg`, forward `0 deg`, and right `+35 deg`.
- Scene 3.1 and 3.2 use the outdoor ground-coverage profile. Their five base pitches are `0`, `-10`, `-25`, `-40`, and `-55 deg`, with no upward sky view.
- Every position on scene 3's upper two layers adds one `-82 deg` direct-down image for terrain, roof, platform, and object-top coverage.
- Positions use a layer-major XZ serpentine order to keep adjacent screenshots close and preserve parallax.
- Sparse convex-hull row transitions are interpolated with reconstruction-only bridge points so within-layer steps stay at or below `2.0` game units whenever the exclusion mask permits it.
- Black outlined points in the maps are positions already present in the old 22-view grids. They remain useful as aesthetic anchors.
- Scene 2 uses `scene_2_no_lamp_scan_layers.yaml`; the chandelier ellipsoid is excluded.
- Coordinates are RE9 game units. This package does not claim a meter or centimeter conversion.

## Files

Each scene directory contains:

- `*_reconstruction_positions.csv`: one row per spatial position in capture order.
- `*_reconstruction_samples.csv`: one row per camera pose.
- `*_reconstruction_manifest.json`: metadata plus complete position and sample records. Scene 3 manifests can be loaded directly by the capture UI.
- `*_reconstruction_3d.png`: layered 3D point and route map.
- `*_reconstruction_topdown.png`: per-layer XZ route panels.

The parent directory also contains `reconstruction_capture_summary.csv`, `reconstruction_capture_summary.json`, and the source specification used to reproduce the package.

## Open scene 3 in the capture UI

```powershell
.\scripts\scan_scene3_1_reconstruction_gui.ps1
.\scripts\scan_scene3_2_reconstruction_gui.ps1
```

## Regenerate

From the repository root:

```powershell
.venv\Scripts\python.exe scripts\generate_reconstruction_capture_plans.py
```
