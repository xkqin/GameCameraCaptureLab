#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <cstdint>
#include <cstring>
#include <cmath>
#include <algorithm>
#include <cstdlib>
#include <vector>

#pragma comment(lib, "Ws2_32.lib")

namespace
{
constexpr wchar_t kMappingName[] = L"Local\\BmwUuuPoseBridge.v2";
constexpr wchar_t kUuuModuleName[] = L"UniversalUE5Unlocker.dll";
constexpr std::size_t kBufferSize = 64 * 1024;
constexpr std::size_t kCameraDataSize = 84;
constexpr std::size_t kMetadataOffset = 256;
constexpr std::size_t kControlOffset = 512;
constexpr std::size_t kTrajectoryOffset = 1024;
constexpr std::uint32_t kMetadataMagic = 0x42574D42; // "BMWB"
constexpr std::uint32_t kMetadataVersion = 7;
constexpr std::uint32_t kControlMagic = 0x43574D42; // "BMWC"
constexpr std::uint32_t kControlVersion = 1;
constexpr std::uint32_t kTrajectoryMagic = 0x54574D42; // "BMWT"
constexpr std::uint32_t kTrajectoryVersion = 1;
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
constexpr std::uint32_t kTrajectoryErrorInternalCall = 4;

constexpr std::uint32_t kFlagBridgeLoaded = 1u << 0;
constexpr std::uint32_t kFlagConnectCalled = 1u << 1;
constexpr std::uint32_t kFlagBufferRequested = 1u << 2;
constexpr std::uint32_t kFlagNativeControlReady = 1u << 3;

constexpr std::uint32_t kCapabilityForward = 1u << 0;
constexpr std::uint32_t kCapabilityRight = 1u << 1;
constexpr std::uint32_t kCapabilityUp = 1u << 2;
constexpr std::uint32_t kCapabilityYaw = 1u << 3;
constexpr std::uint32_t kCapabilityPitch = 1u << 4;
constexpr std::uint32_t kCapabilityRoll = 1u << 5;
constexpr std::uint32_t kCapabilityFov = 1u << 6;
constexpr std::uint32_t kAllPoseCapabilities =
    kCapabilityForward | kCapabilityRight | kCapabilityUp |
    kCapabilityYaw | kCapabilityPitch | kCapabilityRoll | kCapabilityFov;

// These offsets are intentionally locked to Universal UE5 Unlocker 5.8.21.
// A different image size is rejected instead of calling unknown code.
constexpr std::uint32_t kExpectedUuuImageSize = 0x32E000;
constexpr std::uintptr_t kStartScreenshotSessionRva = 0x17C30;
constexpr std::uintptr_t kMoveCameraPanoramaRva = 0x17C60;
constexpr std::uintptr_t kMoveCameraMultishotRva = 0x17C90;
constexpr std::uintptr_t kEndScreenshotSessionRva = 0x17CC0;
constexpr std::uintptr_t kControllerGlobalRva = 0x2FF958;
constexpr std::uintptr_t kCameraFeatureOffset = 0x1C48;
constexpr std::uintptr_t kGameSpecificCameraVtableRva = 0x2A8C20;
constexpr std::size_t kMoveForwardVtableIndex = 2; // +0x10
constexpr std::size_t kMoveRightVtableIndex = 3;   // +0x18
constexpr std::size_t kMoveUpVtableIndex = 4;      // +0x20
constexpr std::size_t kRotateYawVtableIndex = 8;   // +0x40
constexpr std::size_t kRotatePitchVtableIndex = 9; // +0x48
constexpr std::size_t kRotateRollVtableIndex = 10; // +0x50
constexpr std::size_t kSetFovVtableIndex = 11;     // +0x58
constexpr std::uintptr_t kExpectedCameraMethodRvas[] = {
    0x3A60, // move forward
    0x3A20, // move right
    0x3AA0, // move up
    0x13ECD0, // yaw (GameSpecific::Camera override)
    0x3BD0, // pitch
    0x3C70, // roll
    0x3CE0, // set FOV
};
constexpr std::uintptr_t kSettingsSingletonRva = 0x11B3A0;
constexpr std::uintptr_t kMovementSpeedGetterRva = 0x2AB0;
constexpr std::uintptr_t kUpMultiplierGetterRva = 0x2AA0;
constexpr std::uintptr_t kRotationSpeedGetterRva = 0x2AC0;
// UUU 5.8.21 stores the smoothed input from the previous camera command in
// these six GameSpecific::Camera fields. The movement/rotation methods blend
// the next input with these values before applying it, so a positioning move
// can otherwise be replayed as a large decaying impulse when timed playback
// begins. These offsets are version-locked together with the vtable RVAs.
constexpr std::uintptr_t kMoveRightSmoothingOffset = 0x5C;
constexpr std::uintptr_t kMoveForwardSmoothingOffset = 0x60;
constexpr std::uintptr_t kMoveUpSmoothingOffset = 0x64;
constexpr std::uintptr_t kPitchSmoothingOffset = 0x68;
constexpr std::uintptr_t kYawSmoothingOffset = 0x6C;
constexpr std::uintptr_t kRollSmoothingOffset = 0x70;

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
    UuuNotLoaded = 1,
    UnsupportedUuuVersion = 2,
    CameraFeatureUnavailable = 3,
    InvalidCommand = 4,
    InternalCallFailed = 5,
};

