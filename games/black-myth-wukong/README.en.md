# Black Myth: Wukong adapter

[中文](README.md) · [English](README.en.md)

The Black Myth adapter is a lightweight capture UI that runs alongside UUU. It reads camera poses from Connector shared memory, manages JSON/CSV points and trajectories, and uses UUU's default hotkeys for low-frequency closed-loop movement and screenshots.

## Capability boundary

| Capability | Current status |
|---|---|
| UUU free camera | Available after correct UUU injection |
| XYZ, quaternion, Yaw/Pitch/Roll, FOV | Requires the Native Bridge, Connector handshake, and a valid pose |
| Point load/export and current-frame screenshot | Implemented |
| Capture from point/trajectory files | Control flow implemented; calibration depends on the current game version |
| Native absolute `setPose` | Not public in UUU 5.8.21; unavailable here |
| Film-quality frame-accurate trajectory | Not implemented; current control is hotkey-feedback-based and relative |

A loaded DLL alone does not prove that the pose channel is working. The UI enables capture only after the Bridge version, game PID, UUU handshake, valid pose, and Camera ON checks all pass.

## Usage order

1. Start the game and enter an interactive scene; borderless windowed mode is recommended.
2. Double-click `启动黑神话采集工具.bat`.
3. Select the local UUU directory and run “Prepare Pose Bridge”.
4. Open UUU, select the current game process, and inject.
5. Return to the game, press `Insert` to enable the free camera, and wait for live pose data in the UI.

If UUU was injected before the pose bridge started, the current session cannot complete the handshake. Exit the game fully and retry in the order above.

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

```powershell
cd games\black-myth-wukong
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

The pre-migration notes are in [`docs/ORIGINAL_GUIDE.md`](docs/ORIGINAL_GUIDE.md).
