<p align="center">
  <img src="docs/assets/hero-multigame.png" alt="Game Camera Capture Lab" width="100%">
</p>

<h1 align="center">Game Camera Capture Lab</h1>

<p align="center">
  <a href="README.md">中文</a> · <a href="README.en.md">English</a>
</p>

<p align="center">
  A multi-game toolkit for camera poses, scene points, still scans, and trajectory capture.
</p>

This repository is no longer limited to RE9. Each game is packaged as an independent adapter while sharing one launcher, versioned JSON Schemas, and a common data layout. The registry discovers `games/*/game.json`, so more games can be added without rewriting the launcher.

## Demo

<p align="center">
  <a href="docs/assets/game-camera-capture-demo.mp4">
    <img src="docs/assets/game-camera-capture-demo-preview.gif" alt="KCD2 camera trajectory demo" width="720">
  </a>
</p>

The preview above comes from a real KCD2 free-camera motion capture. Open the [full H.264 demo](docs/assets/game-camera-capture-demo.mp4) by clicking the preview.

## Current game adapters

| Game | Engine | Pose readout | Absolute pose control | Points / still capture | Trajectory support | Maturity |
|---|---|---|---|---|---|---|
| RE Engine / RE9 | RE Engine | Verified | Verified with Lua `setPose` | Verified | Verified replay | Stable |
| Kingdom Come: Deliverance II | CryEngine | Verified | In-game visual result not confirmed | Implemented, with OBS integration | Relative random motion verified; precise replay pending | Beta |
| Black Myth: Wukong | Unreal Engine 5 | Requires UUU Connector handshake | Relative UUU steps plus pose feedback reach an absolute target; not an atomic `setPose`, in-game acceptance pending | Implemented | Experimental native pose-feedback replay | Experimental |

Reading a pose, observing a changed value after a memory write, and visually reaching the target pose in the game are three different checks. The table marks only capabilities that were actually verified; RE9 capabilities are not automatically assumed for other games.

## Interface, planning, and outputs

The interface mockups, pipeline diagrams, and dataset previews created during the earlier RE9 work are intentionally retained. They document the mature RE Engine workflow and serve as interaction references for future adapters.

<table>
  <tr>
    <td width="50%"><img src="docs/assets/interface-overview.png" alt="Capture interface overview"></td>
    <td width="50%"><img src="docs/assets/capture-gui.png" alt="Still scan and trajectory capture GUI"></td>
  </tr>
  <tr>
    <td><b>Capture system overview</b><br>From free camera and pose readout to points, screenshots, and trajectory jobs.</td>
    <td><b>Still-scan and trajectory UI</b><br>A unified view of point plans, task state, and capture progress.</td>
  </tr>
  <tr>
    <td><img src="docs/assets/trajectory-replay.png" alt="Trajectory replay visualization"></td>
    <td><img src="docs/assets/pipeline.png" alt="Capture pipeline"></td>
  </tr>
  <tr>
    <td><b>Trajectory replay design</b><br>Keyframes, camera paths, and measured pose feedback are recorded separately.</td>
    <td><b>Complete data pipeline</b><br>Pose, OBS, frame alignment, scoring, and dataset export.</td>
  </tr>
</table>

![Dataset preview](docs/assets/dataset-preview.png)

The original RE9 trajectory animation and detailed notes remain in [`docs/RE9_ORIGINAL_GUIDE.md`](docs/RE9_ORIGINAL_GUIDE.md).

## Quick start on Windows

1. Clone the repository and install Python 3.10 or newer.
2. Double-click `启动多游戏采集中心.bat`.
3. Select a game in the launcher and review its capability status, notes, and examples.
4. Install legally obtained third-party camera tools according to the adapter guide. This repository does not distribute closed-source DLLs, UUU, PAK files, saves, or game files.

You can also start the hub directly:

```powershell
python launcher\game_capture_hub.py
```

