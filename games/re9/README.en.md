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
