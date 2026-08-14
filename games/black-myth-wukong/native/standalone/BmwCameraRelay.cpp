#include "BmwCameraRelay.h"

#include "BmwCameraBridgeProtocol.h"

#include <winsock2.h>
#include <ws2tcpip.h>

#include <algorithm>
#include <cstdlib>
#include <cstring>
#include <vector>

namespace bmw_camera
{
namespace
{
constexpr char kRelayMagic[] = "BMWP";
constexpr std::uint8_t kRelayVersion = 3;
constexpr std::uint8_t kRelayReadState = 1;
constexpr std::uint8_t kRelayApplyControl = 2;
constexpr std::uint8_t kRelayStartTrajectory = 3;
constexpr std::uint8_t kRelayStopTrajectory = 4;
constexpr std::uint8_t kRelaySetPose = 5;
constexpr std::uint8_t kRelaySetHud = 6;
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

std::uint8_t* g_relayMapping = nullptr;
volatile LONG* g_relayStopRequested = nullptr;
SOCKET g_relayListenSocket = INVALID_SOCKET;

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

bool sendAll(SOCKET client, const void* data, const std::size_t size)
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

bool receiveAll(SOCKET client, void* data, const std::size_t size)
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

bool sendResponse(
    SOCKET client,
    const std::uint8_t operation,
    const std::uint16_t status,
    const void* payload,
    const std::size_t payloadSize)
{
    RelayHeader header{};
    std::memcpy(header.magic, kRelayMagic, sizeof(header.magic));
    header.version = kRelayVersion;
    header.operation = operation;
    header.status = status;
    header.payloadSize = static_cast<std::uint32_t>(payloadSize);
    return sendAll(client, &header, sizeof(header)) &&
        (payloadSize == 0 || sendAll(client, payload, payloadSize));
}

bool sendError(SOCKET client, const std::uint8_t operation, const char* message)
{
    const char* value = message != nullptr ? message : "unknown relay error";
    return sendResponse(client, operation, kRelayStatusError, value, std::strlen(value));
}

bool sendState(SOCKET client)
{
    if (g_relayMapping == nullptr)
    {
        return sendError(client, kRelayReadState, "bridge mapping is unavailable");
    }
    constexpr std::size_t total = sizeof(BridgeMetadata) + sizeof(CameraSnapshot) +
        sizeof(PrecisePose) + 32 + 32 + sizeof(HudControl) + sizeof(NativeTrajectory);
    std::vector<std::uint8_t> state(total);
    std::size_t cursor = 0;
    auto append = [&](const void* source, const std::size_t size)
    {
        std::memcpy(state.data() + cursor, source, size);
        cursor += size;
    };
    append(g_relayMapping + kMetadataOffset, sizeof(BridgeMetadata));
    append(g_relayMapping, sizeof(CameraSnapshot));
    append(g_relayMapping + kPrecisePoseOffset, sizeof(PrecisePose));
    append(g_relayMapping + kControlOffset, 32);
    append(g_relayMapping + kAbsolutePoseOffset, 32);
    append(g_relayMapping + kHudControlOffset, sizeof(HudControl));
    append(g_relayMapping + kTrajectoryOffset, sizeof(NativeTrajectory));
    return sendResponse(client, kRelayReadState, kRelayStatusOk, state.data(), state.size());
}

bool applyControl(SOCKET client, const std::vector<std::uint8_t>& payload)
{
    if (g_relayMapping == nullptr ||
        payload.size() != sizeof(std::uint32_t) + sizeof(NativeControl) - 32)
    {
        return sendError(client, kRelayApplyControl, "invalid relative-control payload");
    }
    auto* control = reinterpret_cast<NativeControl*>(g_relayMapping + kControlOffset);
    std::uint32_t sequence = 0;
    std::memcpy(&sequence, payload.data(), sizeof(sequence));
    std::memcpy(
        reinterpret_cast<std::uint8_t*>(control) + 32,
        payload.data() + sizeof(sequence),
        sizeof(NativeControl) - 32);
    MemoryBarrier();
    InterlockedExchange(&control->requestSequence, static_cast<LONG>(sequence));
    return sendResponse(client, kRelayApplyControl, kRelayStatusOk, nullptr, 0);
}

bool setPose(SOCKET client, const std::vector<std::uint8_t>& payload)
{
    if (g_relayMapping == nullptr || payload.size() != sizeof(AbsolutePoseControl))
    {
        return sendError(client, kRelaySetPose, "invalid absolute-pose payload");
    }
    AbsolutePoseControl command{};
    std::memcpy(&command, payload.data(), sizeof(command));
    if (command.magic != kAbsolutePoseMagic ||
        command.version != kAbsolutePoseVersion ||
        command.size != sizeof(AbsolutePoseControl) || command.requestSequence <= 0)
    {
        return sendError(client, kRelaySetPose, "invalid absolute-pose command");
    }
    auto* destination = reinterpret_cast<AbsolutePoseControl*>(
        g_relayMapping + kAbsolutePoseOffset);
    const LONG sequence = command.requestSequence;
    command.requestSequence = 0;
    std::memcpy(destination, &command, sizeof(command));
    MemoryBarrier();
    InterlockedExchange(&destination->requestSequence, sequence);
    return sendResponse(client, kRelaySetPose, kRelayStatusOk, nullptr, 0);
}

bool setHud(SOCKET client, const std::vector<std::uint8_t>& payload)
{
    if (g_relayMapping == nullptr || payload.size() != sizeof(HudControl))
    {
        return sendError(client, kRelaySetHud, "invalid HUD-control payload");
    }
    HudControl command{};
    std::memcpy(&command, payload.data(), sizeof(command));
    if (command.magic != kHudControlMagic ||
        command.version != kHudControlVersion ||
        command.size != sizeof(HudControl) || command.requestSequence <= 0 ||
        (command.hidden != 0 && command.hidden != 1))
    {
        return sendError(client, kRelaySetHud, "invalid HUD-control command");
    }
    auto* destination = reinterpret_cast<HudControl*>(
        g_relayMapping + kHudControlOffset);
    const LONG sequence = command.requestSequence;
    command.requestSequence = 0;
    std::memcpy(destination, &command, sizeof(command));
    MemoryBarrier();
    InterlockedExchange(&destination->requestSequence, sequence);
    return sendResponse(client, kRelaySetHud, kRelayStatusOk, nullptr, 0);
}

bool startTrajectory(SOCKET client, const std::vector<std::uint8_t>& payload)
{
    if (g_relayMapping == nullptr || payload.size() < sizeof(NativeTrajectory))
    {
        return sendError(client, kRelayStartTrajectory, "invalid trajectory payload");
    }
    NativeTrajectory command{};
    std::memcpy(&command, payload.data(), sizeof(command));
    const std::size_t expected = sizeof(NativeTrajectory) +
        static_cast<std::size_t>(command.pointCount) * sizeof(TrajectoryKeyframe);
    if (command.requestSequence <= 0 || command.pointCount < 2 ||
        command.pointCount > kMaxTrajectoryKeyframes ||
        command.command != kTrajectoryCommandStart || expected != payload.size())
    {
        return sendError(client, kRelayStartTrajectory, "invalid trajectory command");
    }
    auto* destination = reinterpret_cast<NativeTrajectory*>(
        g_relayMapping + kTrajectoryOffset);
    std::memcpy(
        g_relayMapping + kTrajectoryOffset + sizeof(NativeTrajectory),
        payload.data() + sizeof(NativeTrajectory),
        payload.size() - sizeof(NativeTrajectory));
    const LONG sequence = command.requestSequence;
    command.requestSequence = 0;
    std::memcpy(destination, &command, sizeof(command));
    MemoryBarrier();
    InterlockedExchange(&destination->requestSequence, sequence);
    return sendResponse(client, kRelayStartTrajectory, kRelayStatusOk, nullptr, 0);
}

bool stopTrajectory(SOCKET client, const std::vector<std::uint8_t>& payload)
{
    if (g_relayMapping == nullptr || payload.size() != sizeof(std::uint32_t))
    {
        return sendError(client, kRelayStopTrajectory, "invalid trajectory-stop payload");
    }
    std::uint32_t sequence = 0;
    std::memcpy(&sequence, payload.data(), sizeof(sequence));
    auto* trajectory = reinterpret_cast<NativeTrajectory*>(
        g_relayMapping + kTrajectoryOffset);
    trajectory->command = kTrajectoryCommandStop;
    MemoryBarrier();
    InterlockedExchange(&trajectory->requestSequence, static_cast<LONG>(sequence));
    return sendResponse(client, kRelayStopTrajectory, kRelayStatusOk, nullptr, 0);
}

bool handleRequest(SOCKET client)
{
    RelayHeader request{};
    if (!receiveAll(client, &request, sizeof(request)))
    {
        return false;
    }
    if (std::memcmp(request.magic, kRelayMagic, sizeof(request.magic)) != 0 ||
        request.version != kRelayVersion || request.payloadSize > kRelayMaxPayload)
    {
        return sendError(client, request.operation, "invalid relay header");
    }
    std::vector<std::uint8_t> payload(request.payloadSize);
    if (!payload.empty() && !receiveAll(client, payload.data(), payload.size()))
    {
        return false;
    }
    switch (request.operation)
    {
    case kRelayReadState:
        return payload.empty()
            ? sendState(client)
            : sendError(client, request.operation, "read-state payload must be empty");
    case kRelayApplyControl:
        return applyControl(client, payload);
    case kRelayStartTrajectory:
        return startTrajectory(client, payload);
    case kRelayStopTrajectory:
        return stopTrajectory(client, payload);
    case kRelaySetPose:
        return setPose(client, payload);
    case kRelaySetHud:
        return setHud(client, payload);
    default:
        return sendError(client, request.operation, "unknown relay operation");
    }
}

DWORD WINAPI relayWorker(LPVOID)
{
    const unsigned short port = relayPort();
    WSADATA data{};
    if (port == 0 || WSAStartup(MAKEWORD(2, 2), &data) != 0)
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
    setsockopt(server, SOL_SOCKET, SO_REUSEADDR,
        reinterpret_cast<const char*>(&reuse), sizeof(reuse));
    if (bind(server, reinterpret_cast<const sockaddr*>(&address), sizeof(address)) == SOCKET_ERROR ||
        listen(server, 1) == SOCKET_ERROR)
    {
        closesocket(server);
        WSACleanup();
        return 0;
    }
    g_relayListenSocket = server;
    while (g_relayStopRequested != nullptr &&
        InterlockedCompareExchange(g_relayStopRequested, 0, 0) == 0)
    {
        fd_set readable;
        FD_ZERO(&readable);
        FD_SET(server, &readable);
        timeval timeout{0, 250000};
        if (select(0, &readable, nullptr, nullptr, &timeout) <= 0)
        {
            continue;
        }
        SOCKET client = accept(server, nullptr, nullptr);
        if (client == INVALID_SOCKET)
        {
            continue;
        }
        while (g_relayStopRequested != nullptr &&
            InterlockedCompareExchange(g_relayStopRequested, 0, 0) == 0 &&
            handleRequest(client))
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
} // namespace

HANDLE startRelay(std::uint8_t* mapping, volatile LONG* stopRequested)
{
    if (relayPort() == 0 || mapping == nullptr || stopRequested == nullptr)
    {
        return nullptr;
    }
    g_relayMapping = mapping;
    g_relayStopRequested = stopRequested;
    return CreateThread(nullptr, 0, relayWorker, nullptr, 0, nullptr);
}

void stopRelay(HANDLE& thread)
{
    if (g_relayListenSocket != INVALID_SOCKET)
    {
        closesocket(g_relayListenSocket);
        g_relayListenSocket = INVALID_SOCKET;
    }
    if (thread != nullptr)
    {
        WaitForSingleObject(thread, 2000);
        CloseHandle(thread);
        thread = nullptr;
    }
    g_relayMapping = nullptr;
    g_relayStopRequested = nullptr;
}
} // namespace bmw_camera