RE9 has additional dependencies. On first use, run “Install RE9 Python environment” from the hub, or run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
```

KCD2 and Black Myth: Wukong can be started with the standalone scripts in their adapter directories.

## Linux compatibility

The Black Myth adapter now has a native Linux launcher for the cross-platform parts of the studio:

```bash
cd games/black-myth-wukong
sudo apt install python3 python3-venv python3-tk  # Debian/Ubuntu, if needed
chmod +x launch_bmw_capture_studio.sh
./launch_bmw_capture_studio.sh
```

An initial trajectory can be selected from the command line:

```bash
./launch_bmw_capture_studio.sh --trajectory-file /path/to/trajectory.json
```

On Linux the UI, JSON/CSV point and trajectory management, offline planning, output-folder opening, and OBS WebSocket connection are available. Black Myth real-time pose reading, UUU injection, the Native Bridge, and in-game camera control remain Windows-only because UUU 5.8.21 and the current Bridge use Windows process injection and named shared memory. The Linux build therefore reports a compatibility mode instead of pretending that the game camera is connected. The Windows launcher remains `launch_bmw_capture_studio.ps1`.

## Repository layout

```text
GameCameraCaptureLab/
├─ games/
│  ├─ re9/                       # Manifest and notes; mature implementation stays at the root
│  ├─ kcd2/                      # Independent source, UI, tests, and public examples
│  └─ black-myth-wukong/         # UUU adapter and Native Bridge source
├─ src/
│  ├─ game_camera_capture_lab/   # Dynamic registry and multi-game launcher
│  └─ re9_pose_recorder/         # Existing RE Engine capture implementation
├─ schemas/                      # Shared Pose, Point Set, and Trajectory schemas
├─ launcher/                     # Direct launcher entry point
├─ data/                         # RE9 points, scan plans, and representative trajectories
├─ configs/                      # RE9 platform and scan configuration
├─ docs/                         # Architecture, formats, extension, and original RE9 guide
└─ tests/                        # Project and registry tests
```

Each adapter owns its source, tests, runtime data directory, and capability declaration. The launcher does not hard-code the number of games or branch on game names.

## Shared data formats

The repository defines three versioned formats that can be exchanged across adapters:

- `camera-pose/v1`: one camera pose;
- `camera-point-set/v1`: a collection of scene points;
- `camera-trajectory/v1`: time-ordered trajectory keyframes.

Schemas are in [`schemas/`](schemas/), with cross-game examples in [`schemas/examples/`](schemas/examples/). Adapters may keep their native format and map it through a converter, but coordinate systems, angle units, and game IDs must be explicit.

See [file formats](docs/FILE_FORMATS.md) for details.

## Adding a game

An adapter can be added without modifying the launcher:

1. Create `games/<game-id>/game.json`.
2. Add its source, launcher scripts, tests, and a small set of redistributable examples.
3. Declare the verification state for pose readout, absolute control, still capture, and trajectory replay.
4. Run the registry checks and tests.

See [ADDING_A_GAME.md](docs/ADDING_A_GAME.md) and [ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full contract.

## Development and verification

```powershell
$env:PYTHONPATH = "src"
python -m game_camera_capture_lab.validate
python -m compileall -q src launcher games
python -m unittest discover -s tests -v
python -m unittest discover -s games\kcd2\tests -v
python -m unittest discover -s games\black-myth-wukong\tests -v
```

KCD2 and Black Myth tests also need their respective `games/<id>/src` directory on `PYTHONPATH`; the release validation script keeps adapters isolated.

## Distribution boundary

This repository contains original source code, configuration templates, format definitions, tests, and small example data. It explicitly excludes:

- commercial game files, personal saves, and complete playthrough saves;
- closed-source KCD2 Camera Tools, UUU, and other third-party binaries;
- game mods or PAK files unless redistribution permission is confirmed;
- screenshots, long videos, runtime logs, model caches, and complete capture datasets.

The demo video was produced by the project capture workflow as documentation; it is not a redistribution package for a game or third-party tool.

## Historical compatibility

The repository evolved from `RE9_Still_Scan`. To avoid breaking existing scripts and Windows paths, the mature RE9 implementation temporarily remains at the root. The archived detailed guide is [`RE9_ORIGINAL_GUIDE.md`](docs/RE9_ORIGINAL_GUIDE.md). New features and the project brand use **Game Camera Capture Lab**.

## Adapter guides

- [RE9 / RE Engine adapter](games/re9/README.en.md)
- [KCD2 adapter](games/kcd2/README.en.md)
- [Black Myth: Wukong adapter](games/black-myth-wukong/README.en.md)
