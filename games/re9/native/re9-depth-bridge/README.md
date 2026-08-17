# RE9 per-pixel depth bridge

This optional REFramework plugin copies one RE9 D3D12 depth buffer only when the
static still scanner requests it. Normal RGB and 22-view capture remain unchanged
when **Capture per-pixel depth (3DGS)** is disabled.

## Build and install

Run from PowerShell on the capture PC:

```powershell
cd games\re9\native\re9-depth-bridge
.\build.ps1
.\install.ps1 -GameDirectory "D:\steam\steamapps\common\RESIDENT EVIL requiem BIOHAZARD requiem"
```

`build.ps1` downloads the REFramework source headers and one pinned
`nlohmann/json` header. Restart RE9 after installing the DLL. The still
scan UI should then show `Depth plugin: ready (..., D3D12)`.

## Files and units

For every successful RGB still, the scanner writes:

```text
images/<sample>.jpg
depth/<sample>.npy
depth_raw/<sample>.raw
depth_preview/<sample>.png
valid_masks/<sample>.png
cameras/<sample>.json
```

The `.npy` file is a full `height x width` float32 array of linear view-space Z.
Its unit is meters because this RE9 setup uses `1 game unit = 1 m`. The preview is
only for inspection; reconstruction should use `.npy`, the mask, camera metadata,
and RGB image.

## Runtime validation

The plugin and Python tests can be built offline, but the final acceptance test
must run in RE9. Capture a wall, move the camera forward exactly 1 game unit, and
verify that the wall depth decreases by about 1 meter. Also verify RGB/depth edge
alignment at the exact render resolution before starting a large scan.
