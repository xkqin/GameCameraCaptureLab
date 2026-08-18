# Scene 2 indoor oblique reconstruction plan

This is a reconstruction-only still-image plan for the indoor scene 2 space. It does not replace the existing scene 2 RGB scan, reconstruction plan, or no-chandelier layer configuration.

The plan keeps all 567 valid no-chandelier grid positions and orders them through a connected four-neighbor route around the excluded chandelier volume. Three repeated route positions bridge the obstacle without a large same-layer jump. Every route position captures five views:

- horizontal left: yaw offset `-35 deg`, pitch `0 deg`
- horizontal forward: yaw offset `0 deg`, pitch `0 deg`
- horizontal right: yaw offset `+35 deg`, pitch `0 deg`
- one layer-specific vertical oblique view for ceiling, upper wall, lower wall, or floor coverage
- one near-vertical direct view at `+82 deg` on the lower three layers or `-82 deg` on the upper three layers

The six oblique pitches are `+60`, `+45`, `+25`, `-25`, `-45`, and `-60` degrees from the lowest to highest layer. The direct views close the straight-overhead ceiling and straight-below floor gaps that oblique views alone can miss. The resulting UI plan contains 570 ordered route positions and 2,850 still screenshots. Layer-internal steps are at most approximately `0.631` game units, and globally optimized layer transitions keep the largest complete-route step at approximately `2.848` game units.

The upward views cover the interior ceiling underside. Reconstructing the exterior top surface of a roof requires additional camera positions outside and above the recorded indoor hull.

Open the existing capture UI with this plan preloaded:

```powershell
.\scripts\scan_scene2_indoor_oblique_gui.ps1
```

Enable `Capture per-pixel depth (3DGS)` only after the UI reports that the RE9 depth plugin is ready. RGB-only capture remains available with the same plan.

Regenerate the JSON file:

```powershell
.venv\Scripts\python.exe scripts\generate_scene2_indoor_oblique_plan.py
```
