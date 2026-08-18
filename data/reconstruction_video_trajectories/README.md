# RE9 Reconstruction Video Trajectories

This folder contains the strict Scene 3.1 and Scene 3.2 video routes used for pose-distance frame extraction. It is separate from aesthetic trajectory exports and static reconstruction manifests.

| Scene | Logical routes | Capture segments | Total distance | Nominal time at 4 units/s |
|---|---:|---:|---:|---:|
| scene_3.1 | 7 | 11 | 7,115.0 | 29.6 min |
| scene_3.2 | 7 | 23 | 13,971.1 | 58.2 min |

Each scene has five layer-wise serpentine routes at `0`, `-10`, `-25`, `-40`, and `-55` degrees, plus repeated Y04 and Y05 routes at `-82` degrees. Long routes are evenly divided into at most 180-second capture segments. Adjacent segments share the exact same boundary pose.

Use the `trajectories` array directly with the existing RE9 trajectory loader. The `logical_trajectories` array explains how capture segments belong to the seven complete routes.

For reconstruction, record at 60 FPS and extract by pose-log translation: retain a frame after approximately 0.8 game unit of movement, cap gaps at 1.0 unit, remove exact shared-boundary duplicates, and exclude settle/post-roll frames. At the nominal speed this is equivalent to about 5 FPS or every 12th frame.

Regenerate deterministically with:

```powershell
python scripts/generate_reconstruction_video_trajectories.py
```
