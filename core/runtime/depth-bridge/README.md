# Native D3D12 depth bridge

The depth bridge is implemented by this repository and does not require a
graphics proxy, shader injector, add-on SDK, or third-party runtime.

The Windows implementation is compiled directly into `UeCameraRuntime.dll`:

- `games/black-myth-wukong/native/standalone/BmwNativeDepth.cpp`
- `games/black-myth-wukong/native/standalone/BmwNativeDepth.h`

The module remains passive during normal RGB/Pose capture. When the Python
client writes a `game-camera-depth-bridge/v2` request, it lazily hooks the
active D3D12 command stream, selects a recent full-frame depth-stencil
resource, performs a synchronized GPU readback, and returns raw depth plus
metadata. The existing Python converter writes:

```text
depth.npy
depth_preview.png
metadata.json
```

Raw device depth is not labeled as metric depth. A title profile must provide
an audited projection matrix or calibrated clipping-plane model before
`metric_depth` can become `true`.

Black Myth: Wukong is the first runtime target. Other D3D12 games need their
own runtime-injection acceptance test and depth-resource selection profile;
the shared file protocol alone is not proof of compatibility.
