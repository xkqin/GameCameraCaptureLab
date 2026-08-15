#include "BmwCameraBridgeProtocol.h"
#include "BmwCameraInput.h"
#include "BmwCameraMath.h"
#include "BmwCameraRelay.h"
#include "BmwCameraTrajectory.h"
#include "UeCameraProfile.h"

#include <windows.h>
#include <tlhelp32.h>

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iterator>
#include <string>
#include <utility>
#include <vector>

using namespace bmw_camera;
using namespace ue_camera_runtime;

extern "C" void BmwCameraHook1();
extern "C" void BmwCameraHook2();
extern "C" void BmwCameraHook3();
extern "C" void BmwHudHook();

extern "C"
{
__declspec(align(64)) CameraView g_bmwOverrideViews[2]{};
volatile LONG g_bmwActivePoseIndex = 0;
volatile LONG g_bmwCameraEnabled = 0;
CameraView g_bmwObservedView{};
volatile LONG g_bmwObservedLock = 0;
volatile LONG g_bmwObservedSequence = 0;
void* g_bmwLastCameraSource = nullptr;
void* g_bmwLastCameraDestination = nullptr;
void* g_bmwHook1Return = nullptr;
void* g_bmwHook2Return = nullptr;
void* g_bmwHook3Return = nullptr;
volatile LONG g_bmwHudHidden = 0;
void* g_bmwHudHookReturn = nullptr;
}

namespace
{
constexpr std::size_t kHookPatchSize = 14;
constexpr float kDefaultMoveSpeed = 600.0f;
constexpr float kDefaultRotationSpeed = 75.0f;
constexpr float kDefaultFovSpeed = 35.0f;
constexpr float kDefaultMouseSensitivity = 0.08f;
constexpr float kDefaultFastMultiplier = 5.0f;
constexpr float kDefaultSlowMultiplier = 0.25f;

HANDLE g_mapping = nullptr;
std::uint8_t* g_mappingData = nullptr;
BridgeMetadata* g_metadata = nullptr;
PrecisePose* g_precisePose = nullptr;
NativeControl* g_control = nullptr;
AbsolutePoseControl* g_absolutePose = nullptr;
HudControl* g_hudControl = nullptr;
NativeTrajectory* g_trajectory = nullptr;
HANDLE g_workerThread = nullptr;
HANDLE g_relayThread = nullptr;
volatile LONG g_stopRequested = 0;
volatile LONG g_movementLocked = 0;
const GameProfile* g_gameProfile = nullptr;

void publishRuntimeDiagnostic(const std::uint32_t value)
{
    if (g_metadata != nullptr)
    {
        InterlockedExchange(
            reinterpret_cast<volatile LONG*>(&g_metadata->reserved),
            static_cast<LONG>(value));
    }
}

struct HookRecord
{
    std::uint8_t* address{};
    std::array<std::uint8_t, kHookPatchSize> original{};
    bool installed{};
};

constexpr std::size_t kMaximumCameraHooks = 3;
constexpr std::size_t kHudHookRecordIndex = kMaximumCameraHooks;
HookRecord g_hooks[kMaximumCameraHooks + 1]{};

struct SuspendedThreads
{
    std::vector<HANDLE> handles;

    SuspendedThreads() = default;
    SuspendedThreads(const SuspendedThreads&) = delete;
    SuspendedThreads& operator=(const SuspendedThreads&) = delete;
    SuspendedThreads(SuspendedThreads&& other) noexcept
        : handles(std::move(other.handles))
    {
        other.handles.clear();
    }
    SuspendedThreads& operator=(SuspendedThreads&&) = delete;

    ~SuspendedThreads()
    {
        for (HANDLE handle : handles)
        {
            ResumeThread(handle);
            CloseHandle(handle);
        }
    }
};

SuspendedThreads suspendOtherThreads()
{
    SuspendedThreads result;
    const DWORD processId = GetCurrentProcessId();
    const DWORD currentThread = GetCurrentThreadId();
    HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0);
    if (snapshot == INVALID_HANDLE_VALUE)
    {
        return result;
    }
    THREADENTRY32 entry{};
    entry.dwSize = sizeof(entry);
    if (Thread32First(snapshot, &entry))
    {
        do
        {
            if (entry.th32OwnerProcessID != processId ||
                entry.th32ThreadID == currentThread)
            {
                continue;
            }
            HANDLE thread = OpenThread(THREAD_SUSPEND_RESUME, FALSE, entry.th32ThreadID);
            if (thread != nullptr && SuspendThread(thread) != static_cast<DWORD>(-1))
            {
                result.handles.push_back(thread);
            }
            else if (thread != nullptr)
            {
                CloseHandle(thread);
            }
        } while (Thread32Next(snapshot, &entry));
    }
    CloseHandle(snapshot);
    return result;
}

