#pragma once

#include "BmwCameraBridgeProtocol.h"

namespace bmw_camera
{
float wrapDegrees(float value);
float clampValue(float value, float minimum, float maximum);
bool finiteView(const CameraView& view);
void applyRelativeControl(CameraView& view, const NativeControl& command);
CameraSnapshot makeSnapshot(
    const CameraView& view,
    bool cameraEnabled,
    bool movementLocked,
    bool hudHidden,
    bool inputCaptured);
} // namespace bmw_camera
