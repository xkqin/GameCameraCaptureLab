# Black Myth: Wukong self-developed UE camera capture

[中文](README.md) · [English](README.en.md)

This adapter defaults to the project's generic `UeCameraRuntime.dll` plus the Black Myth profile. It does not require UUU, IGCSClient, or Connector. The runtime hooks the UE5 LWC camera-copy path and exposes a free camera, precise pose feedback, atomic absolute `setPose`, 22-view still scans, and in-process smooth trajectories to the complete capture UI. The old `BmwCameraBridge` name remains only as a local compatibility fallback.

## Capabilities

| Capability | Status |
|---|---|
| WASD/QE free camera with game-input suppression while Camera ON | Implemented |
| Mouse-look yaw/pitch | Implemented |
| Hold Shift for 5x movement speed | Implemented |
| Delete / UI button HUD visibility toggle | Implemented; full HUD coverage needs in-game visual acceptance |
| Double-precision XYZ plus Yaw/Pitch/Roll/FOV | Implemented |
| Atomic absolute `setPose` | Implemented |
| In-process Hermite trajectory | Implemented; terminal pose is held |
| Point files and automatic 22-view still capture | Integrated |
| OBS 1920×1080 JPG screenshots | Integrated |
| Single/batch trajectory recording and resume | Integrated |
| 30-second OBS restart segmentation | Integrated |
| Native Windows injection | Implemented |
| Linux/Proton loopback relay | Implemented; injector must run in the same Proton prefix |

The source build and offline protocol tests pass. Our runtime has also completed live signature discovery, hook installation, and real rendered-pose readout in Black Myth. A clean-game-session acceptance test is still required for the final v1 artifacts' free movement, full HUD coverage, long-distance `setPose`, and rendered trajectory, so the adapter remains experimental rather than being presented as final compatibility.

## Windows workflow

1. Fully exit the game, UUU, and IGCSClient.
2. Start only Black Myth: Wukong and enter a rendered scene; borderless mode is recommended.
3. Start the capture UI.
4. Click **Inject Camera Bridge**. The UI uses `UeCameraInjector.exe` by default and refuses to stack over UUU or an old Connector.
5. Start capture after Pose, absolute `setPose`, and trajectory capabilities are ready.

Controls: `Insert` toggles the camera; `Home` toggles movement lock; `Delete` toggles HUD visibility; `WASD/QE` moves; mouse movement controls yaw/pitch; arrow keys rotate; `Z/C` rolls; numpad `+/-` changes FOV; hold `Shift` for 5x speed; `Ctrl` slows down. Mouse sensitivity can be set with `BMW_CAMERA_MOUSE_SENSITIVITY`. Automated capture enables the camera itself.

## Build

With Visual Studio 2022 Build Tools, CMake, and x64 MASM:

```powershell
cd games\black-myth-wukong\native
.\build_standalone.ps1
```

Outputs:

```text
native/build_standalone_v1/Release/
├─ UeCameraRuntime.dll          # default generic runtime
├─ UeCameraInjector.exe         # default generic injector
├─ BmwCameraBridge.dll          # compatibility name
└─ BmwCameraInjector.exe        # compatibility name
```

No UUU binary is included or redistributed.

## Linux/Proton

Set the Steam launch option to expose the loopback relay:

```bash
BMW_BRIDGE_PORT=28791 %command%
```

Then launch the Linux UI with the same endpoint and Proton environment:

```bash
export BMW_BRIDGE_ENDPOINT=127.0.0.1:28791
export BMW_PROTON_COMMAND="/path/to/Proton/proton"
./launch_bmw_capture_studio.sh
```

For a custom prefix launcher, set `BMW_CAMERA_INJECT_COMMAND` with an `{injector}` placeholder. The relay binds only to loopback and supports state reads, relative controls, absolute `setPose`, trajectory start, and trajectory stop.

## Tests

```powershell
$env:PYTHONPATH = "src;games/black-myth-wukong/src"
python -m unittest discover -s games/black-myth-wukong/tests -v
```

Game updates can invalidate the audited camera signature. If the UI reports `hook_unavailable`, stop capture and re-audit the signature instead of writing to an unverified address.
