# RE Engine / RE9 adapter

[中文](README.md) · [English](README.en.md)

This is the most mature adapter in Game Camera Capture Lab. To preserve existing scripts, configuration, and long trajectory files, the RE9 implementation remains at the repository root:

- Python source: `core/src/re9_pose_recorder/`
- Lua and platform configuration: `core/configs/`, `data/scene_2_capture/`
- Still-scan points: `data/scene_points/`
- Trajectories and exports: `data/trajectories/`, `data/trajectory_exports/`
- Windows/Linux launch scripts: `scripts/`

See the [Chinese user guide](../../docs/USER_GUIDE_ZH.md) and the [archived original guide](../../docs/RE9_ORIGINAL_GUIDE.md) for the detailed workflow.

Pose readout, absolute pose control through Lua, OBS screenshots, layered scans, and trajectory replay have been verified for this adapter. These capabilities must not be assumed for other game adapters.

## Optional per-pixel depth capture (experimental)

The existing still-capture UI now includes `Capture per-pixel depth (3DGS)`. It is disabled by default, leaving the RGB and 22-view workflow unchanged. When enabled, every RGB still must pair with a same-resolution per-pixel depth image; a failed depth readback removes that RGB and does not append a completed row to `samples.csv`.

Build and install the REFramework D3D12 plugin first:

```powershell
cd games\re9\native\re9-depth-bridge
.\build.ps1
.\install.ps1 -GameDirectory "D:\steam\steamapps\common\RESIDENT EVIL requiem BIOHAZARD requiem"
```

Restart RE9 and wait for `Depth plugin: ready (..., D3D12)` in the UI before enabling depth. Each RGB produces a `depth/*.npy` array plus raw GPU depth, a preview, a valid-pixel mask, and camera metadata. The `.npy` file is a meter-valued `height x width` linear view-space Z array. Resuming an older RGB-only dataset backs up and extends its CSV schema without deleting old rows.

See [`native/re9-depth-bridge/README.md`](native/re9-depth-bridge/README.md) for source, output details, and runtime acceptance checks. The native plugin has offline build and test coverage, but each RE9 update still requires the one-unit wall-depth and RGB/depth edge-alignment checks.
