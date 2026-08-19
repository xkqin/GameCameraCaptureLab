#pragma once

#include <windows.h>

namespace bmw_camera
{
// Starts the repository-owned D3D12 depth bridge. The worker is passive until
// a file-IPC request appears, so normal RGB/Pose capture does not install GPU
// hooks or allocate readback resources.
HANDLE startNativeDepthCapture(volatile LONG* stopRequested);

// Waits for the worker, restores any lazily-installed vtable hooks, and
// releases retained D3D12 objects.
void stopNativeDepthCapture(HANDLE thread);
} // namespace bmw_camera
