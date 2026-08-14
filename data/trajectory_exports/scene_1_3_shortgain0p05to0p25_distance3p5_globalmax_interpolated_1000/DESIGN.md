# scene_1.3 Terminal-Gain Interpolated Preview

This is an experimental replay candidate set. It does not replace any accepted trajectory directory.

## Objective

- Generate 1000 short, smooth candidate motions from measured high-score starts.
- Every trajectory ends at the single measured global maximum, score 6.702875.
- Intermediate aesthetic monotonicity is not required.

## Score provenance

- Exactly two frames per trajectory carry measured scores: the real start and real endpoint.
- All 28 materialized interpolation frames have `score=null`, `raw_score=null`, and `estimated_score=null`.
- No interpolated value is presented as measured aesthetic evidence.

## Geometry

- All 6 eligible measured start families inside the configured radius are used.
- Family counts differ by at most one trajectory.
- One non-oscillating smooth arc per trajectory.
- Maximum configured lateral offset: 0.18 m, additionally limited to 0.12 of endpoint distance.
- 30 replay keyframes at 0.10 seconds per step.
- Exact pose curves and 1 cm quantized physical curves are both unique.

## Required smoke checks

- The current capture UI source is unavailable in this workspace, so runtime loading is not proven here.
- Collision clearance is not proven without a collision mesh or game smoke capture.
- Intermediate video aesthetics are unscored and intentionally not assumed monotonic.
