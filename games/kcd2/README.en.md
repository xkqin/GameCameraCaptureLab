# Kingdom Come: Deliverance II adapter

[中文](README.md) · [English](README.en.md)

The KCD2 adapter provides an independent Tkinter interface for camera DLL injection ordering, live pose readout, scene-boundary points, layered scan plans, OBS capture, and trajectory experiments.

## Capability boundary

| Capability | Current status |
|---|---|
| Camera Tools free camera | Verified in game |
| XYZ, quaternion, Yaw/Pitch/Roll, FOV | Verified readable |
| Continuous pose CSV, points, and scan plans | Verified |
| OBS RGB + Pose still samples | Implemented; requires OBS WebSocket |
| Raw `depth.npy` + preview | Output schema and offline conversion verified; repository-owned native backend integration pending |
| 20 Hz relative random motion with seed replay | Verified |
| Arbitrary absolute `setPose` reaching the final game image | Not confirmed |
| Exact replay of arbitrary JSON keyframes | Code path exists; still depends on absolute-control acceptance |

A changed value after writing memory does not prove that the rendered camera moved. The adapter therefore does not label absolute trajectory replay as complete.

## Unified capture entrypoint

KCD2 can now be selected from the repository launcher, but it is not treated as a UE5 profile:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File launchers\launch_unified_capture_studio.ps1 -GameId kcd2
```

This route launches the adapter's Camera Tools/IGCS backend and writes runtime data under
`capture_data/kcd2/`. The legacy `games/kcd2/capture_studio_data/` output and direct launcher
remain available. The unified entrypoint only supplies routing and paths; it does not copy or
upload the closed-source DLL. Passing `-TrajectoryFile` makes the studio load that JSON
trajectory on startup.

## Local setup

Closed-source Camera Tools is not included. The unified launcher discovers it in this order:
`-CameraToolsDir`, `GAME_CAMERA_TOOLS_DIR`, the repository-local directory, then the legacy
sibling project. Before injection it checks the DLL, Client, and fixed v1.0.5 SHA256. A legally
obtained installation may also be placed in:

```text
games/kcd2/camera_tools/
├─ KCD2CameraTools.dll
├─ IGCSClient.exe
└─ other tool files
```

After the game reaches an interactive scene, double-click `启动KCD2采集工具.bat`, then use the “System and Live Pose” page to prepare and inject. IGCS Client must start before the camera DLL; restart the game completely after a failed session.

## RGB + Pose + Depth still capture

The Stills page now has an optional **Capture raw depth too** checkbox, disabled by default. When selected, each sample is stored as:

```text
sample_000001/
├─ rgb.jpg
├─ depth.npy
├─ depth_preview.png
└─ metadata.json
```

OBS WebSocket still produces RGB. The repository has replaced its external depth add-on with a
repository-owned native D3D12 runtime, but only the Black Myth runtime is connected today.
KCD2 therefore does not enable or fabricate depth yet. Once integrated, output remains raw
device depth with `metric_depth=false` until an audited projection matrix or clipping-plane
calibration is available.

Camera positions use the user-provided `1 game unit = 1 m` scale and record
`meters_per_unit=1.0`. Point sets, trajectories, Pose CSV files, and static samples preserve
native coordinates while adding `position_m`; this does not convert raw device depth into
metric depth.

KCD2 native depth is explicitly “backend integration pending”, not “implementation complete”.
The UI blocks it until depth-buffer selection, pixel alignment, HUD, and transparent-object
behavior pass an in-game acceptance capture.

## Data and examples

Direct launches write runtime data to the ignored `capture_studio_data/` directory; unified
launches write to the ignored `capture_data/kcd2/` directory. The repository keeps small reusable examples:

- `examples/scene_points/`: real scene boundary points;
- `examples/scan_plans/`: a five-layer plan with 131 spatial positions and 22 views per position;
- `examples/trajectories/`: a 160-frame relative random-motion example.

## Offline tests

```powershell
cd games\kcd2
$env:PYTHONPATH = "src"
python -m compileall -q kcd2_pose_control.py src tests
python -m unittest discover -s tests -v
```

Low-level fields and experiment notes are in [`docs/camera_reverse_engineering.md`](docs/camera_reverse_engineering.md). The pre-migration notes are in [`docs/ORIGINAL_GUIDE.md`](docs/ORIGINAL_GUIDE.md).
