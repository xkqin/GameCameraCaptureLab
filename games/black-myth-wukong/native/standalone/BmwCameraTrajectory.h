#pragma once

#include "BmwCameraBridgeProtocol.h"

#include <cstdint>
#include <vector>

namespace bmw_camera
{
struct ActiveTrajectoryPoint
{
    float timeSeconds;
    CameraView view;
};

bool loadTrajectory(
    const std::uint8_t* mapping,
    const NativeTrajectory& header,
    std::vector<ActiveTrajectoryPoint>& points);

CameraView sampleTrajectory(
    const std::vector<ActiveTrajectoryPoint>& points,
    float elapsedSeconds,
    int& segment);
} // namespace bmw_camera
