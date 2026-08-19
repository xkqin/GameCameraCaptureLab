#pragma once

#include <cstddef>
#include <cstdint>
#include <windows.h>

namespace bmw_camera
{
constexpr wchar_t kMappingName[] = L"Local\\BmwCameraBridge.v1";
constexpr std::size_t kBufferSize = 64 * 1024;
constexpr std::size_t kPrecisePoseOffset = 128;
constexpr std::size_t kMetadataOffset = 256;
constexpr std::size_t kControlOffset = 512;
constexpr std::size_t kAbsolutePoseOffset = 768;
constexpr std::size_t kHudControlOffset = 896;
constexpr std::size_t kInputEventsOffset = 960;
constexpr std::size_t kTrajectoryOffset = 1024;

constexpr std::uint32_t kMetadataMagic = 0x42574D42; // BMWB
constexpr std::uint32_t kMetadataVersion = 9;
constexpr std::uint32_t kPrecisePoseMagic = 0x50574D42; // BMWP
constexpr std::uint32_t kPrecisePoseVersion = 1;
constexpr std::uint32_t kControlMagic = 0x43574D42; // BMWC
constexpr std::uint32_t kControlVersion = 1;
constexpr std::uint32_t kAbsolutePoseMagic = 0x41574D42; // BMWA
constexpr std::uint32_t kAbsolutePoseVersion = 1;
constexpr std::uint32_t kHudControlMagic = 0x48574D42; // BMWH
constexpr std::uint32_t kHudControlVersion = 1;
constexpr std::uint32_t kInputEventsMagic = 0x45574D42; // BMWE
constexpr std::uint32_t kInputEventsVersion = 1;
constexpr std::uint32_t kTrajectoryMagic = 0x54574D42; // BMWT
constexpr std::uint32_t kTrajectoryVersion = 1;

constexpr std::uint32_t kFlagBridgeLoaded = 1u << 0;
constexpr std::uint32_t kFlagHooksInstalled = 1u << 1;
constexpr std::uint32_t kFlagPoseObserved = 1u << 2;
constexpr std::uint32_t kFlagNativeControlReady = 1u << 3;
constexpr std::uint32_t kFlagInputCaptureReady = 1u << 4;
constexpr std::uint32_t kFlagHudControlReady = 1u << 5;
constexpr std::uint32_t kFlagWindowInputCapture = 1u << 6;

constexpr std::uint32_t kCapabilityForward = 1u << 0;
constexpr std::uint32_t kCapabilityRight = 1u << 1;
constexpr std::uint32_t kCapabilityUp = 1u << 2;
constexpr std::uint32_t kCapabilityYaw = 1u << 3;
constexpr std::uint32_t kCapabilityPitch = 1u << 4;
constexpr std::uint32_t kCapabilityRoll = 1u << 5;
constexpr std::uint32_t kCapabilityFov = 1u << 6;
constexpr std::uint32_t kCapabilityAbsolutePose = 1u << 7;
constexpr std::uint32_t kCapabilityHudVisibility = 1u;
constexpr std::uint32_t kAllPoseCapabilities =
    kCapabilityForward | kCapabilityRight | kCapabilityUp |
    kCapabilityYaw | kCapabilityPitch | kCapabilityRoll |
    kCapabilityFov | kCapabilityAbsolutePose;

enum class ControlState : LONG
{
    Idle = 0,
    Pending = 1,
    Applied = 2,
    Error = 3,
};

enum class ControlError : LONG
{
    None = 0,
    HooksUnavailable = 1,
    UnsupportedGameBuild = 2,
    CameraNotObserved = 3,
    InvalidCommand = 4,
    InternalFailure = 5,
};

constexpr std::uint32_t kTrajectoryCommandStart = 1;
constexpr std::uint32_t kTrajectoryCommandStop = 2;
constexpr std::uint32_t kTrajectoryStateIdle = 0;
constexpr std::uint32_t kTrajectoryStatePending = 1;
constexpr std::uint32_t kTrajectoryStatePlaying = 2;
constexpr std::uint32_t kTrajectoryStateCompleted = 3;
constexpr std::uint32_t kTrajectoryStateStopped = 4;
constexpr std::uint32_t kTrajectoryStateError = 5;
constexpr std::uint32_t kTrajectoryErrorNone = 0;
constexpr std::uint32_t kTrajectoryErrorUnavailable = 1;
constexpr std::uint32_t kTrajectoryErrorInvalidCommand = 2;
constexpr std::uint32_t kTrajectoryErrorInvalidKeyframes = 3;
constexpr std::uint32_t kTrajectoryErrorInternal = 4;

#pragma pack(push, 1)
struct BridgeMetadata
{
    std::uint32_t magic;
    std::uint32_t version;
    std::uint32_t size;
    std::uint32_t processId;
    volatile LONG hookCount;
    volatile LONG poseSampleCount;
    volatile LONG flags;
    std::uint32_t reserved;
    std::uint64_t loadTickMilliseconds;
};

struct CameraSnapshot
{
    unsigned char cameraEnabled;
    unsigned char movementLocked;
    unsigned char hudHidden;
    unsigned char inputCaptured;
    float fov;
    float x;
    float y;
    float z;
    float qx;
    float qy;
    float qz;
    float qw;
    float upX;
    float upY;
    float upZ;
    float rightX;
    float rightY;
    float rightZ;
    float forwardX;
    float forwardY;
    float forwardZ;
    float pitchRadians;
    float yawRadians;
    float rollRadians;
};

struct PrecisePose
{
    std::uint32_t magic;
    std::uint32_t version;
    std::uint32_t size;
    volatile LONG sequence;
    double x;
    double y;
    double z;
    double pitchDegrees;
    double yawDegrees;
    double rollDegrees;
    float fovDegrees;
    std::uint32_t flags;
};

struct NativeControl
{
    std::uint32_t magic;
    std::uint32_t version;
    std::uint32_t size;
    volatile LONG requestSequence;
    volatile LONG acknowledgeSequence;
    volatile LONG state;
    volatile LONG errorCode;
    volatile LONG capabilities;
    float moveForward;
    float moveRight;
    float moveUp;
    float yawRadians;
    float pitchRadians;
    float rollRadians;
    float fovDegrees;
    std::uint32_t setFov;
};

struct AbsolutePoseControl
{
    std::uint32_t magic;
    std::uint32_t version;
    std::uint32_t size;
    volatile LONG requestSequence;
    volatile LONG acknowledgeSequence;
    volatile LONG state;
    volatile LONG errorCode;
    volatile LONG capabilities;
    double x;
    double y;
    double z;
    float yawDegrees;
    float pitchDegrees;
    float rollDegrees;
    float fovDegrees;
    std::uint32_t enableCamera;
    std::uint32_t reserved[3];
};

struct HudControl
{
    std::uint32_t magic;
    std::uint32_t version;
    std::uint32_t size;
    volatile LONG requestSequence;
    volatile LONG acknowledgeSequence;
    volatile LONG state;
    volatile LONG errorCode;
    volatile LONG capabilities;
    volatile LONG hidden;
    std::uint32_t reserved[7];
};

struct InputEvents
{
    std::uint32_t magic;
    std::uint32_t version;
    std::uint32_t size;
    volatile LONG recordPointSequence;
    std::uint32_t reserved[12];
};

struct NativeTrajectory
{
    std::uint32_t magic;
    std::uint32_t version;
    std::uint32_t size;
    volatile LONG requestSequence;
    volatile LONG acknowledgeSequence;
    volatile LONG state;
    volatile LONG errorCode;
    std::uint32_t pointCount;
    float durationSeconds;
    float playbackHz;
    volatile LONG currentSegment;
    float elapsedSeconds;
    std::uint32_t command;
    std::uint32_t reserved[3];
};

struct TrajectoryKeyframe
{
    float timeSeconds;
    float x;
    float y;
    float z;
    float yawDegrees;
    float pitchDegrees;
    float rollDegrees;
    float fovDegrees;
};

// Exact FMinimalViewInfo prefix copied by the two Black Myth camera paths.
// The final padding makes each double-buffer slot 64 bytes, allowing the hook
// assembly to select a slot with one shift.
struct CameraView
{
    double x;
    double y;
    double z;
    double pitchDegrees;
    double yawDegrees;
    double rollDegrees;
    float fovDegrees;
    float padding[3];
};
#pragma pack(pop)

static_assert(sizeof(BridgeMetadata) == 40, "BridgeMetadata layout changed");
static_assert(sizeof(CameraSnapshot) == 84, "CameraSnapshot layout changed");
static_assert(sizeof(PrecisePose) == 72, "PrecisePose layout changed");
static_assert(sizeof(NativeControl) == 64, "NativeControl layout changed");
static_assert(sizeof(AbsolutePoseControl) == 88, "AbsolutePoseControl layout changed");
static_assert(sizeof(HudControl) == 64, "HudControl layout changed");
static_assert(sizeof(InputEvents) == 64, "InputEvents layout changed");
static_assert(sizeof(NativeTrajectory) == 64, "NativeTrajectory layout changed");
static_assert(sizeof(TrajectoryKeyframe) == 32, "TrajectoryKeyframe layout changed");
static_assert(sizeof(CameraView) == 64, "CameraView layout changed");
static_assert(offsetof(CameraView, fovDegrees) == 0x30, "FOV offset changed");

constexpr std::size_t kMaxTrajectoryKeyframes =
    (kBufferSize - kTrajectoryOffset - sizeof(NativeTrajectory)) /
    sizeof(TrajectoryKeyframe);
} // namespace bmw_camera
