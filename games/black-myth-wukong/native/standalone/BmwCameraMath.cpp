#include "BmwCameraMath.h"

#include <algorithm>
#include <cmath>

namespace bmw_camera
{
namespace
{
constexpr double kPi = 3.1415926535897932384626433832795;

double radians(const double degrees)
{
    return degrees * kPi / 180.0;
}
}

float wrapDegrees(const float value)
{
    float wrapped = std::fmod(value + 180.0f, 360.0f);
    if (wrapped < 0.0f)
    {
        wrapped += 360.0f;
    }
    return wrapped - 180.0f;
}

float clampValue(const float value, const float minimum, const float maximum)
{
    return std::max(minimum, std::min(maximum, value));
}

bool finiteView(const CameraView& view)
{
    return std::isfinite(view.x) && std::isfinite(view.y) &&
        std::isfinite(view.z) && std::isfinite(view.pitchDegrees) &&
        std::isfinite(view.yawDegrees) && std::isfinite(view.rollDegrees) &&
        std::isfinite(view.fovDegrees) &&
        view.fovDegrees >= 1.0f && view.fovDegrees <= 179.0f;
}

void applyRelativeControl(CameraView& view, const NativeControl& command)
{
    const double pitch = radians(view.pitchDegrees);
    const double yaw = radians(view.yawDegrees);
    const double roll = radians(view.rollDegrees);
    const double cp = std::cos(pitch);
    const double sp = std::sin(pitch);
    const double cy = std::cos(yaw);
    const double sy = std::sin(yaw);
    const double cr = std::cos(roll);
    const double sr = std::sin(roll);

    const double forward[3] = {cp * cy, cp * sy, sp};
    const double right[3] = {
        sr * sp * cy - cr * sy,
        sr * sp * sy + cr * cy,
        -sr * cp,
    };
    const double up[3] = {
        -(cr * sp * cy + sr * sy),
        sr * cy - cr * sp * sy,
        cr * cp,
    };
    const double forwardStep = command.moveForward;
    const double rightStep = command.moveRight;
    const double upStep = command.moveUp;
    view.x += forward[0] * forwardStep + right[0] * rightStep + up[0] * upStep;
    view.y += forward[1] * forwardStep + right[1] * rightStep + up[1] * upStep;
    view.z += forward[2] * forwardStep + right[2] * rightStep + up[2] * upStep;
    view.yawDegrees = wrapDegrees(static_cast<float>(
        view.yawDegrees + command.yawRadians * 180.0 / kPi));
    view.pitchDegrees = clampValue(static_cast<float>(
        view.pitchDegrees + command.pitchRadians * 180.0 / kPi), -89.9f, 89.9f);
    view.rollDegrees = wrapDegrees(static_cast<float>(
        view.rollDegrees + command.rollRadians * 180.0 / kPi));
    if (command.setFov != 0)
    {
        view.fovDegrees = clampValue(command.fovDegrees, 1.0f, 179.0f);
    }
}

CameraSnapshot makeSnapshot(
    const CameraView& view,
    const bool cameraEnabled,
    const bool movementLocked,
    const bool hudHidden,
    const bool inputCaptured)
{
    const double pitch = radians(view.pitchDegrees);
    const double yaw = radians(view.yawDegrees);
    const double roll = radians(view.rollDegrees);
    const double cp = std::cos(pitch);
    const double sp = std::sin(pitch);
    const double cy = std::cos(yaw);
    const double sy = std::sin(yaw);
    const double cr = std::cos(roll);
    const double sr = std::sin(roll);

    CameraSnapshot result{};
    result.cameraEnabled = cameraEnabled ? 1 : 0;
    result.movementLocked = movementLocked ? 1 : 0;
    result.hudHidden = hudHidden ? 1 : 0;
    result.inputCaptured = inputCaptured ? 1 : 0;
    result.fov = view.fovDegrees;
    result.x = static_cast<float>(view.x);
    result.y = static_cast<float>(view.y);
    result.z = static_cast<float>(view.z);

    const double halfPitch = pitch * 0.5;
    const double halfYaw = yaw * 0.5;
    const double halfRoll = roll * 0.5;
    const double hcp = std::cos(halfPitch);
    const double hsp = std::sin(halfPitch);
    const double hcy = std::cos(halfYaw);
    const double hsy = std::sin(halfYaw);
    const double hcr = std::cos(halfRoll);
    const double hsr = std::sin(halfRoll);
    result.qx = static_cast<float>(hsr * hcp * hcy - hcr * hsp * hsy);
    result.qy = static_cast<float>(hcr * hsp * hcy + hsr * hcp * hsy);
    result.qz = static_cast<float>(hcr * hcp * hsy - hsr * hsp * hcy);
    result.qw = static_cast<float>(hcr * hcp * hcy + hsr * hsp * hsy);

    result.forwardX = static_cast<float>(cp * cy);
    result.forwardY = static_cast<float>(cp * sy);
    result.forwardZ = static_cast<float>(sp);
    result.rightX = static_cast<float>(sr * sp * cy - cr * sy);
    result.rightY = static_cast<float>(sr * sp * sy + cr * cy);
    result.rightZ = static_cast<float>(-sr * cp);
    result.upX = static_cast<float>(-(cr * sp * cy + sr * sy));
    result.upY = static_cast<float>(sr * cy - cr * sp * sy);
    result.upZ = static_cast<float>(cr * cp);
    result.pitchRadians = static_cast<float>(pitch);
    result.yawRadians = static_cast<float>(yaw);
    result.rollRadians = static_cast<float>(roll);
    return result;
}
} // namespace bmw_camera
