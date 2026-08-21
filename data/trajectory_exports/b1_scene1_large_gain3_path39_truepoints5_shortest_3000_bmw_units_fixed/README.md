# B1 Scene 1: Black Myth capture-ready trajectory set

Use this file in the Black Myth capture UI:

`b1_scene1_large_gain3_path39_truepoints5_shortest_3000_low_to_high_ui.json`

Do not load `*_trajectories.json` directly into the capture UI. That file is
planning and audit metadata whose XYZ controls are stored in meters.

The capture UI file stores Black Myth native positions in centimeters. Its
3,000 trajectories each contain exactly five measured source controls and end
at the source global maximum:

- Score: `6.8101372718811035`
- Source image: `14286_p0650_middle_yaw315_pitch_00.jpg`
- Native XYZ: `(-71425.0, 47934.50609946881, 4583.854037332569)`
- Yaw / pitch / FOV: `(315.0, 0.0, 65.0)` degrees

`black_myth_capture_endpoint_audit.json` independently reloads the UI file
with `bmw_capture_studio.files.load_trajectories` and checks every endpoint.

Only the five source controls have measured EZCAM scores. Runtime interpolation
frames are not scored, and collision clearance remains unverified until a smoke
capture is completed.
