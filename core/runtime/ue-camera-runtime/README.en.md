# UE Camera Runtime

This is the complete capture project's own UE4/UE5 camera-runtime foundation for offline single-player visual-data capture. It follows a profile, injector, and camera-ABI design inspired by general-purpose tools, while its implementation, protocol, and source are maintained here. It does not copy or redistribute third-party closed-source code, assets, or branding.

The runtime now registers Black Myth: Wukong and Backrooms Lost Runners profiles on the shared LWC `FMinimalViewInfo` ABI. It exposes shared-memory pose, atomic `setPose`, keyboard/mouse, optional HUD control, and in-process Hermite trajectories while reusing the project's point maps, 22-view still scans, OBS pipeline, and dataset manifests.

Build the native targets and inspect the registered profiles:

```powershell
.\UeCameraInjector.exe --list
.\UeCameraInjector.exe --process b1-Win64-Shipping.exe
```

Adding another game requires a profile when its camera matches an existing ABI, or a dedicated ABI adapter when its camera structure/register contract differs. UE4/UE5 does not imply one universal camera layout. Use this only for offline single-player games; online or anti-cheat targets are out of scope.
