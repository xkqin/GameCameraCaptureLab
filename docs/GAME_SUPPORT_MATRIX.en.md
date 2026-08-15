# Game camera support matrix (2026-08-15)

[中文](GAME_SUPPORT_MATRIX.md)

This matrix answers two different questions:

1. Is there reliable public evidence that a title has a working free camera, camera paths, and path nodes containing camera location, orientation, and FOV?
2. Has this project accepted live Pose readout, atomic absolute `setPose`, trajectories, and capture output for that title?

Public compatibility evidence makes a game a strong adaptation candidate. It does not provide or certify this project's process name, hook signature, register contract, or match count.

## Confirmed scope

| Level | Meaning | Games |
|---|---|---|
| Project runtime verified | A project profile exists and live Pose/control acceptance has passed; rendered capture status remains as declared by each manifest | Black Myth: Wukong; Backrooms Lost Runners |
| Public free camera verified | Public evidence covers the free camera, camera paths, and Location/Orientation/FOV path-node state; a native project profile is still required | Hellblade II; Silent Hill 2 Remake; The Talos Principle 2; Layers of Fear (2023); RoboCop: Rogue City; Still Wakes the Deep; Clair Obscur: Expedition 33; Immortals of Aveum; Nobody Wants to Die; Remnant II; The 7th Guest Remake; Lies of P; Hogwarts Legacy; Star Wars Jedi: Survivor |
| Explicitly excluded | Requires disabling anti-cheat or is an online/anti-cheat scenario | Lords of the Fallen (2023); Fortnite |

## Best next native-profile targets

Priority is based on offline use, clear public camera evidence, absence of a known anti-cheat blocker, and likelihood of reusing the current UE5 ABI.

| Priority | Game | Confirmed | Missing project work |
|---|---|---|---|
| P0 | The Talos Principle 2 | Public UE5 compatibility, free camera, path-node Pose/FOV | Read-only signature scan of the target EXE; LWC ABI verification |
| P0 | Layers of Fear (2023) | Public UE5 compatibility, free camera, path-node Pose/FOV | Same |
| P0 | Still Wakes the Deep | Public UE5 compatibility, free camera, path-node Pose/FOV | Same |
| P0 | RoboCop: Rogue City | Public UE5 compatibility with a documented minimum tool version | Same, plus current-build match-count audit |
| P1 | Senua's Saga: Hellblade II | Public UE5 compatibility with camera/aspect-ratio notes | Camera-copy ABI plus bars/HUD/rendered acceptance |
| P1 | Silent Hill 2 Remake | Public UE5 compatibility; missing actor pose editing is unrelated to camera Pose | Native Pose and absolute setPose acceptance |
| P1 | Clair Obscur: Expedition 33 | Public UE5 compatibility; mouse rotation caveat in some scenes | Input isolation and cutscene/combat hook acceptance |
| P2 | Lies of P / Hogwarts Legacy / Jedi: Survivor | Public UE4 compatibility and path-node Pose/FOV | A UE4 non-LWC ABI adapter; the UE5 profile cannot be reused blindly |

## Evidence boundary

- The public UE5 documentation explicitly warns that the tool does not work unconditionally with every UE5 game; engine changes can remove or alter functions needed by the camera.
- It also states that camera-path nodes record camera location, orientation, and field of view. That proves camera-state availability, not connection to this project's shared-memory protocol.
- Public connector documentation further shows read-only world position, quaternion, view matrix, and Pitch/Yaw/Roll export. It does not prove an external `setPose(x, y, z, yaw, ...)` API; this project's absolute `setPose` still needs per-game native-runtime acceptance.
- The public UE4 compatibility table marks Lies of P, Hogwarts Legacy, and Star Wars Jedi: Survivor as working and documents their caveats.
- A BSD-2-Clause repository contains game-specific camera reference source, mostly for older titles and builds. It is useful for ABI and hook research but cannot certify current retail binaries.
- A candidate becomes a project adapter only after `signature count -> live Pose -> rendered absolute setPose -> smooth trajectory -> OBS image/manifest` acceptance.

## Online sources

- [UE5 free-camera compatibility, camera paths, and state fields](https://opm.fransbouma.com/uuuv5.htm)
- [UE4 free-camera compatibility, camera paths, and state fields](https://opm.fransbouma.com/uuuv4.htm)
- [Read-only live camera fields exposed by the public connector](https://github.com/FransBouma/IgcsConnector#camera-data-made-available-to-reshade-shaders)
- [BSD-2-Clause game-camera reference source](https://github.com/FransBouma/InjectableGenericCameraSystem)

## Query the machine-readable catalog

```powershell
$env:PYTHONPATH = "src"
python -m game_camera_capture_lab.support_catalog
python -m game_camera_capture_lab.support_catalog --level public_free_camera_verified
python -m game_camera_capture_lab.support_catalog --json
```

The source data is [`catalogs/game_support_catalog_v1.json`](../catalogs/game_support_catalog_v1.json). Public free-camera evidence and project-native runtime verification are mutually exclusive evidence levels, preventing a candidate from being displayed as a shipped adapter.
