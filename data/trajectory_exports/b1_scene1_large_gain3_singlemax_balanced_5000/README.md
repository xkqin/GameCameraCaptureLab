# B1 Scene 1 Large-Gain Trajectories (5,000)

This directory contains 5,000 offline-planned, low-to-high camera trajectories for the Black Myth: Wukong B1 outdoor scene:

`scen_1_heifengdong_dongwai_static22_20260819_224337_145`

## Planning constraints

- All exported controls are real source poses with measured EZCAM still-image scores.
- Scores at real controls are strictly increasing.
- Minimum measured score gain: 3.0.
- Every trajectory ends at the same global maximum pose (score 6.810137).
- Minimum net displacement: 10 m.
- Maximum physical path length: 80 m.
- Maximum path-to-net-distance ratio: 2.0.
- Near physical revisits are forbidden at a 4 m control-point radius.
- All 5,000 physical geometry signatures and real-node path signatures are unique.
- Runtime interpolation frames do not carry measured score labels.

## Main files

- `b1_scene1_large_gain3_singlemax_balanced_5000_low_to_high_ui.json`: UI/replay input.
- `b1_scene1_large_gain3_singlemax_balanced_5000_trajectories.json`: full planning metadata and trajectories.
- `trajectory_summary.csv`: per-trajectory summary.
- `validation.json`: exact source-pose and trajectory-constraint validation.
- `capture_readiness_validation.json`: replay contract and capture-readiness report.
- `exact_global_route_overlap_audit.json`: exact all-pairs inter-trajectory overlap audit.
- `global_route_overlap_audit.json`: earlier deterministic 100,000-pair overlap sample.
- `*.png`: score, distance, XZ, and XYZ diagnostics.

## Important limitation

Continuous runtime interpolation has not been checked against a scene collision mesh, and interpolated video frames have not been recaptured and rescored. Run a short in-game smoke capture before collecting all 5,000 videos.