bool ensureMapping()
{
    if (g_mappingData != nullptr)
    {
        return true;
    }
    g_mapping = CreateFileMappingW(
        INVALID_HANDLE_VALUE,
        nullptr,
        PAGE_READWRITE,
        0,
        static_cast<DWORD>(kBufferSize),
        kMappingName);
    if (g_mapping == nullptr)
    {
        return false;
    }
    g_mappingData = static_cast<std::uint8_t*>(MapViewOfFile(
        g_mapping, FILE_MAP_ALL_ACCESS, 0, 0, kBufferSize));
    if (g_mappingData == nullptr)
    {
        CloseHandle(g_mapping);
        g_mapping = nullptr;
        return false;
    }
    std::memset(g_mappingData, 0, kBufferSize);
    g_metadata = reinterpret_cast<BridgeMetadata*>(g_mappingData + kMetadataOffset);
    g_precisePose = reinterpret_cast<PrecisePose*>(g_mappingData + kPrecisePoseOffset);
    g_control = reinterpret_cast<NativeControl*>(g_mappingData + kControlOffset);
    g_absolutePose = reinterpret_cast<AbsolutePoseControl*>(
        g_mappingData + kAbsolutePoseOffset);
    g_hudControl = reinterpret_cast<HudControl*>(g_mappingData + kHudControlOffset);
    g_trajectory = reinterpret_cast<NativeTrajectory*>(g_mappingData + kTrajectoryOffset);

    g_metadata->magic = kMetadataMagic;
    g_metadata->version = kMetadataVersion;
    g_metadata->size = sizeof(BridgeMetadata);
    g_metadata->processId = GetCurrentProcessId();
    g_metadata->loadTickMilliseconds = GetTickCount64();
    InterlockedExchange(&g_metadata->flags, kFlagBridgeLoaded);

    g_precisePose->magic = kPrecisePoseMagic;
    g_precisePose->version = kPrecisePoseVersion;
    g_precisePose->size = sizeof(PrecisePose);
    g_control->magic = kControlMagic;
    g_control->version = kControlVersion;
    g_control->size = sizeof(NativeControl);
    g_absolutePose->magic = kAbsolutePoseMagic;
    g_absolutePose->version = kAbsolutePoseVersion;
    g_absolutePose->size = sizeof(AbsolutePoseControl);
    g_absolutePose->capabilities = kCapabilityAbsolutePose;
    g_hudControl->magic = kHudControlMagic;
    g_hudControl->version = kHudControlVersion;
    g_hudControl->size = sizeof(HudControl);
    g_hudControl->hidden = 0;
    g_trajectory->magic = kTrajectoryMagic;
    g_trajectory->version = kTrajectoryVersion;
    g_trajectory->size = sizeof(NativeTrajectory);
    MemoryBarrier();
    return true;
}

void releaseMapping()
{
    if (g_mappingData != nullptr)
    {
        UnmapViewOfFile(g_mappingData);
        g_mappingData = nullptr;
        g_metadata = nullptr;
        g_precisePose = nullptr;
        g_control = nullptr;
        g_absolutePose = nullptr;
        g_hudControl = nullptr;
        g_trajectory = nullptr;
    }
    if (g_mapping != nullptr)
    {
        CloseHandle(g_mapping);
        g_mapping = nullptr;
    }
}

bool writeHook(HookRecord& record, std::uint8_t* address, void* detour)
{
    if (address == nullptr || detour == nullptr)
    {
        return false;
    }
    std::memcpy(record.original.data(), address, record.original.size());
    std::array<std::uint8_t, kHookPatchSize> patch{
        0xFF, 0x25, 0x00, 0x00, 0x00, 0x00,
    };
    const auto target = reinterpret_cast<std::uintptr_t>(detour);
    std::memcpy(patch.data() + 6, &target, sizeof(target));
    DWORD previousProtection = 0;
    if (!VirtualProtect(address, patch.size(), PAGE_EXECUTE_READWRITE, &previousProtection))
    {
        return false;
    }
    std::memcpy(address, patch.data(), patch.size());
    FlushInstructionCache(GetCurrentProcess(), address, patch.size());
    DWORD ignored = 0;
    VirtualProtect(address, patch.size(), previousProtection, &ignored);
    record.address = address;
    record.installed = true;
    return true;
}

void restoreHookRecords()
{
    for (auto& hook : g_hooks)
    {
        if (!hook.installed || hook.address == nullptr)
        {
            continue;
        }
        DWORD previousProtection = 0;
        if (VirtualProtect(
            hook.address, hook.original.size(), PAGE_EXECUTE_READWRITE, &previousProtection))
        {
            std::memcpy(hook.address, hook.original.data(), hook.original.size());
            FlushInstructionCache(GetCurrentProcess(), hook.address, hook.original.size());
            DWORD ignored = 0;
            VirtualProtect(hook.address, hook.original.size(), previousProtection, &ignored);
        }
        hook.installed = false;
    }
}

