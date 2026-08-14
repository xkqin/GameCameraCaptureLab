#include "BmwCameraTrajectory.h"

#include "BmwCameraMath.h"

#include <algorithm>
#include <cmath>
#include <cstring>

namespace bmw_camera
{
namespace
{
bool finiteKeyframe(const TrajectoryKeyframe& point)
{
    return std::isfinite(point.timeSeconds) && std::isfinite(point.x) &&
        std::isfinite(point.y) && std::isfinite(point.z) &&
        std::isfinite(point.yawDegrees) && std::isfinite(point.pitchDegrees) &&
        std::isfinite(point.rollDegrees) && std::isfinite(point.fovDegrees) &&
        point.fovDegrees >= 1.0f && point.fovDegrees <= 179.0f;
}

template <typename Getter>
double slope(
    const std::vector<ActiveTrajectoryPoint>& points,
    const int index,
    Getter getter)
{
    const int last = static_cast<int>(points.size()) - 1;
    if (last <= 0 || index <= 0 || index >= last)
    {
        return 0.0;
    }
    const double dt = points[index + 1].timeSeconds - points[index - 1].timeSeconds;
    return (getter(points[index + 1]) - getter(points[index - 1])) / dt;
}

template <typename Getter>
double interpolate(
    const std::vector<ActiveTrajectoryPoint>& points,
    const int segment,
    const float elapsed,
    Getter getter)
{
    const auto& first = points[segment];
    const auto& second = points[segment + 1];
    const double duration = second.timeSeconds - first.timeSeconds;
    const double u = std::max(0.0, std::min(
        1.0, (elapsed - first.timeSeconds) / duration));
    const double u2 = u * u;
    const double u3 = u2 * u;
    const double h00 = 2.0 * u3 - 3.0 * u2 + 1.0;
    const double h10 = u3 - 2.0 * u2 + u;
    const double h01 = -2.0 * u3 + 3.0 * u2;
    const double h11 = u3 - u2;
    return h00 * getter(first) + h10 * duration * slope(points, segment, getter) +
        h01 * getter(second) + h11 * duration * slope(points, segment + 1, getter);
}
} // namespace

bool loadTrajectory(
    const std::uint8_t* mapping,
    const NativeTrajectory& header,
    std::vector<ActiveTrajectoryPoint>& points)
{
    if (mapping == nullptr || header.pointCount < 2 ||
        header.pointCount > kMaxTrajectoryKeyframes ||
        !std::isfinite(header.durationSeconds) || header.durationSeconds <= 0.0f)
    {
        return false;
    }
    const auto* source = reinterpret_cast<const TrajectoryKeyframe*>(
        mapping + kTrajectoryOffset + sizeof(NativeTrajectory));
    points.clear();
    points.reserve(header.pointCount);
    float firstTime = 0.0f;
    float previousTime = -1.0f;
    for (std::uint32_t index = 0; index < header.pointCount; ++index)
    {
        TrajectoryKeyframe raw{};
        std::memcpy(&raw, source + index, sizeof(raw));
        if (!finiteKeyframe(raw) || (index > 0 && raw.timeSeconds <= previousTime))
        {
            return false;
        }
        if (index == 0)
        {
            firstTime = raw.timeSeconds;
        }
        ActiveTrajectoryPoint point{};
        point.timeSeconds = raw.timeSeconds - firstTime;
        point.view.x = raw.x;
        point.view.y = raw.y;
        point.view.z = raw.z;
        point.view.yawDegrees = raw.yawDegrees;
        point.view.pitchDegrees = raw.pitchDegrees;
        point.view.rollDegrees = raw.rollDegrees;
        point.view.fovDegrees = raw.fovDegrees;
        if (!points.empty())
        {
            point.view.yawDegrees = points.back().view.yawDegrees +
                wrapDegrees(static_cast<float>(point.view.yawDegrees - points.back().view.yawDegrees));
            point.view.pitchDegrees = points.back().view.pitchDegrees +
                wrapDegrees(static_cast<float>(point.view.pitchDegrees - points.back().view.pitchDegrees));
            point.view.rollDegrees = points.back().view.rollDegrees +
                wrapDegrees(static_cast<float>(point.view.rollDegrees - points.back().view.rollDegrees));
        }
        points.push_back(point);
        previousTime = raw.timeSeconds;
    }
    const float actualDuration = points.back().timeSeconds;
    return actualDuration > 0.0f &&
        std::fabs(actualDuration - header.durationSeconds) < 0.05f;
}

CameraView sampleTrajectory(
    const std::vector<ActiveTrajectoryPoint>& points,
    const float elapsedSeconds,
    int& segment)
{
    if (elapsedSeconds <= points.front().timeSeconds)
    {
        segment = 0;
        return points.front().view;
    }
    if (elapsedSeconds >= points.back().timeSeconds)
    {
        segment = static_cast<int>(points.size()) - 1;
        return points.back().view;
    }
    while (segment + 1 < static_cast<int>(points.size()) - 1 &&
        elapsedSeconds > points[segment + 1].timeSeconds)
    {
        ++segment;
    }
    const int active = std::max(0, std::min(
        segment, static_cast<int>(points.size()) - 2));
    CameraView result{};
    result.x = interpolate(points, active, elapsedSeconds,
        [](const ActiveTrajectoryPoint& point) { return point.view.x; });
    result.y = interpolate(points, active, elapsedSeconds,
        [](const ActiveTrajectoryPoint& point) { return point.view.y; });
    result.z = interpolate(points, active, elapsedSeconds,
        [](const ActiveTrajectoryPoint& point) { return point.view.z; });
    result.yawDegrees = interpolate(points, active, elapsedSeconds,
        [](const ActiveTrajectoryPoint& point) { return point.view.yawDegrees; });
    result.pitchDegrees = interpolate(points, active, elapsedSeconds,
        [](const ActiveTrajectoryPoint& point) { return point.view.pitchDegrees; });
    result.rollDegrees = interpolate(points, active, elapsedSeconds,
        [](const ActiveTrajectoryPoint& point) { return point.view.rollDegrees; });
    result.fovDegrees = static_cast<float>(interpolate(points, active, elapsedSeconds,
        [](const ActiveTrajectoryPoint& point) { return point.view.fovDegrees; }));
    return result;
}
} // namespace bmw_camera
