# Backrooms Lost Runners Camera Capture Adapter

[中文](README.md)

This is the second game profile for the project's UE Camera Runtime. The target build is UE 5.6:

- Process: `BackroomsLostRunners-Win64-Shipping.exe`
- ProductVersion: `++UE5+Release-5.6-CL-44394996`
- Three LWC `FMinimalViewInfo` copy sites were verified and share the existing runtime ABI.
- The profile requires exactly three matches and refuses to hook any other count.
- A game-specific HUD hook has not been added, so Delete HUD toggling is unavailable.

The first live Runtime acceptance passed on 2026-08-15: DLL injection, three hooks, continuous Pose, a reversible absolute `setPose`, relative movement, and a 1.2-second native trajectory all reached their requested poses. The adapter is now beta; keyboard/mouse feel and OBS still/video capture still need one user-visible acceptance pass.