void restoreHooks()
{
    auto suspended = suspendOtherThreads();
    restoreHookRecords();
}

bool installHooks(bool& hudInstalled)
{
    hudInstalled = false;
    if (g_gameProfile == nullptr ||
        std::string(g_gameProfile->cameraHook.abi) !=
            "fminimal_view_info_copy_lwc_v1")
    {
        publishRuntimeDiagnostic(g_gameProfile == nullptr ? 2u : 3u);
        if (g_control != nullptr)
        {
            InterlockedExchange(
                &g_control->errorCode,
                static_cast<LONG>(ControlError::UnsupportedGameBuild));
        }
        return false;
    }
    const auto& cameraHook = g_gameProfile->cameraHook;
    const auto sites = locateProfileHooks(cameraHook);
    if (sites.size() < cameraHook.minMatches || sites.size() > cameraHook.maxMatches)
    {
        publishRuntimeDiagnostic(100u + static_cast<std::uint32_t>(sites.size()));
        if (g_control != nullptr)
        {
            InterlockedExchange(
                &g_control->errorCode,
                static_cast<LONG>(ControlError::UnsupportedGameBuild));
        }
        return false;
    }
    std::vector<std::uint8_t*> hudTargets;
    if (g_gameProfile->hudHook != nullptr &&
        std::string(g_gameProfile->hudHook->abi) == "black_myth_hud_opacity_v1")
    {
        hudTargets = locateProfileHooks(*g_gameProfile->hudHook);
    }
    auto suspended = suspendOtherThreads();
    g_bmwHook1Return = sites[0] + cameraHook.continuationOffset;
    if (!writeHook(g_hooks[0], sites[0], reinterpret_cast<void*>(&BmwCameraHook1)))
    {
        return false;
    }
    if (sites.size() == 2)
    {
        g_bmwHook2Return = sites[1] + cameraHook.continuationOffset;
        if (!writeHook(g_hooks[1], sites[1], reinterpret_cast<void*>(&BmwCameraHook2)))
        {
            restoreHookRecords();
            return false;
        }
    }
    else if (sites.size() == 3)
    {
        g_bmwHook2Return = sites[1] + cameraHook.continuationOffset;
        if (!writeHook(g_hooks[1], sites[1], reinterpret_cast<void*>(&BmwCameraHook2)))
        {
            restoreHookRecords();
            return false;
        }
        g_bmwHook3Return = sites[2] + cameraHook.continuationOffset;
        if (!writeHook(g_hooks[2], sites[2], reinterpret_cast<void*>(&BmwCameraHook3)))
        {
            restoreHookRecords();
            return false;
        }
    }
    if (g_gameProfile->hudHook != nullptr &&
        hudTargets.size() >= g_gameProfile->hudHook->minMatches &&
        hudTargets.size() <= g_gameProfile->hudHook->maxMatches)
    {
        g_bmwHudHookReturn = hudTargets[0] + g_gameProfile->hudHook->continuationOffset;
        hudInstalled = writeHook(
            g_hooks[kHudHookRecordIndex],
            hudTargets[0],
            reinterpret_cast<void*>(&BmwHudHook));
    }
    if (g_metadata != nullptr)
    {
        InterlockedExchange(&g_metadata->hookCount, static_cast<LONG>(sites.size()));
        InterlockedOr(&g_metadata->flags, kFlagHooksInstalled);
        if (hudInstalled)
        {
            InterlockedOr(&g_metadata->flags, kFlagHudControlReady);
        }
    }
    if (g_hudControl != nullptr)
    {
        InterlockedExchange(
            &g_hudControl->capabilities,
            hudInstalled ? static_cast<LONG>(kCapabilityHudVisibility) : 0);
        if (!hudInstalled)
        {
            InterlockedExchange(
                &g_hudControl->errorCode,
                static_cast<LONG>(ControlError::HooksUnavailable));
        }
    }
    publishRuntimeDiagnostic(200u + static_cast<std::uint32_t>(sites.size()));
    return true;
}

bool readObserved(CameraView& result)
{
    for (int attempt = 0; attempt < 5; ++attempt)
    {
        if (InterlockedCompareExchange(&g_bmwObservedLock, 0, 0) != 0)
        {
            YieldProcessor();
            continue;
        }
        const LONG first = InterlockedCompareExchange(&g_bmwObservedSequence, 0, 0);
        if (first <= 0)
        {
            return false;
        }
        std::memcpy(&result, &g_bmwObservedView, sizeof(result));
        MemoryBarrier();
        const LONG second = InterlockedCompareExchange(&g_bmwObservedSequence, 0, 0);
        if (first == second &&
            InterlockedCompareExchange(&g_bmwObservedLock, 0, 0) == 0 &&
            finiteView(result))
        {
            return true;
        }
    }
    return false;
}

