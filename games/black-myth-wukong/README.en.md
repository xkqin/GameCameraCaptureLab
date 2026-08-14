# Black Myth: Wukong adapter

[中文](README.md) · [English](README.en.md)

The Black Myth adapter is a lightweight capture UI that runs alongside UUU. It reads camera poses from Connector shared memory, manages JSON/CSV points and trajectories, and calls UUU 5.8.21's in-process native camera methods under pose feedback control.

## Capability boundary

| Capability | Current status |
|---|---|
| UUU free camera | Available after correct UUU injection |
| XYZ, quaternion, Yaw/Pitch/Roll, FOV | Requires the Native Bridge, Connector handshake, and a valid pose |
| Point-map record, load, and export | Implemented; maps retain XYZ, orientation, and FOV |
| Automatic static 22-view capture per spatial point | Implemented as a one-click multi-point run with the shared RE9/KCD2 8 middle + 6 upper + 6 lower + ceiling/floor pattern |
| Single/batch trajectory recording with OBS | Implemented with task and sample progress |
| Automatic resume | Implemented; complete trajectories are detected from artifacts |
| Silent-video protection | OBS audio inputs are muted before recording and restored afterward |
| Absolute pose target | Implemented through UUU 5.8.21 relative native steps plus pose-feedback control; this is not a one-command atomic `setPose`, and in-game visual acceptance is pending |
| Film-quality frame-accurate trajectory | Native backend implemented; frame smoothness and final image still require in-game acceptance |

A loaded DLL alone does not prove that the pose channel is working. The UI enables capture only after the Bridge version, game PID, UUU handshake, valid pose, and Camera ON checks all pass.

## Usage order

1. Start the game and enter an interactive scene; borderless windowed mode is recommended.
2. Double-click `启动黑神话采集工具.bat`.
3. Select the local UUU directory and run “Prepare Pose Bridge”.
4. Open UUU, select the current game process, and inject.
5. Return to the game, press `Insert` to enable the free camera, and wait for live pose data in the UI.

If UUU was injected before the pose bridge started, the current session cannot complete the handshake. Exit the game fully and retry in the order above.

## Linux launcher and boundary

The adapter can be launched on Linux with:

```bash
cd games/black-myth-wukong
sudo apt install python3 python3-venv python3-tk  # Debian/Ubuntu, if needed
chmod +x launch_bmw_capture_studio.sh
./launch_bmw_capture_studio.sh
```

Use `./launch_bmw_capture_studio.sh --trajectory-file /path/to/file.json` to preselect a trajectory. Linux supports the Tk interface, JSON/CSV point and trajectory file management, offline data handling, opening output folders, and OBS WebSocket when OBS is available on the same Linux desktop. The live Black Myth camera path is not Linux-supported: UUU 5.8.21 injection, the Windows Native Bridge, Connector pose shared memory, and in-game camera control still require Windows. On Linux the status panel explicitly shows compatibility mode, and global F8 is disabled; use the visible UI controls for file operations.

## Feishu alerts and opt-in repair

The adapter reuses RE9's config search order and fields: `configs/linux.local.yaml` → `configs/linux.yaml` → `configs/default.yaml`. Configure `notifications.feishu.webhook_url`, `secret`, and `mention_open_id` for asynchronous error alerts, or override them with `RE9_FEISHU_WEBHOOK_URL`, `RE9_FEISHU_SECRET`, and `RE9_FEISHU_MENTION_OPEN_ID`. Alerts are disabled by default, and failure logs contain only exception types, never webhook URLs or signing secrets.

`automation.codex_recovery.enabled` is `false` by default. Only when explicitly enabled with `codex_bin` (or `RE9_CODEX_BIN`) will an error queue a detached repair worker. It reuses RE9's `RE9_CODEX_*` fields, cooldown lock, and private state pattern; repair logs stay under `capture_data/logs/`. The default prompt performs offline checks first and does not start the game or capture automatically. Never commit `*.local.yaml`, webhooks, secrets, logs, or datasets.

## Native Bridge

Only the Bridge source is included:

```text
native/
├─ BmwUuuPoseBridge.cpp
├─ CMakeLists.txt
└─ build_bridge.ps1
```

The build output `native/build/Release/IgcsConnector.addon64` is ignored. UUU and Connector are not distributed with this repository.

## Data and tests

Runtime data is written to the ignored `capture_data/` directory. Public point and trajectory format examples are in `examples/`.

Static point-map runs are written under `capture_data/still_captures/`. Each spatial point expands to 22 images captured through the OBS WebSocket Program/Source screenshot API; `manifest.json/.csv` records the spatial-point index, view pattern, target pose, observed pose, screenshot source, and image path. There is no window-capture fallback. The UI can continue from a selected spatial-point ordinal.

Trajectory recordings use a KCD2-style resumable layout:

```text
capture_data/trajectory_captures/<scene-id>/<batch-id>/
├─ run_manifest.json
├─ trajectory_index.csv
├─ trajectory_set_source.json/.csv
└─ traj_0001/
   ├─ raw/video.*
   ├─ source_keyframes.csv
   ├─ playback_plan.csv
   ├─ observed_pose.csv
   ├─ trajectory_timing.csv
   └─ recording_manifest.json
```

The trajectory file dropdown loads a selection immediately. The primary action continuously records from the selected trajectory index through the end of the file, while resume locates the newest incomplete batch. Resume checks the actual video, four CSV artifacts, and completion manifest. The OBS password stays in process memory or can be supplied through `BMW_OBS_PASSWORD`; it is not written to `settings.json`.

`playback_plan.csv` records absolute targets sent to UUU 5.8.21's in-process native camera control; measured poses are stored separately in `observed_pose.csv`. The controller converges from real pose feedback and no longer depends on game-window focus or simulated hotkeys. The internal ABI is version-locked to UUU 5.8.21; other versions are rejected.

```powershell
cd games\black-myth-wukong
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

The pre-migration notes are in [`docs/ORIGINAL_GUIDE.md`](docs/ORIGINAL_GUIDE.md).