#pragma pack(push, 1)
struct BridgeMetadata
{
    std::uint32_t magic;
    std::uint32_t version;
    std::uint32_t size;
    std::uint32_t processId;
    volatile LONG connectCallCount;
    volatile LONG bufferRequestCount;
    volatile LONG flags;
    std::uint32_t reserved;
    std::uint64_t loadTickMilliseconds;
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

struct CameraSnapshot
{
    unsigned char cameraEnabled;
    unsigned char movementLocked;
    unsigned char reserved1;
    unsigned char reserved2;
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
#pragma pack(pop)

static_assert(sizeof(BridgeMetadata) == 40, "BridgeMetadata layout changed");
static_assert(sizeof(NativeControl) == 64, "NativeControl layout changed");
static_assert(sizeof(NativeTrajectory) == 64, "NativeTrajectory layout changed");
static_assert(sizeof(TrajectoryKeyframe) == 32, "TrajectoryKeyframe layout changed");
static_assert(sizeof(CameraSnapshot) == 84, "CameraSnapshot layout changed");
constexpr std::size_t kMaxTrajectoryKeyframes =
    (kBufferSize - kTrajectoryOffset - sizeof(NativeTrajectory)) /
    sizeof(TrajectoryKeyframe);

using MoveCameraFunction = void(__fastcall*)(void*, float);
using SettingsSingletonFunction = void*(__fastcall*)();
using SettingGetterFunction = float(__fastcall*)(void*);

constexpr float uuuRollArgument(const float requestedRollRadians)
{
    // UUU's GameSpecific::Camera roll method uses the opposite sign from its
    // public hotkey/pose convention (positive feedback roll is Tilt Right).
    return -requestedRollRadians;
}

static_assert(uuuRollArgument(1.0f) == -1.0f, "UUU roll sign changed");

HANDLE g_mapping = nullptr;
std::uint8_t* g_cameraData = nullptr;
BridgeMetadata* g_metadata = nullptr;
NativeControl* g_control = nullptr;
NativeTrajectory* g_trajectory = nullptr;
HANDLE g_workerThread = nullptr;
HANDLE g_relayThread = nullptr;
SOCKET g_relayListenSocket = INVALID_SOCKET;
volatile LONG g_stopRequested = 0;

// Linux/Proton relay.  The game and this DLL remain Windows binaries, but a
// native Linux capture UI cannot open Wine's named CreateFileMapping object.
// When BMW_BRIDGE_PORT is set, expose the same pose/control/trajectory blocks
// on loopback.  The relay never binds a non-loopback address.
constexpr char kRelayMagic[] = "BMWP";
constexpr std::uint8_t kRelayVersion = 1;
constexpr std::uint8_t kRelayReadState = 1;
constexpr std::uint8_t kRelayApplyControl = 2;
constexpr std::uint8_t kRelayStartTrajectory = 3;
constexpr std::uint8_t kRelayStopTrajectory = 4;
constexpr std::uint16_t kRelayStatusOk = 0;
constexpr std::uint16_t kRelayStatusError = 1;
constexpr std::size_t kRelayMaxPayload = 8 * 1024 * 1024;

#pragma pack(push, 1)
struct RelayHeader
{
    char magic[4];
    std::uint8_t version;
    std::uint8_t operation;
    std::uint16_t status;
    std::uint32_t payloadSize;
};
#pragma pack(pop)

static_assert(sizeof(RelayHeader) == 12, "RelayHeader layout changed");

unsigned short relayPort()
{
    char value[16]{};
    const DWORD length = GetEnvironmentVariableA(
        "BMW_BRIDGE_PORT", value, static_cast<DWORD>(sizeof(value)));
    if (length == 0 || length >= sizeof(value))
    {
        return 0;
    }
    char* end = nullptr;
    const long parsed = std::strtol(value, &end, 10);
    if (end == value || *end != '\0' || parsed < 1 || parsed > 65535)
    {
        return 0;
    }
    return static_cast<unsigned short>(parsed);
}

bool relaySendAll(SOCKET client, const void* data, const std::size_t size)
{
    const auto* bytes = static_cast<const char*>(data);
    std::size_t sent = 0;
    while (sent < size)
    {
        const int result = send(
            client,
            bytes + sent,
            static_cast<int>(std::min<std::size_t>(size - sent, 1 << 20)),
            0);
        if (result <= 0)
        {
            return false;
        }
        sent += static_cast<std::size_t>(result);
    }
    return true;
}

bool relayReceiveAll(SOCKET client, void* data, const std::size_t size)
{
    auto* bytes = static_cast<char*>(data);
    std::size_t received = 0;
    while (received < size)
    {
        const int result = recv(
            client,
            bytes + received,
            static_cast<int>(std::min<std::size_t>(size - received, 1 << 20)),
            0);
        if (result <= 0)
        {
            return false;
        }
        received += static_cast<std::size_t>(result);
    }
    return true;
}

bool relaySend(
    SOCKET client,
    const std::uint8_t operation,
    const std::uint16_t status,
    const void* payload,
    const std::size_t payloadSize)
{
    if (payloadSize > kRelayMaxPayload)
    {
        return false;
    }
    RelayHeader response{};
    std::memcpy(response.magic, kRelayMagic, sizeof(response.magic));
    response.version = kRelayVersion;
    response.operation = operation;
    response.status = status;
    response.payloadSize = static_cast<std::uint32_t>(payloadSize);
    if (!relaySendAll(client, &response, sizeof(response)))
    {
        return false;
    }
    return payloadSize == 0 || relaySendAll(client, payload, payloadSize);
}

bool relaySendError(SOCKET client, const std::uint8_t operation, const char* message)
{
    const char* value = message != nullptr ? message : "unknown relay error";
    return relaySend(
        client,
        operation,
        kRelayStatusError,
        value,
        std::strlen(value));
}

bool relaySendState(SOCKET client, const std::uint8_t operation)
{
    if (g_cameraData == nullptr || g_metadata == nullptr ||
        g_control == nullptr || g_trajectory == nullptr)
    {
        return relaySendError(client, operation, "bridge state is not initialized");
    }
    std::vector<std::uint8_t> state(
        sizeof(BridgeMetadata) + sizeof(CameraSnapshot) +
        sizeof(NativeControl) + sizeof(NativeTrajectory));
    std::size_t offset = 0;
    std::memcpy(state.data() + offset, g_metadata, sizeof(BridgeMetadata));
    offset += sizeof(BridgeMetadata);
    std::memcpy(state.data() + offset, g_cameraData, sizeof(CameraSnapshot));
    offset += sizeof(CameraSnapshot);
    std::memcpy(state.data() + offset, g_control, sizeof(NativeControl));
    offset += sizeof(NativeControl);
    std::memcpy(state.data() + offset, g_trajectory, sizeof(NativeTrajectory));
    return relaySend(client, operation, kRelayStatusOk, state.data(), state.size());
}

bool relayApplyControl(SOCKET client, const std::vector<std::uint8_t>& payload)
{
    if (g_control == nullptr || payload.size() != sizeof(std::uint32_t) + sizeof(NativeControl) - sizeof(std::uint32_t) * 8)
    {
        return relaySendError(client, kRelayApplyControl, "invalid native-control payload");
    }
    std::uint32_t sequence = 0;
    std::memcpy(&sequence, payload.data(), sizeof(sequence));
    std::memcpy(
        reinterpret_cast<std::uint8_t*>(g_control) + sizeof(std::uint32_t) * 8,
        payload.data() + sizeof(sequence),
        sizeof(NativeControl) - sizeof(std::uint32_t) * 8);
    MemoryBarrier();
    InterlockedExchange(&g_control->requestSequence, static_cast<LONG>(sequence));
    return relaySend(client, kRelayApplyControl, kRelayStatusOk, nullptr, 0);
}

bool relayStartTrajectory(SOCKET client, const std::vector<std::uint8_t>& payload)
{
    if (g_cameraData == nullptr || g_trajectory == nullptr ||
        payload.size() < sizeof(NativeTrajectory))
    {
        return relaySendError(client, kRelayStartTrajectory, "invalid trajectory payload");
    }
    NativeTrajectory command{};
    std::memcpy(&command, payload.data(), sizeof(command));
    const std::size_t expected = sizeof(NativeTrajectory) +
        static_cast<std::size_t>(command.pointCount) * sizeof(TrajectoryKeyframe);
    if (command.requestSequence == 0 || command.pointCount < 2 ||
        command.pointCount > kMaxTrajectoryKeyframes ||
        command.command != kTrajectoryCommandStart ||
        expected != payload.size())
    {
        return relaySendError(client, kRelayStartTrajectory, "invalid trajectory command");
    }
    std::memcpy(
        g_cameraData + kTrajectoryOffset + sizeof(NativeTrajectory),
        payload.data() + sizeof(NativeTrajectory),
        payload.size() - sizeof(NativeTrajectory));
    const LONG sequence = command.requestSequence;
    command.requestSequence = 0;
    std::memcpy(g_trajectory, &command, sizeof(command));
    MemoryBarrier();
    InterlockedExchange(&g_trajectory->requestSequence, sequence);
    return relaySend(client, kRelayStartTrajectory, kRelayStatusOk, nullptr, 0);
}

bool relayStopTrajectory(SOCKET client, const std::vector<std::uint8_t>& payload)
{
    if (g_trajectory == nullptr || payload.size() != sizeof(std::uint32_t))
    {
        return relaySendError(client, kRelayStopTrajectory, "invalid trajectory-stop payload");
    }
    std::uint32_t sequence = 0;
    std::memcpy(&sequence, payload.data(), sizeof(sequence));
    NativeTrajectory command{};
    std::memcpy(&command, g_trajectory, sizeof(command));
    command.command = kTrajectoryCommandStop;
    command.requestSequence = 0;
    std::memcpy(g_trajectory, &command, sizeof(command));
    MemoryBarrier();
    InterlockedExchange(&g_trajectory->requestSequence, static_cast<LONG>(sequence));
    return relaySend(client, kRelayStopTrajectory, kRelayStatusOk, nullptr, 0);
}

bool relayHandleRequest(SOCKET client)
{
    RelayHeader request{};
    if (!relayReceiveAll(client, &request, sizeof(request)))
    {
        return false;
    }
    if (std::memcmp(request.magic, kRelayMagic, sizeof(request.magic)) != 0 ||
        request.version != kRelayVersion ||
        request.payloadSize > kRelayMaxPayload)
    {
        return relaySendError(client, request.operation, "invalid relay header");
    }
    std::vector<std::uint8_t> payload(request.payloadSize);
    if (!payload.empty() && !relayReceiveAll(client, payload.data(), payload.size()))
    {
        return false;
    }
    switch (request.operation)
    {
    case kRelayReadState:
        if (!payload.empty())
        {
            return relaySendError(client, request.operation, "read-state payload must be empty");
        }
        return relaySendState(client, request.operation);
    case kRelayApplyControl:
        return relayApplyControl(client, payload);
    case kRelayStartTrajectory:
        return relayStartTrajectory(client, payload);
    case kRelayStopTrajectory:
        return relayStopTrajectory(client, payload);
    default:
        return relaySendError(client, request.operation, "unknown relay operation");
    }
}

DWORD WINAPI relayWorker(LPVOID)
{
    const unsigned short port = relayPort();
    WSADATA wsaData{};
    if (port == 0 || WSAStartup(MAKEWORD(2, 2), &wsaData) != 0)
    {
        return 0;
    }
    SOCKET server = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (server == INVALID_SOCKET)
    {
        WSACleanup();
        return 0;
    }
    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    address.sin_port = htons(port);
    BOOL reuse = TRUE;
    setsockopt(server, SOL_SOCKET, SO_REUSEADDR, reinterpret_cast<const char*>(&reuse), sizeof(reuse));
    if (bind(server, reinterpret_cast<const sockaddr*>(&address), sizeof(address)) == SOCKET_ERROR ||
        listen(server, 1) == SOCKET_ERROR)
    {
        closesocket(server);
        WSACleanup();
        return 0;
    }
    g_relayListenSocket = server;
    while (InterlockedCompareExchange(&g_stopRequested, 0, 0) == 0)
    {
        fd_set readable;
        FD_ZERO(&readable);
        FD_SET(server, &readable);
        timeval timeout{0, 500000};
        const int selected = select(0, &readable, nullptr, nullptr, &timeout);
        if (selected <= 0)
        {
            continue;
        }
        SOCKET client = accept(server, nullptr, nullptr);
        if (client == INVALID_SOCKET)
        {
            continue;
        }
        while (InterlockedCompareExchange(&g_stopRequested, 0, 0) == 0 &&
            relayHandleRequest(client))
        {
        }
        closesocket(client);
    }
    if (g_relayListenSocket != INVALID_SOCKET)
    {
        closesocket(g_relayListenSocket);
        g_relayListenSocket = INVALID_SOCKET;
    }
    WSACleanup();
    return 0;
}

bool finiteCommand(const NativeControl& command)
{
    return std::isfinite(command.moveForward) &&
        std::isfinite(command.moveRight) &&
        std::isfinite(command.moveUp) &&
        std::isfinite(command.yawRadians) &&
        std::isfinite(command.pitchRadians) &&
        std::isfinite(command.rollRadians) &&
        std::isfinite(command.fovDegrees);
}

bool addressInsideModule(const std::uintptr_t address, const std::uintptr_t base)
{
    return address >= base && address < base + kExpectedUuuImageSize;
}

bool expectedUuuImage(HMODULE module)
{
    if (module == nullptr)
    {
        return false;
    }
    const auto base = reinterpret_cast<std::uintptr_t>(module);
    __try
    {
        const auto dos = reinterpret_cast<const IMAGE_DOS_HEADER*>(base);
        if (dos->e_magic != IMAGE_DOS_SIGNATURE)
        {
            return false;
        }
        const auto nt = reinterpret_cast<const IMAGE_NT_HEADERS64*>(base + dos->e_lfanew);
        if (nt->Signature != IMAGE_NT_SIGNATURE ||
            nt->OptionalHeader.SizeOfImage != kExpectedUuuImageSize)
        {
            return false;
        }
        return reinterpret_cast<std::uintptr_t>(
                GetProcAddress(module, "IGCS_StartScreenshotSession")) ==
                base + kStartScreenshotSessionRva &&
            reinterpret_cast<std::uintptr_t>(
                GetProcAddress(module, "IGCS_MoveCameraPanorama")) ==
                base + kMoveCameraPanoramaRva &&
            reinterpret_cast<std::uintptr_t>(
                GetProcAddress(module, "IGCS_MoveCameraMultishot")) ==
                base + kMoveCameraMultishotRva &&
            reinterpret_cast<std::uintptr_t>(
                GetProcAddress(module, "IGCS_EndScreenshotSession")) ==
                base + kEndScreenshotSessionRva;
    }
    __except (EXCEPTION_EXECUTE_HANDLER)
    {
        return false;
    }
}

void* resolveCameraFeature(HMODULE module)
{
    if (!expectedUuuImage(module))
    {
        return nullptr;
    }
    const auto base = reinterpret_cast<std::uintptr_t>(module);
    __try
    {
        const auto controller = *reinterpret_cast<void**>(base + kControllerGlobalRva);
        if (controller == nullptr)
        {
            return nullptr;
        }
        const auto feature = *reinterpret_cast<void**>(
            reinterpret_cast<std::uintptr_t>(controller) + kCameraFeatureOffset);
        if (feature == nullptr)
        {
            return nullptr;
        }
        const auto vtable = *reinterpret_cast<void***>(feature);
        if (vtable == nullptr ||
            reinterpret_cast<std::uintptr_t>(vtable) !=
                base + kGameSpecificCameraVtableRva)
        {
            return nullptr;
        }
        constexpr std::size_t requiredSlots[] = {
            kMoveForwardVtableIndex,
            kMoveRightVtableIndex,
            kMoveUpVtableIndex,
            kRotateYawVtableIndex,
            kRotatePitchVtableIndex,
            kRotateRollVtableIndex,
            kSetFovVtableIndex,
        };
        for (std::size_t index = 0; index < _countof(requiredSlots); ++index)
        {
            const auto method = reinterpret_cast<std::uintptr_t>(vtable[requiredSlots[index]]);
            if (!addressInsideModule(method, base) ||
                method != base + kExpectedCameraMethodRvas[index])
            {
                return nullptr;
            }
        }
        return feature;
    }
    __except (EXCEPTION_EXECUTE_HANDLER)
    {
        return nullptr;
    }
}

float readSetting(
    const std::uintptr_t base,
    const std::uintptr_t getterRva,
    const float fallback)
{
    __try
    {
        const auto singleton = reinterpret_cast<SettingsSingletonFunction>(
            base + kSettingsSingletonRva)();
        if (singleton == nullptr)
        {
            return fallback;
        }
        const auto value = reinterpret_cast<SettingGetterFunction>(base + getterRva)(singleton);
        return std::isfinite(value) && std::fabs(value) > 0.000001f ? value : fallback;
    }
    __except (EXCEPTION_EXECUTE_HANDLER)
    {
        return fallback;
    }
}

bool applyNativeStep(HMODULE module, void* feature, const NativeControl& command)
{
    if (feature == nullptr || !finiteCommand(command))
    {
        return false;
    }
    const auto base = reinterpret_cast<std::uintptr_t>(module);
    const float movementSpeed = readSetting(base, kMovementSpeedGetterRva, 1.0f);
    const float upMultiplier = readSetting(base, kUpMultiplierGetterRva, 1.0f);
    const float rotationSpeed = readSetting(base, kRotationSpeedGetterRva, 1.0f);

    __try
    {
        const auto vtable = *reinterpret_cast<void***>(feature);
        if (std::fabs(command.moveForward) > 0.000001f)
        {
            reinterpret_cast<MoveCameraFunction>(vtable[kMoveForwardVtableIndex])(
                feature, command.moveForward / movementSpeed);
        }
        if (std::fabs(command.moveRight) > 0.000001f)
        {
            reinterpret_cast<MoveCameraFunction>(vtable[kMoveRightVtableIndex])(
                feature, command.moveRight / movementSpeed);
        }
        if (std::fabs(command.moveUp) > 0.000001f)
        {
            reinterpret_cast<MoveCameraFunction>(vtable[kMoveUpVtableIndex])(
                feature, command.moveUp / movementSpeed / upMultiplier);
        }
        if (std::fabs(command.yawRadians) > 0.000001f)
        {
            reinterpret_cast<MoveCameraFunction>(vtable[kRotateYawVtableIndex])(
                feature, command.yawRadians / rotationSpeed);
        }
        if (std::fabs(command.pitchRadians) > 0.000001f)
        {
            reinterpret_cast<MoveCameraFunction>(vtable[kRotatePitchVtableIndex])(
                feature, command.pitchRadians / rotationSpeed);
        }
        if (std::fabs(command.rollRadians) > 0.000001f)
        {
            reinterpret_cast<MoveCameraFunction>(vtable[kRotateRollVtableIndex])(
                feature, uuuRollArgument(command.rollRadians) / rotationSpeed);
        }
        if (command.setFov != 0 && command.fovDegrees > 0.0f)
        {
            reinterpret_cast<MoveCameraFunction>(vtable[kSetFovVtableIndex])(
                feature, command.fovDegrees);
        }
        return true;
    }
    __except (EXCEPTION_EXECUTE_HANDLER)
    {
        return false;
    }
}

bool clearUuuSmoothingState(void* feature)
{
    if (feature == nullptr)
    {
        return false;
    }
    __try
    {
        const auto address = reinterpret_cast<std::uintptr_t>(feature);
        *reinterpret_cast<float*>(address + kMoveRightSmoothingOffset) = 0.0f;
        *reinterpret_cast<float*>(address + kMoveForwardSmoothingOffset) = 0.0f;
        *reinterpret_cast<float*>(address + kMoveUpSmoothingOffset) = 0.0f;
        *reinterpret_cast<float*>(address + kPitchSmoothingOffset) = 0.0f;
        *reinterpret_cast<float*>(address + kYawSmoothingOffset) = 0.0f;
        *reinterpret_cast<float*>(address + kRollSmoothingOffset) = 0.0f;
        MemoryBarrier();
        return true;
    }
    __except (EXCEPTION_EXECUTE_HANDLER)
    {
        return false;
    }
}

struct ActiveTrajectoryPoint
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

bool finiteTrajectoryPoint(const TrajectoryKeyframe& point)
{
    return std::isfinite(point.timeSeconds) &&
        std::isfinite(point.x) &&
        std::isfinite(point.y) &&
        std::isfinite(point.z) &&
        std::isfinite(point.yawDegrees) &&
        std::isfinite(point.pitchDegrees) &&
        std::isfinite(point.rollDegrees) &&
        std::isfinite(point.fovDegrees) &&
        point.fovDegrees >= 1.0f && point.fovDegrees <= 179.0f;
}

bool readCameraSnapshot(CameraSnapshot& result)
{
    if (g_cameraData == nullptr)
    {
        return false;
    }
    CameraSnapshot first{};
    CameraSnapshot second{};
    for (int attempt = 0; attempt < 3; ++attempt)
    {
        std::memcpy(&first, g_cameraData, sizeof(first));
        std::memcpy(&second, g_cameraData, sizeof(second));
        if (std::memcmp(&first, &second, sizeof(first)) == 0)
        {
            result = second;
            return std::isfinite(result.x) && std::isfinite(result.y) &&
                std::isfinite(result.z) && std::isfinite(result.fov);
        }
    }
    return false;
}

template <typename Getter>
float trajectorySlope(
    const std::vector<ActiveTrajectoryPoint>& points,
    const int index,
    Getter getter)
{
    const int last = static_cast<int>(points.size()) - 1;
    if (last <= 0)
    {
        return 0.0f;
    }
    if (index <= 0)
    {
        // Start from rest. UUU applies its own input smoothing on top of this
        // curve, so a one-sided secant here creates an avoidable first-frame
        // step even after stale smoothing state has been cleared.
        return 0.0f;
    }
    if (index >= last)
    {
        // Finish at rest so the last planned increment approaches zero and
        // the terminal pose can be held without a snap.
        return 0.0f;
    }
    const float dt = points[index + 1].timeSeconds - points[index - 1].timeSeconds;
    return (getter(points[index + 1]) - getter(points[index - 1])) / dt;
}

template <typename Getter>
float interpolateTrajectoryValue(
    const std::vector<ActiveTrajectoryPoint>& points,
    const int segment,
    const float elapsed,
    Getter getter)
{
    const auto& first = points[segment];
    const auto& second = points[segment + 1];
    const float duration = second.timeSeconds - first.timeSeconds;
    const float u = clampValue((elapsed - first.timeSeconds) / duration, 0.0f, 1.0f);
    const float u2 = u * u;
    const float u3 = u2 * u;
    const float h00 = 2.0f * u3 - 3.0f * u2 + 1.0f;
    const float h10 = u3 - 2.0f * u2 + u;
    const float h01 = -2.0f * u3 + 3.0f * u2;
    const float h11 = u3 - u2;
    const float slope0 = trajectorySlope(points, segment, getter);
    const float slope1 = trajectorySlope(points, segment + 1, getter);
    return h00 * getter(first) + h10 * duration * slope0 +
        h01 * getter(second) + h11 * duration * slope1;
}

ActiveTrajectoryPoint sampleTrajectory(
    const std::vector<ActiveTrajectoryPoint>& points,
    const float elapsed,
    int& segment)
{
    if (elapsed <= points.front().timeSeconds)
    {
        segment = 0;
        return points.front();
    }
    if (elapsed >= points.back().timeSeconds)
    {
        segment = static_cast<int>(points.size()) - 1;
        return points.back();
    }
    while (segment + 1 < static_cast<int>(points.size()) - 1 &&
        elapsed > points[segment + 1].timeSeconds)
    {
        ++segment;
    }
    const int activeSegment = std::max(0, std::min(
        segment, static_cast<int>(points.size()) - 2));
    ActiveTrajectoryPoint result{};
    result.timeSeconds = elapsed;
    result.x = interpolateTrajectoryValue(points, activeSegment, elapsed,
        [](const ActiveTrajectoryPoint& point) { return point.x; });
    result.y = interpolateTrajectoryValue(points, activeSegment, elapsed,
        [](const ActiveTrajectoryPoint& point) { return point.y; });
    result.z = interpolateTrajectoryValue(points, activeSegment, elapsed,
        [](const ActiveTrajectoryPoint& point) { return point.z; });
    result.yawDegrees = interpolateTrajectoryValue(points, activeSegment, elapsed,
        [](const ActiveTrajectoryPoint& point) { return point.yawDegrees; });
    result.pitchDegrees = interpolateTrajectoryValue(points, activeSegment, elapsed,
        [](const ActiveTrajectoryPoint& point) { return point.pitchDegrees; });
    result.rollDegrees = interpolateTrajectoryValue(points, activeSegment, elapsed,
        [](const ActiveTrajectoryPoint& point) { return point.rollDegrees; });
    result.fovDegrees = interpolateTrajectoryValue(points, activeSegment, elapsed,
        [](const ActiveTrajectoryPoint& point) { return point.fovDegrees; });
    return result;
}

bool applyTrajectoryFrame(
    HMODULE module,
    void* feature,
    const ActiveTrajectoryPoint& target,
    const ActiveTrajectoryPoint& previousTarget)
{
    CameraSnapshot camera{};
    if (!readCameraSnapshot(camera) || !camera.cameraEnabled || camera.movementLocked)
    {
        return false;
    }
    NativeControl command{};
    float worldX = target.x - previousTarget.x;
    float worldY = target.y - previousTarget.y;
    float worldZ = target.z - previousTarget.z;
    float yawDegrees = target.yawDegrees - previousTarget.yawDegrees;
    float pitchDegrees = target.pitchDegrees - previousTarget.pitchDegrees;
    float rollDegrees = target.rollDegrees - previousTarget.rollDegrees;
    // Timed playback uses only the Hermite plan's incremental delta. Both
    // mid-path and terminal feedback are deliberately excluded: CameraToolsData
    // can lag the worker, and repeatedly correcting a stale terminal Pose caused
    // a visible end oscillation followed by a false playback failure.
    const float right = worldX * camera.rightX + worldY * camera.rightY + worldZ * camera.rightZ;
    const float up = worldX * camera.upX + worldY * camera.upY + worldZ * camera.upZ;
    const float forward = worldX * camera.forwardX + worldY * camera.forwardY + worldZ * camera.forwardZ;
    command.moveRight = clampValue(right, -256.0f, 256.0f);
    command.moveUp = clampValue(up, -256.0f, 256.0f);
    command.moveForward = clampValue(forward, -256.0f, 256.0f);
    command.yawRadians = clampValue(yawDegrees * 3.14159265358979323846f / 180.0f,
        -0.75f, 0.75f);
    command.pitchRadians = clampValue(pitchDegrees * 3.14159265358979323846f / 180.0f,
        -0.75f, 0.75f);
    command.rollRadians = clampValue(rollDegrees * 3.14159265358979323846f / 180.0f,
        -0.75f, 0.75f);
    command.fovDegrees = target.fovDegrees;
    command.setFov = std::fabs(target.fovDegrees - camera.fov) > 0.01f ? 1u : 0u;
    return applyNativeStep(module, feature, command);
}

bool loadTrajectory(
    const NativeTrajectory& header,
    std::vector<ActiveTrajectoryPoint>& points)
{
    if (g_cameraData == nullptr || header.pointCount < 2 ||
        header.pointCount > kMaxTrajectoryKeyframes ||
        !std::isfinite(header.durationSeconds) || header.durationSeconds <= 0.0f)
    {
        return false;
    }
    points.clear();
    points.reserve(header.pointCount);
    const auto* source = reinterpret_cast<const TrajectoryKeyframe*>(
        g_cameraData + kTrajectoryOffset + sizeof(NativeTrajectory));
    float firstTime = 0.0f;
    float previousTime = -1.0f;
    for (std::uint32_t index = 0; index < header.pointCount; ++index)
    {
        TrajectoryKeyframe raw{};
        std::memcpy(&raw, source + index, sizeof(raw));
        if (!finiteTrajectoryPoint(raw) ||
            (index > 0 && raw.timeSeconds <= previousTime))
        {
            return false;
        }
        if (index == 0)
        {
            firstTime = raw.timeSeconds;
        }
        ActiveTrajectoryPoint point{
            raw.timeSeconds - firstTime,
            raw.x,
            raw.y,
            raw.z,
            raw.yawDegrees,
            raw.pitchDegrees,
            raw.rollDegrees,
            raw.fovDegrees,
        };
        if (index > 0)
        {
            point.yawDegrees = points.back().yawDegrees +
                wrapDegrees(point.yawDegrees - points.back().yawDegrees);
            point.pitchDegrees = points.back().pitchDegrees +
                wrapDegrees(point.pitchDegrees - points.back().pitchDegrees);
            point.rollDegrees = points.back().rollDegrees +
                wrapDegrees(point.rollDegrees - points.back().rollDegrees);
        }
        points.push_back(point);
        previousTime = raw.timeSeconds;
    }
    const float actualDuration = points.back().timeSeconds;
    return actualDuration > 0.0f &&
        std::fabs(actualDuration - header.durationSeconds) < 0.05f;
}

void initializeSession()
{
    // The Python UI can keep the mapping alive between game processes. Clear
    // the previous session so stale pose/control state is never considered live.
    std::memset(g_cameraData, 0, kBufferSize);
    g_metadata = reinterpret_cast<BridgeMetadata*>(g_cameraData + kMetadataOffset);
    std::memset(g_metadata, 0, sizeof(BridgeMetadata));
    g_metadata->magic = kMetadataMagic;
    g_metadata->version = kMetadataVersion;
    g_metadata->size = static_cast<std::uint32_t>(sizeof(BridgeMetadata));
    g_metadata->processId = GetCurrentProcessId();
    g_metadata->loadTickMilliseconds = GetTickCount64();
    InterlockedExchange(&g_metadata->flags, static_cast<LONG>(kFlagBridgeLoaded));

    g_control = reinterpret_cast<NativeControl*>(g_cameraData + kControlOffset);
    std::memset(g_control, 0, sizeof(NativeControl));
    g_control->magic = kControlMagic;
    g_control->version = kControlVersion;
    g_control->size = static_cast<std::uint32_t>(sizeof(NativeControl));
    InterlockedExchange(&g_control->state, static_cast<LONG>(ControlState::Idle));
    g_trajectory = reinterpret_cast<NativeTrajectory*>(g_cameraData + kTrajectoryOffset);
    std::memset(g_trajectory, 0, sizeof(NativeTrajectory));
    g_trajectory->magic = kTrajectoryMagic;
    g_trajectory->version = kTrajectoryVersion;
    g_trajectory->size = static_cast<std::uint32_t>(sizeof(NativeTrajectory));
    InterlockedExchange(&g_trajectory->state, static_cast<LONG>(kTrajectoryStateIdle));
    MemoryBarrier();
}

bool ensureBuffer()
{
    if (g_cameraData != nullptr)
    {
        return true;
    }
    g_mapping = CreateFileMappingW(
        INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE, 0,
        static_cast<DWORD>(kBufferSize), kMappingName);
    if (g_mapping == nullptr)
    {
        return false;
    }
    g_cameraData = static_cast<std::uint8_t*>(MapViewOfFile(
        g_mapping, FILE_MAP_ALL_ACCESS, 0, 0, kBufferSize));
    if (g_cameraData == nullptr)
    {
        CloseHandle(g_mapping);
        g_mapping = nullptr;
        return false;
    }
    initializeSession();
    return true;
}

void releaseBuffer()
{
    if (g_cameraData != nullptr)
    {
        UnmapViewOfFile(g_cameraData);
        g_cameraData = nullptr;
        g_metadata = nullptr;
        g_control = nullptr;
        g_trajectory = nullptr;
    }
    if (g_mapping != nullptr)
    {
        CloseHandle(g_mapping);
        g_mapping = nullptr;
    }
}

DWORD WINAPI controlWorker(LPVOID)
{
    LONG lastSequence = 0;
    LONG lastTrajectorySequence = 0;
    bool trajectoryPlaying = false;
    float activeTrajectoryPlaybackHz = 60.0f;
    std::vector<ActiveTrajectoryPoint> trajectoryPoints;
    int trajectorySegment = 0;
    ActiveTrajectoryPoint previousTrajectoryTarget{};
    LARGE_INTEGER performanceFrequency{};
    LARGE_INTEGER trajectoryStart{};
    QueryPerformanceFrequency(&performanceFrequency);
    while (InterlockedCompareExchange(&g_stopRequested, 0, 0) == 0)
    {
        if (g_control == nullptr || g_metadata == nullptr || g_trajectory == nullptr)
        {
            Sleep(10);
            continue;
        }

        HMODULE uuu = GetModuleHandleW(kUuuModuleName);
        void* feature = resolveCameraFeature(uuu);
        ControlError readinessError = ControlError::None;
        if (uuu == nullptr)
        {
            readinessError = ControlError::UuuNotLoaded;
        }
        else if (!expectedUuuImage(uuu))
        {
            readinessError = ControlError::UnsupportedUuuVersion;
        }
        else if (feature == nullptr)
        {
            readinessError = ControlError::CameraFeatureUnavailable;
        }

        if (readinessError == ControlError::None)
        {
            InterlockedExchange(&g_control->capabilities, static_cast<LONG>(kAllPoseCapabilities));
            InterlockedOr(&g_metadata->flags, static_cast<LONG>(kFlagNativeControlReady));
            // Readiness failures are published without changing the command
            // state. Once the camera feature becomes available again, clear
            // that stale readiness error. Preserve a real command failure
            // (ControlState::Error) until the next command succeeds.
            const LONG state = InterlockedCompareExchange(&g_control->state, 0, 0);
            if (state != static_cast<LONG>(ControlState::Error))
            {
                InterlockedExchange(
                    &g_control->errorCode,
                    static_cast<LONG>(ControlError::None));
            }
        }
        else
        {
            InterlockedExchange(&g_control->capabilities, 0);
            InterlockedExchange(&g_control->errorCode, static_cast<LONG>(readinessError));
            InterlockedAnd(&g_metadata->flags, ~static_cast<LONG>(kFlagNativeControlReady));
        }

        // A trajectory request is committed by requestSequence, exactly like
        // the single-step command. Keyframes are copied before that sequence
        // is published, so the worker never consumes a half-written path.
        const LONG requestedTrajectory = InterlockedCompareExchange(
            &g_trajectory->requestSequence, 0, 0);
        if (requestedTrajectory != lastTrajectorySequence)
        {
            MemoryBarrier();
            NativeTrajectory command{};
            std::memcpy(&command, g_trajectory, sizeof(command));
            InterlockedExchange(
                &g_trajectory->state, static_cast<LONG>(kTrajectoryStatePending));
            std::uint32_t error = kTrajectoryErrorNone;
            std::uint32_t nextState = kTrajectoryStateStopped;

            if (command.magic != kTrajectoryMagic ||
                command.version != kTrajectoryVersion ||
                command.size != sizeof(NativeTrajectory) ||
                command.requestSequence != requestedTrajectory)
            {
                error = kTrajectoryErrorInvalidCommand;
                nextState = kTrajectoryStateError;
            }
            else if (command.command == kTrajectoryCommandStop)
            {
                trajectoryPlaying = false;
                trajectoryPoints.clear();
                if (feature != nullptr)
                {
                    clearUuuSmoothingState(feature);
                }
                nextState = kTrajectoryStateStopped;
            }
            else if (command.command != kTrajectoryCommandStart ||
                readinessError != ControlError::None ||
                !std::isfinite(command.playbackHz) ||
                command.playbackHz < 30.0f || command.playbackHz > 240.0f)
            {
                error = readinessError != ControlError::None
                    ? kTrajectoryErrorUnavailable
                    : kTrajectoryErrorInvalidCommand;
                trajectoryPlaying = false;
                trajectoryPoints.clear();
                nextState = kTrajectoryStateError;
            }
            else if (!loadTrajectory(command, trajectoryPoints))
            {
                error = kTrajectoryErrorInvalidKeyframes;
                trajectoryPlaying = false;
                nextState = kTrajectoryStateError;
            }
            else
            {
                if (!clearUuuSmoothingState(feature))
                {
                    error = kTrajectoryErrorInternalCall;
                    trajectoryPlaying = false;
                    trajectoryPoints.clear();
                    nextState = kTrajectoryStateError;
                }
                else
                {
                    QueryPerformanceCounter(&trajectoryStart);
                    trajectorySegment = 0;
                    previousTrajectoryTarget = trajectoryPoints.front();
                    activeTrajectoryPlaybackHz = command.playbackHz;
                    trajectoryPlaying = true;
                    nextState = kTrajectoryStatePlaying;
                    InterlockedExchange(&g_trajectory->currentSegment, 0);
                    g_trajectory->elapsedSeconds = 0.0f;
                }
            }

            InterlockedExchange(&g_trajectory->errorCode, static_cast<LONG>(error));
            MemoryBarrier();
            InterlockedExchange(
                &g_trajectory->state, static_cast<LONG>(nextState));
            InterlockedExchange(&g_trajectory->acknowledgeSequence, requestedTrajectory);
            lastTrajectorySequence = requestedTrajectory;
        }

        if (trajectoryPlaying)
        {
            if (readinessError != ControlError::None)
            {
                trajectoryPlaying = false;
                trajectoryPoints.clear();
                if (feature != nullptr)
                {
                    clearUuuSmoothingState(feature);
                }
                InterlockedExchange(
                    &g_trajectory->errorCode,
                    static_cast<LONG>(kTrajectoryErrorUnavailable));
                InterlockedExchange(
                    &g_trajectory->state, static_cast<LONG>(kTrajectoryStateError));
                Sleep(1);
                continue;
            }

            LARGE_INTEGER now{};
            QueryPerformanceCounter(&now);
            const double elapsed = performanceFrequency.QuadPart > 0
                ? static_cast<double>(now.QuadPart - trajectoryStart.QuadPart) /
                    static_cast<double>(performanceFrequency.QuadPart)
                : 0.0;
            const float duration = trajectoryPoints.back().timeSeconds;
            const float rawElapsedSeconds = std::max(0.0f, static_cast<float>(elapsed));
            const float elapsedSeconds = clampValue(rawElapsedSeconds, 0.0f, duration);
            int sampledSegment = trajectorySegment;
            const ActiveTrajectoryPoint target = sampleTrajectory(
                trajectoryPoints, elapsedSeconds, sampledSegment);
            const bool applied = applyTrajectoryFrame(
                uuu, feature, target, previousTrajectoryTarget);
            if (!applied)
            {
                trajectoryPlaying = false;
                trajectoryPoints.clear();
                clearUuuSmoothingState(feature);
                InterlockedExchange(
                    &g_trajectory->errorCode,
                    static_cast<LONG>(kTrajectoryErrorInternalCall));
                InterlockedExchange(
                    &g_trajectory->state, static_cast<LONG>(kTrajectoryStateError));
                Sleep(1);
                continue;
            }
            previousTrajectoryTarget = target;
            trajectorySegment = sampledSegment;
            g_trajectory->elapsedSeconds = elapsedSeconds;
            InterlockedExchange(&g_trajectory->currentSegment, sampledSegment);

            if (rawElapsedSeconds >= duration)
            {
                trajectoryPlaying = false;
                trajectoryPoints.clear();
                clearUuuSmoothingState(feature);
                g_trajectory->elapsedSeconds = duration;
                InterlockedExchange(
                    &g_trajectory->currentSegment,
                    static_cast<LONG>(g_trajectory->pointCount - 1));
                InterlockedExchange(
                    &g_trajectory->errorCode,
                    static_cast<LONG>(kTrajectoryErrorNone));
                InterlockedExchange(
                    &g_trajectory->state,
                    static_cast<LONG>(kTrajectoryStateCompleted));
                continue;
            }

            const DWORD frameSleep = static_cast<DWORD>(std::max(
                1.0, std::round(1000.0 / activeTrajectoryPlaybackHz)));
            Sleep(frameSleep);
            continue;
        }

        const LONG requested = InterlockedCompareExchange(&g_control->requestSequence, 0, 0);
        if (requested == lastSequence)
        {
            Sleep(1);
            continue;
        }

        MemoryBarrier();
        NativeControl command{};
        std::memcpy(&command, g_control, sizeof(command));
        InterlockedExchange(&g_control->state, static_cast<LONG>(ControlState::Pending));
        ControlError error = ControlError::None;
        bool applied = false;
        if (command.magic != kControlMagic || command.version != kControlVersion ||
            command.size != sizeof(NativeControl) || !finiteCommand(command))
        {
            error = ControlError::InvalidCommand;
        }
        else if (uuu == nullptr)
        {
            error = ControlError::UuuNotLoaded;
        }
        else if (!expectedUuuImage(uuu))
        {
            error = ControlError::UnsupportedUuuVersion;
        }
        else if (feature == nullptr)
        {
            error = ControlError::CameraFeatureUnavailable;
        }
        else
        {
            applied = applyNativeStep(uuu, feature, command);
            if (!applied)
            {
                error = ControlError::InternalCallFailed;
            }
        }

        InterlockedExchange(&g_control->errorCode, static_cast<LONG>(error));
        MemoryBarrier();
        InterlockedExchange(
            &g_control->state,
            static_cast<LONG>(applied ? ControlState::Applied : ControlState::Error));
        InterlockedExchange(&g_control->acknowledgeSequence, requested);
        lastSequence = requested;
    }
    return 0;
}
}

extern "C" __declspec(dllexport) bool connectFromCameraTools()
{
    if (!ensureBuffer())
    {
        return false;
    }
    InterlockedIncrement(&g_metadata->connectCallCount);
    InterlockedOr(&g_metadata->flags, static_cast<LONG>(kFlagConnectCalled));
    return true;
}

extern "C" __declspec(dllexport) unsigned char* getDataFromCameraToolsBuffer()
{
    if (!ensureBuffer())
    {
        return nullptr;
    }
    InterlockedIncrement(&g_metadata->bufferRequestCount);
    InterlockedOr(&g_metadata->flags, static_cast<LONG>(kFlagBufferRequested));
    return g_cameraData;
}

// Optional IGCS Connector callbacks used for ReShade state synchronization.
extern "C" __declspec(dllexport) void addCameraPath() {}
extern "C" __declspec(dllexport) void appendStateSnapshotToPath(int) {}
extern "C" __declspec(dllexport) void appendStateSnapshotAfterSnapshotOnPath(int, int) {}
extern "C" __declspec(dllexport) void insertStateSnapshotBeforeSnapshotOnPath(int, int) {}
extern "C" __declspec(dllexport) void removeCameraPath(int) {}
extern "C" __declspec(dllexport) void removeStateSnapshotFromPath(int, int) {}
extern "C" __declspec(dllexport) void setReshadeState(int, int) {}
extern "C" __declspec(dllexport) void setReshadeStateInterpolated(int, int, int, float) {}
extern "C" __declspec(dllexport) void updateStateSnapshotOnPath(int, int) {}

BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID)
{
    if (reason == DLL_PROCESS_ATTACH)
    {
        DisableThreadLibraryCalls(instance);
        if (ensureBuffer())
        {
            InterlockedExchange(&g_stopRequested, 0);
            g_workerThread = CreateThread(nullptr, 0, controlWorker, nullptr, 0, nullptr);
            if (relayPort() != 0)
            {
                g_relayThread = CreateThread(nullptr, 0, relayWorker, nullptr, 0, nullptr);
            }
        }
    }
    else if (reason == DLL_PROCESS_DETACH)
    {
        InterlockedExchange(&g_stopRequested, 1);
        if (g_relayListenSocket != INVALID_SOCKET)
        {
            closesocket(g_relayListenSocket);
            g_relayListenSocket = INVALID_SOCKET;
        }
        if (g_workerThread != nullptr)
        {
            CloseHandle(g_workerThread);
            g_workerThread = nullptr;
        }
        if (g_relayThread != nullptr)
        {
            CloseHandle(g_relayThread);
            g_relayThread = nullptr;
        }
        releaseBuffer();
    }
    return TRUE;
}