CameraView readOverride()
{
    for (int attempt = 0; attempt < 3; ++attempt)
    {
        const LONG first = InterlockedCompareExchange(&g_bmwActivePoseIndex, 0, 0) & 1;
        CameraView result{};
        std::memcpy(&result, &g_bmwOverrideViews[first], sizeof(result));
        MemoryBarrier();
        const LONG second = InterlockedCompareExchange(&g_bmwActivePoseIndex, 0, 0) & 1;
        if (first == second)
        {
            return result;
        }
    }
    return g_bmwOverrideViews[0];
}

void publishOverride(const CameraView& view)
{
    const LONG current = InterlockedCompareExchange(&g_bmwActivePoseIndex, 0, 0) & 1;
    const LONG next = 1 - current;
    std::memcpy(&g_bmwOverrideViews[next], &view, sizeof(view));
    MemoryBarrier();
    InterlockedExchange(&g_bmwActivePoseIndex, next);
}

void enableCameraFromObserved()
{
    if (InterlockedCompareExchange(&g_bmwCameraEnabled, 0, 0) != 0)
    {
        return;
    }
    CameraView observed{};
    if (readObserved(observed))
    {
        publishOverride(observed);
        InterlockedExchange(&g_bmwCameraEnabled, 1);
    }
}

bool gameHasFocus()
{
    return inputGameHasFocus();
}

bool keyDown(const int virtualKey)
{
    return inputKeyDown(virtualKey);
}

bool keyPressed(const int virtualKey)
{
    return inputConsumePress(virtualKey);
}

float environmentFloat(const char* name, const float fallback)
{
    char value[64]{};
    const DWORD size = GetEnvironmentVariableA(name, value, sizeof(value));
    if (size == 0 || size >= sizeof(value))
    {
        return fallback;
    }
    char* end = nullptr;
    const float parsed = std::strtof(value, &end);
    return end != value && *end == '\0' && std::isfinite(parsed) && parsed > 0.0f
        ? parsed
        : fallback;
}

bool finiteNativeControl(const NativeControl& command)
{
    return std::isfinite(command.moveForward) && std::isfinite(command.moveRight) &&
        std::isfinite(command.moveUp) && std::isfinite(command.yawRadians) &&
        std::isfinite(command.pitchRadians) && std::isfinite(command.rollRadians) &&
        std::isfinite(command.fovDegrees);
}

bool finiteAbsolutePose(const AbsolutePoseControl& command)
{
    return std::isfinite(command.x) && std::isfinite(command.y) &&
        std::isfinite(command.z) && std::isfinite(command.yawDegrees) &&
        std::isfinite(command.pitchDegrees) && std::isfinite(command.rollDegrees) &&
        std::isfinite(command.fovDegrees) &&
        command.fovDegrees >= 1.0f && command.fovDegrees <= 179.0f;
}

CameraView viewFromAbsolute(const AbsolutePoseControl& command)
{
    CameraView view{};
    view.x = command.x;
    view.y = command.y;
    view.z = command.z;
    view.yawDegrees = command.yawDegrees;
    view.pitchDegrees = command.pitchDegrees;
    view.rollDegrees = command.rollDegrees;
    view.fovDegrees = command.fovDegrees;
    return view;
}

void publishSnapshot(const CameraView& view, const LONG observedSequence)
{
    if (g_mappingData == nullptr || g_precisePose == nullptr || g_metadata == nullptr)
    {
        return;
    }
    const bool enabled = InterlockedCompareExchange(&g_bmwCameraEnabled, 0, 0) != 0;
    const bool locked = InterlockedCompareExchange(&g_movementLocked, 0, 0) != 0;
    const bool hudHidden = InterlockedCompareExchange(&g_bmwHudHidden, 0, 0) != 0;
    const bool inputCaptured = inputCaptureActive();
    const CameraSnapshot snapshot = makeSnapshot(
        view, enabled, locked, hudHidden, inputCaptured);
    std::memcpy(g_mappingData, &snapshot, sizeof(snapshot));

    InterlockedIncrement(&g_precisePose->sequence);
    MemoryBarrier();
    g_precisePose->x = view.x;
    g_precisePose->y = view.y;
    g_precisePose->z = view.z;
    g_precisePose->pitchDegrees = view.pitchDegrees;
    g_precisePose->yawDegrees = view.yawDegrees;
    g_precisePose->rollDegrees = view.rollDegrees;
    g_precisePose->fovDegrees = view.fovDegrees;
    g_precisePose->flags = (enabled ? 1u : 0u) | (locked ? 2u : 0u) |
        (hudHidden ? 4u : 0u) | (inputCaptured ? 8u : 0u);
    MemoryBarrier();
    InterlockedIncrement(&g_precisePose->sequence);

    if (observedSequence > 0)
    {
        InterlockedExchange(&g_metadata->poseSampleCount, observedSequence);
        InterlockedOr(&g_metadata->flags, kFlagPoseObserved | kFlagNativeControlReady);
        InterlockedExchange(&g_control->capabilities, kAllPoseCapabilities);
        InterlockedExchange(&g_absolutePose->capabilities, kCapabilityAbsolutePose);
    }
}

