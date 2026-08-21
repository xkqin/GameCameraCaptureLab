# Black Myth: Wukong B1 Scene 1 Large-Gain Trajectories (5,000)

This directory contains 5,000 offline-planned, low-to-high camera trajectories for the Black Myth: Wukong B1 outdoor scene:

`scen_1_heifengdong_dongwai_static22_20260819_224337_145`

This is Black Myth: Wukong trajectory data. It does not replace or modify the Resident Evil scene sets elsewhere in this repository.

## Planning constraints

- Every exported control is a real source pose with a measured EZCAM still-image score.
- Measured scores at real controls are strictly increasing.
- Minimum measured score gain: 3.0.
- Every trajectory ends at the same global maximum pose with score 6.810137.
- Minimum net displacement: 10 m.
- Maximum cumulative physical path length: 40 m.
- Preferred physical path length during selection: 33 m.
- Maximum path-to-net-distance ratio: 1.8.
- Maximum target-distance backtracking: 2 m.
- Near physical revisits are forbidden at measured control points.
- All 5,000 physical geometry signatures and real-node path signatures are unique.
- Runtime interpolation frames do not carry measured score labels.

## Observed statistics

- Physical path length: 25.72-40.00 m, mean 36.64 m.
- Replay duration: 11.69-18.18 s, mean 16.66 s.
- Measured score gain: 3.002-4.180, mean 3.283.
- Real measured controls per route: 4-6, mean 4.813.
- Unique start nodes: 535 across 224 unique XYZ positions.
- Distinct penultimate XYZ positions: 19.

## Main files

- `b1_scene1_large_gain3_singlemax_balanced_5000_low_to_high_ui.json`: UI/replay input.
- `b1_scene1_large_gain3_singlemax_balanced_5000_trajectories.json`: full planning metadata and trajectories.
- `trajectory_summary.csv`: per-trajectory summary.
- `validation.json`: exact source-pose and trajectory-constraint validation.
- `capture_readiness_validation.json`: replay contract and capture-readiness report.
- `independent_route_audit.json`: independent motion, uniqueness, and self-overlap audit.
- `*.png`: measured score, cumulative distance, XZ, and XYZ diagnostics.

## Important limitation

Continuous runtime interpolation has not been checked against a scene collision mesh, and interpolated video frames have not been recaptured and rescored. Run a short in-game smoke capture before collecting all 5,000 videos.
