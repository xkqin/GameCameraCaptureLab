# Kingdom Come: Deliverance II adapter

[中文](README.md) · [English](README.en.md)

The KCD2 adapter provides an independent Tkinter interface for camera DLL injection ordering, live pose readout, scene-boundary points, layered scan plans, OBS capture, and trajectory experiments.

## Capability boundary

| Capability | Current status |
|---|---|
| Camera Tools free camera | Verified in game |
| XYZ, quaternion, Yaw/Pitch/Roll, FOV | Verified readable |
| Continuous pose CSV, points, and scan plans | Verified |
| OBS screenshots and recording manifest | Implemented; requires OBS WebSocket |
| 20 Hz relative random motion with seed replay | Verified |
| Arbitrary absolute `setPose` reaching the final game image | Not confirmed |
| Exact replay of arbitrary JSON keyframes | Code path exists; still depends on absolute-control acceptance |

A changed value after writing memory does not prove that the rendered camera moved. The adapter therefore does not label absolute trajectory replay as complete.

## Local setup

Closed-source Camera Tools is not included. Place a legally obtained v1.0.5 installation in:

```text
games/kcd2/camera_tools/
├─ KCD2CameraTools.dll
├─ IGCSClient.exe
└─ other tool files
```

After the game reaches an interactive scene, double-click `启动KCD2采集工具.bat`, then use the “System and Live Pose” page to prepare and inject. IGCS Client must start before the camera DLL; restart the game completely after a failed session.

## Data and examples

Runtime data is written to the ignored `capture_studio_data/` directory. The repository keeps small reusable examples:

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