void processInput(const double elapsedSeconds, const bool trajectoryPlaying)
{
    LONG mouseDeltaX = 0;
    LONG mouseDeltaY = 0;
    inputConsumeMouseDelta(mouseDeltaX, mouseDeltaY);
    if (!gameHasFocus())
    {
        return;
    }
    if (keyPressed(VK_DELETE) && g_hudControl != nullptr &&
        InterlockedCompareExchange(&g_hudControl->capabilities, 0, 0) != 0)
    {
        const LONG current = InterlockedCompareExchange(&g_bmwHudHidden, 0, 0);
        InterlockedExchange(&g_bmwHudHidden, current == 0 ? 1 : 0);
        InterlockedExchange(&g_hudControl->hidden, current == 0 ? 1 : 0);
    }
    if (keyPressed(VK_INSERT))
    {
        if (InterlockedCompareExchange(&g_bmwCameraEnabled, 0, 0) != 0)
        {
            InterlockedExchange(&g_bmwCameraEnabled, 0);
        }
        else
        {
            enableCameraFromObserved();
        }
    }
    if (keyPressed(VK_HOME))
    {
        const LONG current = InterlockedCompareExchange(&g_movementLocked, 0, 0);
        InterlockedExchange(&g_movementLocked, current == 0 ? 1 : 0);
    }
    if (trajectoryPlaying ||
        InterlockedCompareExchange(&g_bmwCameraEnabled, 0, 0) == 0 ||
        InterlockedCompareExchange(&g_movementLocked, 0, 0) != 0)
    {
        return;
    }

    static const float moveSpeed = environmentFloat(
        "BMW_CAMERA_MOVE_SPEED", kDefaultMoveSpeed);
    static const float rotateSpeed = environmentFloat(
        "BMW_CAMERA_ROTATION_SPEED", kDefaultRotationSpeed);
    static const float fovSpeed = environmentFloat(
        "BMW_CAMERA_FOV_SPEED", kDefaultFovSpeed);
    static const float mouseSensitivity = environmentFloat(
        "BMW_CAMERA_MOUSE_SENSITIVITY", kDefaultMouseSensitivity);
    static const float fastMultiplier = environmentFloat(
        "BMW_CAMERA_FAST_MULTIPLIER", kDefaultFastMultiplier);
    static const float slowMultiplier = environmentFloat(
        "BMW_CAMERA_SLOW_MULTIPLIER", kDefaultSlowMultiplier);
    float multiplier = 1.0f;
    if (keyDown(VK_SHIFT))
    {
        multiplier *= fastMultiplier;
    }
    if (keyDown(VK_CONTROL))
    {
        multiplier *= slowMultiplier;
    }
    const float moveStep = moveSpeed * multiplier * static_cast<float>(elapsedSeconds);
    const float angleStep = rotateSpeed * multiplier * static_cast<float>(elapsedSeconds);
    const float fovStep = fovSpeed * static_cast<float>(elapsedSeconds);
    NativeControl input{};
    input.yawRadians += static_cast<float>(mouseDeltaX) *
        mouseSensitivity * 0.0174532925199433f;
    input.pitchRadians -= static_cast<float>(mouseDeltaY) *
        mouseSensitivity * 0.0174532925199433f;
    if (keyDown('W') || keyDown(VK_NUMPAD8)) input.moveForward += moveStep;
    if (keyDown('S') || keyDown(VK_NUMPAD5)) input.moveForward -= moveStep;
    if (keyDown('D') || keyDown(VK_NUMPAD6)) input.moveRight += moveStep;
    if (keyDown('A') || keyDown(VK_NUMPAD4)) input.moveRight -= moveStep;
    if (keyDown('E') || keyDown(VK_NUMPAD7)) input.moveUp += moveStep;
    if (keyDown('Q') || keyDown(VK_NUMPAD9)) input.moveUp -= moveStep;
    if (keyDown(VK_RIGHT)) input.yawRadians += angleStep * 0.0174532925199433f;
    if (keyDown(VK_LEFT)) input.yawRadians -= angleStep * 0.0174532925199433f;
    if (keyDown(VK_UP)) input.pitchRadians += angleStep * 0.0174532925199433f;
    if (keyDown(VK_DOWN)) input.pitchRadians -= angleStep * 0.0174532925199433f;
    if (keyDown('C') || keyDown(VK_NUMPAD3)) input.rollRadians += angleStep * 0.0174532925199433f;
    if (keyDown('Z') || keyDown(VK_NUMPAD1)) input.rollRadians -= angleStep * 0.0174532925199433f;

    CameraView view = readOverride();
    if (keyDown(VK_ADD))
    {
        input.setFov = 1;
        input.fovDegrees = view.fovDegrees + fovStep;
    }
    else if (keyDown(VK_SUBTRACT))
    {
        input.setFov = 1;
        input.fovDegrees = view.fovDegrees - fovStep;
    }
    const bool changed = std::fabs(input.moveForward) > 0.0f ||
        std::fabs(input.moveRight) > 0.0f || std::fabs(input.moveUp) > 0.0f ||
        std::fabs(input.yawRadians) > 0.0f || std::fabs(input.pitchRadians) > 0.0f ||
        std::fabs(input.rollRadians) > 0.0f || input.setFov != 0;
    if (changed)
    {
        applyRelativeControl(view, input);
        publishOverride(view);
    }
}

