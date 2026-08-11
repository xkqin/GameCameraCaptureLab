#include <windows.h>
#include <cstdint>
#include <cstring>

namespace
{
constexpr wchar_t kMappingName[] = L"Local\\BmwUuuPoseBridge.v1";
constexpr std::size_t kBufferSize = 8 * 1024;
constexpr std::size_t kCameraDataSize = 84;
constexpr std::size_t kMetadataOffset = 256;
constexpr std::uint32_t kMetadataMagic = 0x42574D42; // "BMWB"
constexpr std::uint32_t kMetadataVersion = 2;
constexpr std::uint32_t kFlagBridgeLoaded = 1u << 0;
constexpr std::uint32_t kFlagConnectCalled = 1u << 1;
constexpr std::uint32_t kFlagBufferRequested = 1u << 2;

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
#pragma pack(pop)

static_assert(sizeof(BridgeMetadata) == 40, "BridgeMetadata layout changed");

HANDLE g_mapping = nullptr;
std::uint8_t* g_cameraData = nullptr;
BridgeMetadata* g_metadata = nullptr;

void initializeSession()
{
    // The Python UI can keep the named mapping alive between game processes.
    // Always clear the old CameraToolsData when a new game-side bridge loads so
    // a stale pose can never be mistaken for a live connection.
    std::memset(g_cameraData, 0, kCameraDataSize);
    g_metadata = reinterpret_cast<BridgeMetadata*>(g_cameraData + kMetadataOffset);
    std::memset(g_metadata, 0, sizeof(BridgeMetadata));
    g_metadata->magic = kMetadataMagic;
    g_metadata->version = kMetadataVersion;
    g_metadata->size = static_cast<std::uint32_t>(sizeof(BridgeMetadata));
    g_metadata->processId = GetCurrentProcessId();
    g_metadata->loadTickMilliseconds = GetTickCount64();
    InterlockedExchange(&g_metadata->flags, static_cast<LONG>(kFlagBridgeLoaded));
    MemoryBarrier();
}

bool ensureBuffer()
{
    if (g_cameraData != nullptr)
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

    g_cameraData = static_cast<std::uint8_t*>(MapViewOfFile(
        g_mapping,
        FILE_MAP_ALL_ACCESS,
        0,
        0,
        kBufferSize));
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
    }
    if (g_mapping != nullptr)
    {
        CloseHandle(g_mapping);
        g_mapping = nullptr;
    }
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

// UUU looks up these optional IGCS Connector callbacks for Camera Path/ReShade
// state synchronization. This bridge only exposes pose data, so they are safe
// no-ops. Keeping the exports avoids a partial connector handshake.
extern "C" __declspec(dllexport) void addCameraPath() {}
extern "C" __declspec(dllexport) void appendStateSnapshotToPath(int) {}
extern "C" __declspec(dllexport) void appendStateSnapshotAfterSnapshotOnPath(int, int) {}
extern "C" __declspec(dllexport) void insertStateSnapshotBeforeSnapshotOnPath(int, int) {}
extern "C" __declspec(dllexport) void removeCameraPath(int) {}
extern "C" __declspec(dllexport) void removeStateSnapshotFromPath(int, int) {}
extern "C" __declspec(dllexport) void setReshadeState(int, int) {}
extern "C" __declspec(dllexport) void setReshadeStateInterpolated(int, int, int, float) {}
extern "C" __declspec(dllexport) void updateStateSnapshotOnPath(int, int) {}

BOOL WINAPI DllMain(HINSTANCE, DWORD reason, LPVOID)
{
    if (reason == DLL_PROCESS_ATTACH)
    {
        ensureBuffer();
    }
    else if (reason == DLL_PROCESS_DETACH)
    {
        releaseBuffer();
    }
    return TRUE;
}