DWORD WINAPI bridgeWorker(LPVOID)
{
    if (!ensureMapping())
    {
        return 0;
    }
    g_gameProfile = currentProcessProfile();
    publishRuntimeDiagnostic(g_gameProfile == nullptr ? 2u : 1u);
    bool hudInstalled = false;
    const bool hooksInstalled = installHooks(hudInstalled);
    const bool inputReady = startInputCapture();
    if (inputReady)
    {
        InterlockedOr(&g_metadata->flags, kFlagInputCaptureReady);
    }
    g_relayThread = startRelay(g_mappingData, &g_stopRequested);
    LONG lastControlSequence = 0;
    LONG lastAbsoluteSequence = 0;
    LONG lastTrajectorySequence = 0;
    LONG lastHudSequence = 0;
    bool trajectoryPlaying = false;
    float trajectoryPlaybackHz = 60.0f;
    std::vector<ActiveTrajectoryPoint> trajectoryPoints;
    int trajectorySegment = 0;
    LARGE_INTEGER frequency{};
    LARGE_INTEGER previousTick{};
    LARGE_INTEGER trajectoryStart{};
    QueryPerformanceFrequency(&frequency);
    QueryPerformanceCounter(&previousTick);

    while (InterlockedCompareExchange(&g_stopRequested, 0, 0) == 0)
    {
        LARGE_INTEGER now{};
        QueryPerformanceCounter(&now);
        double deltaSeconds = frequency.QuadPart > 0
            ? static_cast<double>(now.QuadPart - previousTick.QuadPart) /
                static_cast<double>(frequency.QuadPart)
            : 0.004;
        previousTick = now;
        deltaSeconds = std::max(0.0, std::min(deltaSeconds, 0.05));

        CameraView observed{};
        const bool hasObserved = readObserved(observed);
        const LONG observedSequence = InterlockedCompareExchange(
            &g_bmwObservedSequence, 0, 0);
        if (!inputCaptureReady())
        {
            InterlockedAnd(
                &g_metadata->flags,
                ~static_cast<LONG>(kFlagInputCaptureReady));
        }
        if (!hooksInstalled)
        {
            InterlockedExchange(&g_control->capabilities, 0);
            InterlockedExchange(&g_absolutePose->capabilities, 0);
        }

        const LONG requestedHud = InterlockedCompareExchange(
            &g_hudControl->requestSequence, 0, 0);
        if (requestedHud != lastHudSequence)
        {
            MemoryBarrier();
            HudControl command{};
            std::memcpy(&command, g_hudControl, sizeof(command));
            InterlockedExchange(
                &g_hudControl->state, static_cast<LONG>(ControlState::Pending));
            ControlError error = ControlError::None;
            bool applied = false;
            if (command.magic != kHudControlMagic ||
                command.version != kHudControlVersion ||
                command.size != sizeof(HudControl) ||
                command.requestSequence != requestedHud ||
                (command.hidden != 0 && command.hidden != 1))
            {
                error = ControlError::InvalidCommand;
            }
            else if (!hudInstalled)
            {
                error = ControlError::HooksUnavailable;
            }
            else
            {
                InterlockedExchange(&g_bmwHudHidden, command.hidden != 0 ? 1 : 0);
                applied = true;
            }
            InterlockedExchange(&g_hudControl->hidden,
                InterlockedCompareExchange(&g_bmwHudHidden, 0, 0));
            InterlockedExchange(
                &g_hudControl->errorCode, static_cast<LONG>(error));
            MemoryBarrier();
            InterlockedExchange(
                &g_hudControl->state,
                static_cast<LONG>(applied ? ControlState::Applied : ControlState::Error));
            InterlockedExchange(&g_hudControl->acknowledgeSequence, requestedHud);
            lastHudSequence = requestedHud;
        }

        const LONG requestedTrajectory = InterlockedCompareExchange(
            &g_trajectory->requestSequence, 0, 0);
        if (requestedTrajectory != lastTrajectorySequence)
        {
            MemoryBarrier();
            NativeTrajectory command{};
            std::memcpy(&command, g_trajectory, sizeof(command));
            InterlockedExchange(&g_trajectory->state, kTrajectoryStatePending);
            std::uint32_t error = kTrajectoryErrorNone;
            std::uint32_t nextState = kTrajectoryStateStopped;
            if (command.magic != kTrajectoryMagic || command.version != kTrajectoryVersion ||
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
                nextState = kTrajectoryStateStopped;
            }
            else if (command.command != kTrajectoryCommandStart || !hooksInstalled ||
                !hasObserved || !std::isfinite(command.playbackHz) ||
                command.playbackHz < 30.0f || command.playbackHz > 240.0f)
            {
                error = (!hooksInstalled || !hasObserved)
                    ? kTrajectoryErrorUnavailable
                    : kTrajectoryErrorInvalidCommand;
                trajectoryPlaying = false;
                trajectoryPoints.clear();
                nextState = kTrajectoryStateError;
            }
            else if (!loadTrajectory(g_mappingData, command, trajectoryPoints))
            {
                error = kTrajectoryErrorInvalidKeyframes;
                trajectoryPlaying = false;
                nextState = kTrajectoryStateError;
            }
            else
            {
                publishOverride(trajectoryPoints.front().view);
                InterlockedExchange(&g_bmwCameraEnabled, 1);
                QueryPerformanceCounter(&trajectoryStart);
                trajectorySegment = 0;
                trajectoryPlaybackHz = command.playbackHz;
                trajectoryPlaying = true;
                nextState = kTrajectoryStatePlaying;
                InterlockedExchange(&g_trajectory->currentSegment, 0);
                g_trajectory->elapsedSeconds = 0.0f;
            }
            InterlockedExchange(&g_trajectory->errorCode, error);
            MemoryBarrier();
            InterlockedExchange(&g_trajectory->state, nextState);
            InterlockedExchange(&g_trajectory->acknowledgeSequence, requestedTrajectory);
            lastTrajectorySequence = requestedTrajectory;
        }

        const LONG requestedAbsolute = InterlockedCompareExchange(
            &g_absolutePose->requestSequence, 0, 0);
        if (requestedAbsolute != lastAbsoluteSequence)
        {
            MemoryBarrier();
            AbsolutePoseControl command{};
            std::memcpy(&command, g_absolutePose, sizeof(command));
            InterlockedExchange(
                &g_absolutePose->state, static_cast<LONG>(ControlState::Pending));
            ControlError error = ControlError::None;
            bool applied = false;
            if (command.magic != kAbsolutePoseMagic ||
                command.version != kAbsolutePoseVersion ||
                command.size != sizeof(AbsolutePoseControl) ||
                command.requestSequence != requestedAbsolute || !finiteAbsolutePose(command))
            {
                error = ControlError::InvalidCommand;
            }
            else if (!hooksInstalled)
            {
                error = ControlError::HooksUnavailable;
            }
            else if (!hasObserved)
            {
                error = ControlError::CameraNotObserved;
            }
            else
            {
                trajectoryPlaying = false;
                if (g_trajectory->state == kTrajectoryStatePlaying)
                {
                    InterlockedExchange(&g_trajectory->state, kTrajectoryStateStopped);
                }
                publishOverride(viewFromAbsolute(command));
                if (command.enableCamera != 0)
                {
                    InterlockedExchange(&g_bmwCameraEnabled, 1);
                }
                applied = true;
            }
            InterlockedExchange(&g_absolutePose->errorCode, static_cast<LONG>(error));
            MemoryBarrier();
            InterlockedExchange(
                &g_absolutePose->state,
                static_cast<LONG>(applied ? ControlState::Applied : ControlState::Error));
            InterlockedExchange(&g_absolutePose->acknowledgeSequence, requestedAbsolute);
            lastAbsoluteSequence = requestedAbsolute;
        }

        const LONG requestedControl = InterlockedCompareExchange(
            &g_control->requestSequence, 0, 0);
        if (requestedControl != lastControlSequence)
        {
            MemoryBarrier();
            NativeControl command{};
            std::memcpy(&command, g_control, sizeof(command));
            InterlockedExchange(&g_control->state, static_cast<LONG>(ControlState::Pending));
            ControlError error = ControlError::None;
            bool applied = false;
            if (command.magic != kControlMagic || command.version != kControlVersion ||
                command.size != sizeof(NativeControl) || !finiteNativeControl(command))
            {
                error = ControlError::InvalidCommand;
            }
            else if (!hooksInstalled)
            {
                error = ControlError::HooksUnavailable;
            }
            else if (!hasObserved)
            {
                error = ControlError::CameraNotObserved;
            }
            else
            {
                trajectoryPlaying = false;
                if (InterlockedCompareExchange(&g_bmwCameraEnabled, 0, 0) == 0)
                {
                    publishOverride(observed);
                    InterlockedExchange(&g_bmwCameraEnabled, 1);
                }
                CameraView view = readOverride();
                applyRelativeControl(view, command);
                publishOverride(view);
                applied = true;
            }
            InterlockedExchange(&g_control->errorCode, static_cast<LONG>(error));
            MemoryBarrier();
            InterlockedExchange(
                &g_control->state,
                static_cast<LONG>(applied ? ControlState::Applied : ControlState::Error));
            InterlockedExchange(&g_control->acknowledgeSequence, requestedControl);
            lastControlSequence = requestedControl;
        }

        if (trajectoryPlaying)
        {
            const double elapsed = frequency.QuadPart > 0
                ? static_cast<double>(now.QuadPart - trajectoryStart.QuadPart) /
                    static_cast<double>(frequency.QuadPart)
                : 0.0;
            const float duration = trajectoryPoints.back().timeSeconds;
            const float rawElapsed = std::max(0.0f, static_cast<float>(elapsed));
            const float sampledElapsed = std::min(rawElapsed, duration);
            int sampledSegment = trajectorySegment;
            const CameraView target = sampleTrajectory(
                trajectoryPoints, sampledElapsed, sampledSegment);
            publishOverride(target);
            trajectorySegment = sampledSegment;
            g_trajectory->elapsedSeconds = sampledElapsed;
            InterlockedExchange(&g_trajectory->currentSegment, sampledSegment);
            if (rawElapsed >= duration)
            {
                // Deliberately hold the final pose. The next trajectory can
                // start from here without a return-to-start jump.
                publishOverride(trajectoryPoints.back().view);
                trajectoryPlaying = false;
                g_trajectory->elapsedSeconds = duration;
                InterlockedExchange(
                    &g_trajectory->currentSegment,
                    static_cast<LONG>(g_trajectory->pointCount - 1));
                InterlockedExchange(&g_trajectory->errorCode, kTrajectoryErrorNone);
                InterlockedExchange(&g_trajectory->state, kTrajectoryStateCompleted);
            }
        }

        processInput(deltaSeconds, trajectoryPlaying);
        CameraView snapshotView{};
        if (InterlockedCompareExchange(&g_bmwCameraEnabled, 0, 0) != 0)
        {
            snapshotView = readOverride();
        }
        else if (hasObserved)
        {
            snapshotView = observed;
        }
        if (finiteView(snapshotView))
        {
            publishSnapshot(snapshotView, observedSequence);
        }

        const DWORD sleepMilliseconds = trajectoryPlaying
            ? static_cast<DWORD>(std::max<double>(
                1.0, std::round(1000.0f / trajectoryPlaybackHz)))
            : 4;
        Sleep(sleepMilliseconds);
    }
    stopRelay(g_relayThread);
    stopInputCapture();
    restoreHooks();
    return 0;
}
} // namespace

extern "C" __declspec(dllexport) bool BMWCameraBridge_IsStandalone()
{
    return true;
}

extern "C" __declspec(dllexport) unsigned char* BMWCameraBridge_GetSharedBuffer()
{
    return ensureMapping() ? g_mappingData : nullptr;
}

BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID reserved)
{
    if (reason == DLL_PROCESS_ATTACH)
    {
        DisableThreadLibraryCalls(instance);
        InterlockedExchange(&g_stopRequested, 0);
        g_workerThread = CreateThread(nullptr, 0, bridgeWorker, nullptr, 0, nullptr);
        return g_workerThread != nullptr;
    }
    if (reason == DLL_PROCESS_DETACH)
    {
        InterlockedExchange(&g_stopRequested, 1);
        if (reserved == nullptr && g_workerThread != nullptr)
        {
            WaitForSingleObject(g_workerThread, 2000);
        }
        if (g_workerThread != nullptr)
        {
            CloseHandle(g_workerThread);
            g_workerThread = nullptr;
        }
        if (reserved == nullptr)
        {
            restoreHooks();
            releaseMapping();
        }
    }
    return TRUE;
}
