#include "BmwNativeDepth.h"

#include <d3d12.h>
#include <dxgi1_6.h>
#include <wrl/client.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <cwctype>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iterator>
#include <mutex>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <vector>

using Microsoft::WRL::ComPtr;
namespace fs = std::filesystem;

namespace bmw_camera
{
namespace
{
constexpr std::size_t kPresentVtableIndex = 8;
constexpr std::size_t kPresent1VtableIndex = 22;
constexpr std::size_t kExecuteCommandListsVtableIndex = 10;
constexpr std::size_t kCreateDepthStencilViewVtableIndex = 21;
constexpr std::size_t kResetCommandListVtableIndex = 10;
constexpr std::size_t kResourceBarrierVtableIndex = 26;
constexpr std::size_t kOmSetRenderTargetsVtableIndex = 46;
constexpr ULONGLONG kCandidateLifetimeMilliseconds = 2500;
constexpr ULONGLONG kRequestTimeoutMilliseconds = 6500;
constexpr ULONGLONG kReadbackTimeoutMilliseconds = 6500;
constexpr std::size_t kMaximumCandidates = 8;

using PresentFunction = HRESULT(STDMETHODCALLTYPE*)(IDXGISwapChain*, UINT, UINT);
using Present1Function = HRESULT(STDMETHODCALLTYPE*)(
    IDXGISwapChain1*, UINT, UINT, const DXGI_PRESENT_PARAMETERS*);
using ExecuteCommandListsFunction = void(STDMETHODCALLTYPE*)(
    ID3D12CommandQueue*, UINT, ID3D12CommandList* const*);
using CreateDepthStencilViewFunction = void(STDMETHODCALLTYPE*)(
    ID3D12Device*,
    ID3D12Resource*,
    const D3D12_DEPTH_STENCIL_VIEW_DESC*,
    D3D12_CPU_DESCRIPTOR_HANDLE);
using ResetCommandListFunction = HRESULT(STDMETHODCALLTYPE*)(
    ID3D12GraphicsCommandList*, ID3D12CommandAllocator*, ID3D12PipelineState*);
using ResourceBarrierFunction = void(STDMETHODCALLTYPE*)(
    ID3D12GraphicsCommandList*, UINT, const D3D12_RESOURCE_BARRIER*);
using OmSetRenderTargetsFunction = void(STDMETHODCALLTYPE*)(
    ID3D12GraphicsCommandList*,
    UINT,
    const D3D12_CPU_DESCRIPTOR_HANDLE*,
    BOOL,
    const D3D12_CPU_DESCRIPTOR_HANDLE*);

PresentFunction g_originalPresent = nullptr;
Present1Function g_originalPresent1 = nullptr;
ExecuteCommandListsFunction g_originalExecuteCommandLists = nullptr;
CreateDepthStencilViewFunction g_originalCreateDepthStencilView = nullptr;
ResetCommandListFunction g_originalResetCommandList = nullptr;
ResourceBarrierFunction g_originalResourceBarrier = nullptr;
OmSetRenderTargetsFunction g_originalOmSetRenderTargets = nullptr;
void** g_presentSlot = nullptr;
void** g_present1Slot = nullptr;
void** g_executeCommandListsSlot = nullptr;
void** g_createDepthStencilViewSlot = nullptr;
void** g_resetCommandListSlot = nullptr;
void** g_resourceBarrierSlot = nullptr;
void** g_omSetRenderTargetsSlot = nullptr;
std::mutex g_hookMutex;
bool g_hooksInstalled = false;

enum class DepthBindingEvidence : std::uint8_t
{
    none = 0,
    inferredUniqueTransition = 1,
    dsvDescriptor = 2,
};

const char* depthBindingEvidenceName(const DepthBindingEvidence evidence)
{
    switch (evidence)
    {
    case DepthBindingEvidence::dsvDescriptor:
        return "dsv_descriptor_map";
    case DepthBindingEvidence::inferredUniqueTransition:
        return "bound_dsv_with_unique_depth_transition";
    default:
        return "none";
    }
}

struct DepthCandidate
{
    ID3D12Resource* resource{};
    ID3D12CommandQueue* queue{};
    D3D12_RESOURCE_DESC description{};
    D3D12_RESOURCE_STATES state{D3D12_RESOURCE_STATE_COMMON};
    ULONGLONG lastSeenMilliseconds{};
    DepthBindingEvidence bindingEvidence{DepthBindingEvidence::none};
    bool queueConflict{};
};

std::mutex g_gpuMutex;
std::vector<DepthCandidate> g_candidates;
// Descriptor metadata is non-owning: retaining every DSV resource would pin
// large render targets in VRAM. A resource is AddRef'd only when its DSV is
// actually bound into a command list record.
std::unordered_map<SIZE_T, ID3D12Resource*> g_dsvResources;

struct RecordedTransition
{
    ID3D12Resource* resource{};
    D3D12_RESOURCE_STATES before{D3D12_RESOURCE_STATE_COMMON};
    D3D12_RESOURCE_STATES after{D3D12_RESOURCE_STATE_COMMON};
    UINT subresource{D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES};
};

struct CommandListRecord
{
    std::vector<RecordedTransition> transitions;
    std::vector<ID3D12Resource*> boundDepthResources;
    bool unresolvedDepthBinding{};
};

std::unordered_map<ID3D12GraphicsCommandList*, CommandListRecord> g_commandListRecords;

struct PendingRequest
{
    std::string id;
    fs::path path;
    ULONGLONG startedMilliseconds{};
};

struct CapturePayload
{
    PendingRequest request;
    bool completed{};
    std::string error;
    std::vector<std::uint8_t> raw;
    std::uint32_t width{};
    std::uint32_t height{};
    std::uint32_t rowPitch{};
    std::uint64_t slicePitch{};
    std::uint64_t capturedUnixNanoseconds{};
    DXGI_FORMAT resourceFormat{DXGI_FORMAT_UNKNOWN};
    DXGI_FORMAT copyFormat{DXGI_FORMAT_UNKNOWN};
    std::string decodeFormat;
    D3D12_RESOURCE_STATES sourceState{D3D12_RESOURCE_STATE_COMMON};
    std::uint64_t candidateAgeMilliseconds{};
    bool dsvBindingVerified{};
    std::string bindingEvidence{"none"};
    bool swapchainD3d12Verified{};
    bool resourceDeviceMatchesSwapchain{};
    bool queueDeviceMatchesSwapchain{};
    std::uint64_t presentHookCalls{};
    std::uint64_t present1HookCalls{};
    std::uint64_t executeHookCalls{};
    std::uint64_t createDsvHookCalls{};
    std::uint64_t resetHookCalls{};
    std::uint64_t resourceBarrierHookCalls{};
    std::uint64_t transitionBarrierCount{};
    std::uint64_t omSetRenderTargetsHookCalls{};
    std::uint64_t hookExceptionCount{};
};

struct InFlightCapture
{
    CapturePayload payload;
    ComPtr<ID3D12CommandAllocator> allocator;
    ComPtr<ID3D12GraphicsCommandList> commandList;
    ComPtr<ID3D12Resource> source;
    ComPtr<ID3D12Resource> readback;
    ComPtr<ID3D12Fence> fence;
    ComPtr<ID3D12CommandQueue> queue;
    UINT64 fenceValue{};
    UINT64 totalSize{};
    ULONGLONG submittedMilliseconds{};
    bool fenceSignalSucceeded{};
};

std::mutex g_requestMutex;
std::optional<PendingRequest> g_pendingRequest;
std::optional<CapturePayload> g_finishedCapture;
std::optional<InFlightCapture> g_inFlightCapture;
volatile LONG g_captureBusy = 0;
volatile LONG* g_externalStopRequested = nullptr;

std::atomic<std::uint64_t> g_presentHookCalls{0};
std::atomic<std::uint64_t> g_present1HookCalls{0};
std::atomic<std::uint64_t> g_executeHookCalls{0};
std::atomic<std::uint64_t> g_createDsvHookCalls{0};
std::atomic<std::uint64_t> g_resetHookCalls{0};
std::atomic<std::uint64_t> g_resourceBarrierHookCalls{0};
std::atomic<std::uint64_t> g_transitionBarrierCount{0};
std::atomic<std::uint64_t> g_omSetRenderTargetsHookCalls{0};
std::atomic<std::uint64_t> g_hookExceptionCount{0};
std::atomic<std::uint32_t> g_activeHookCalls{0};

struct HookActivity
{
    HookActivity()
    {
        g_activeHookCalls.fetch_add(1, std::memory_order_acq_rel);
    }

    ~HookActivity()
    {
        g_activeHookCalls.fetch_sub(1, std::memory_order_acq_rel);
    }
};

struct CaptureBusyReset
{
    ~CaptureBusyReset()
    {
        InterlockedExchange(&g_captureBusy, 0);
    }
};

bool stopRequested()
{
    return g_externalStopRequested != nullptr &&
        InterlockedCompareExchange(g_externalStopRequested, 0, 0) != 0;
}

void snapshotHookDiagnostics(CapturePayload& payload)
{
    payload.presentHookCalls = g_presentHookCalls.load(std::memory_order_relaxed);
    payload.present1HookCalls = g_present1HookCalls.load(std::memory_order_relaxed);
    payload.executeHookCalls = g_executeHookCalls.load(std::memory_order_relaxed);
    payload.createDsvHookCalls = g_createDsvHookCalls.load(std::memory_order_relaxed);
    payload.resetHookCalls = g_resetHookCalls.load(std::memory_order_relaxed);
    payload.resourceBarrierHookCalls =
        g_resourceBarrierHookCalls.load(std::memory_order_relaxed);
    payload.transitionBarrierCount =
        g_transitionBarrierCount.load(std::memory_order_relaxed);
    payload.omSetRenderTargetsHookCalls =
        g_omSetRenderTargetsHookCalls.load(std::memory_order_relaxed);
    payload.hookExceptionCount =
        g_hookExceptionCount.load(std::memory_order_relaxed);
}

std::string depthSelectionTimeoutDetail()
{
    if (g_presentHookCalls.load(std::memory_order_relaxed) == 0 &&
        g_present1HookCalls.load(std::memory_order_relaxed) == 0)
    {
        return "the real swapchain never reached the repository-owned Present hooks";
    }
    if (g_executeHookCalls.load(std::memory_order_relaxed) == 0)
    {
        return "the real D3D12 command queue never reached the repository-owned ExecuteCommandLists hook";
    }
    if (g_resourceBarrierHookCalls.load(std::memory_order_relaxed) == 0 ||
        g_transitionBarrierCount.load(std::memory_order_relaxed) == 0)
    {
        return "no legacy D3D12 depth transition was observed; the game may use enhanced barriers, which this safe backend does not guess around";
    }
    if (g_omSetRenderTargetsHookCalls.load(std::memory_order_relaxed) == 0)
    {
        return "no submitted command list exposed a depth-stencil binding";
    }
    return "no unambiguous full-frame DSV-bound depth resource matched the active swapchain";
}

std::string currentGameId()
{
    std::vector<wchar_t> executableBuffer(32768, L'\0');
    const DWORD length = GetModuleFileNameW(
        nullptr,
        executableBuffer.data(),
        static_cast<DWORD>(executableBuffer.size()));
    if (length == 0 || length >= executableBuffer.size())
    {
        return "unified";
    }
    std::wstring name = fs::path(
        std::wstring(executableBuffer.data(), length)).filename().wstring();
    std::transform(name.begin(), name.end(), name.begin(), [](const wchar_t value) {
        return static_cast<wchar_t>(std::towlower(value));
    });
    if (name == L"b1-win64-shipping.exe" || name == L"blackmythwukong.exe")
    {
        return "black-myth-wukong";
    }
    if (name == L"kingdomcome.exe")
    {
        return "kcd2";
    }
    return "unified";
}

fs::path channelRoot()
{
    std::vector<wchar_t> buffer(32768, L'\0');
    const DWORD length = GetEnvironmentVariableW(
        L"GAME_CAMERA_DEPTH_BRIDGE_DIR",
        buffer.data(),
        static_cast<DWORD>(buffer.size()));
    if (length > 0 && length < buffer.size())
    {
        return fs::path(std::wstring(buffer.data(), length));
    }
    const DWORD localLength = GetEnvironmentVariableW(
        L"LOCALAPPDATA", buffer.data(), static_cast<DWORD>(buffer.size()));
    const fs::path base = localLength > 0 && localLength < buffer.size()
        ? fs::path(std::wstring(buffer.data(), localLength))
        : fs::temp_directory_path();
    return base / L"GameCameraCaptureLab" / L"depth_bridge" /
        fs::path(currentGameId());
}

std::uint64_t unixTimeNanoseconds()
{
    return static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::system_clock::now().time_since_epoch()).count());
}

std::string jsonEscape(const std::string& value)
{
    std::ostringstream output;
    for (const unsigned char character : value)
    {
        switch (character)
        {
        case '\\': output << "\\\\"; break;
        case '"': output << "\\\""; break;
        case '\n': output << "\\n"; break;
        case '\r': output << "\\r"; break;
        case '\t': output << "\\t"; break;
        default:
            if (character < 0x20)
            {
                output << "\\u" << std::hex << std::setw(4) <<
                    std::setfill('0') << static_cast<unsigned int>(character) <<
                    std::dec << std::setfill(' ');
            }
            else
            {
                output << static_cast<char>(character);
            }
            break;
        }
    }
    return output.str();
}

void atomicTextWrite(const fs::path& target, const std::string& text)
{
    std::error_code error;
    fs::create_directories(target.parent_path(), error);
    const fs::path temporary = target.wstring() + L".tmp";
    {
        std::ofstream stream(temporary, std::ios::binary | std::ios::trunc);
        stream.write(text.data(), static_cast<std::streamsize>(text.size()));
        if (!stream)
        {
            throw std::runtime_error("failed to write native depth metadata");
        }
    }
    fs::remove(target, error);
    error.clear();
    fs::rename(temporary, target, error);
    if (error)
    {
        throw std::runtime_error("failed to publish native depth metadata");
    }
}

void writeRuntimeStatus(const char* state, const std::string& detail = {})
{
    try
    {
        std::ostringstream json;
        json << "{\n"
             << "  \"protocol\": \"game-camera-depth-bridge/v2\",\n"
             << "  \"backend\": \"native_d3d12_runtime\",\n"
             << "  \"process_id\": " << GetCurrentProcessId() << ",\n"
             << "  \"state\": \"" << jsonEscape(state) << "\",\n"
             << "  \"detail\": \"" << jsonEscape(detail) << "\",\n"
             << "  \"updated_unix_ns\": " << unixTimeNanoseconds() << "\n"
             << "}\n";
        atomicTextWrite(channelRoot() / L"runtime_status.json", json.str());
    }
    catch (...)
    {
    }
}

bool safeRequestId(const std::string& value)
{
    return !value.empty() && value.size() <= 96 &&
        std::all_of(value.begin(), value.end(), [](const unsigned char character) {
            return (character >= 'a' && character <= 'z') ||
                (character >= 'A' && character <= 'Z') ||
                (character >= '0' && character <= '9') ||
                character == '-' || character == '_';
        });
}

std::optional<PendingRequest> nextRequest()
{
    const fs::path requests = channelRoot() / L"requests";
    std::error_code error;
    fs::create_directories(requests, error);
    error.clear();
    for (const fs::directory_entry& entry : fs::directory_iterator(requests, error))
    {
        if (error)
        {
            break;
        }
        if (!entry.is_regular_file() || entry.path().extension() != L".request")
        {
            continue;
        }
        const std::string requestId = entry.path().stem().string();
        if (!safeRequestId(requestId))
        {
            continue;
        }
        return PendingRequest{requestId, entry.path(), GetTickCount64()};
    }
    return std::nullopt;
}

const char* resourceFormatName(const DXGI_FORMAT value)
{
    switch (value)
    {
    case DXGI_FORMAT_R32_TYPELESS: return "r32_typeless";
    case DXGI_FORMAT_D32_FLOAT: return "d32_float";
    case DXGI_FORMAT_R32_FLOAT: return "r32_float";
    case DXGI_FORMAT_R24G8_TYPELESS: return "r24_g8_typeless";
    case DXGI_FORMAT_D24_UNORM_S8_UINT: return "d24_unorm_s8_uint";
    case DXGI_FORMAT_R24_UNORM_X8_TYPELESS: return "r24_unorm_x8_uint";
    case DXGI_FORMAT_R16_TYPELESS: return "r16_typeless";
    case DXGI_FORMAT_D16_UNORM: return "d16_unorm";
    case DXGI_FORMAT_R16_UNORM: return "r16_unorm";
    case DXGI_FORMAT_R16_FLOAT: return "r16_float";
    case DXGI_FORMAT_R32G8X24_TYPELESS: return "r32_g8_typeless";
    case DXGI_FORMAT_D32_FLOAT_S8X24_UINT: return "d32_float_s8_uint";
    case DXGI_FORMAT_R32_FLOAT_X8X24_TYPELESS: return "r32_float_x8_uint";
    default: return "unsupported";
    }
}

std::string decodeFormatName(
    const DXGI_FORMAT resourceFormat,
    const DXGI_FORMAT copyFormat,
    const std::uint64_t rowSize,
    const std::uint32_t width)
{
    const std::uint64_t bytesPerPixel = width > 0 ? rowSize / width : 0;
    const DXGI_FORMAT preferred = copyFormat != DXGI_FORMAT_UNKNOWN
        ? copyFormat
        : resourceFormat;
    if ((preferred == DXGI_FORMAT_R32G8X24_TYPELESS ||
         preferred == DXGI_FORMAT_D32_FLOAT_S8X24_UINT ||
         preferred == DXGI_FORMAT_R32_FLOAT_X8X24_TYPELESS) &&
        bytesPerPixel <= 4)
    {
        return "r32_float";
    }
    return resourceFormatName(preferred);
}

bool depthResourceDescription(const D3D12_RESOURCE_DESC& description)
{
    if (description.Dimension != D3D12_RESOURCE_DIMENSION_TEXTURE2D ||
        description.Width == 0 || description.Height == 0 ||
        description.DepthOrArraySize != 1 || description.MipLevels != 1 ||
        description.SampleDesc.Count != 1 ||
        (description.Flags & D3D12_RESOURCE_FLAG_ALLOW_DEPTH_STENCIL) == 0)
    {
        return false;
    }
    return std::string(resourceFormatName(description.Format)) != "unsupported";
}

void releaseCandidate(DepthCandidate& candidate)
{
    if (candidate.resource != nullptr)
    {
        candidate.resource->Release();
        candidate.resource = nullptr;
    }
    if (candidate.queue != nullptr)
    {
        candidate.queue->Release();
        candidate.queue = nullptr;
    }
}

void pruneCandidatesLocked(const ULONGLONG now)
{
    auto iterator = g_candidates.begin();
    while (iterator != g_candidates.end())
    {
        if (now - iterator->lastSeenMilliseconds > kCandidateLifetimeMilliseconds)
        {
            releaseCandidate(*iterator);
            iterator = g_candidates.erase(iterator);
        }
        else
        {
            ++iterator;
        }
    }
}

bool containsDepthState(const D3D12_RESOURCE_STATES state)
{
    return (state & (D3D12_RESOURCE_STATE_DEPTH_WRITE |
        D3D12_RESOURCE_STATE_DEPTH_READ)) != 0;
}

void rememberDepthResourceLocked(
    ID3D12Resource* resource,
    ID3D12CommandQueue* queue,
    const D3D12_RESOURCE_STATES before,
    const D3D12_RESOURCE_STATES after,
    const DepthBindingEvidence bindingEvidence)
{
    if (resource == nullptr || queue == nullptr)
    {
        return;
    }
    const D3D12_RESOURCE_DESC description = resource->GetDesc();
    if (!depthResourceDescription(description))
    {
        return;
    }
    const ULONGLONG now = GetTickCount64();
    pruneCandidatesLocked(now);
    const auto existing = std::find_if(
        g_candidates.begin(), g_candidates.end(),
        [resource](const DepthCandidate& candidate) {
            return candidate.resource == resource;
        });
    if (existing != g_candidates.end())
    {
        existing->description = description;
        existing->state = after;
        existing->lastSeenMilliseconds = now;
        if (static_cast<std::uint8_t>(bindingEvidence) >
            static_cast<std::uint8_t>(existing->bindingEvidence))
        {
            existing->bindingEvidence = bindingEvidence;
        }
        if (existing->queue != queue)
        {
            existing->queueConflict = true;
        }
        return;
    }
    if (!containsDepthState(before) && !containsDepthState(after))
    {
        return;
    }
    if (g_candidates.size() >= kMaximumCandidates)
    {
        auto oldest = std::min_element(
            g_candidates.begin(), g_candidates.end(),
            [](const DepthCandidate& left, const DepthCandidate& right) {
                return left.lastSeenMilliseconds < right.lastSeenMilliseconds;
            });
        releaseCandidate(*oldest);
        g_candidates.erase(oldest);
    }
    resource->AddRef();
    queue->AddRef();
    g_candidates.push_back(DepthCandidate{
        resource, queue, description, after, now, bindingEvidence, false});
}

void clearCommandListRecordLocked(CommandListRecord& record)
{
    for (RecordedTransition& transition : record.transitions)
    {
        if (transition.resource != nullptr)
        {
            transition.resource->Release();
            transition.resource = nullptr;
        }
    }
    record.transitions.clear();
    for (ID3D12Resource*& resource : record.boundDepthResources)
    {
        if (resource != nullptr)
        {
            resource->Release();
            resource = nullptr;
        }
    }
    record.boundDepthResources.clear();
    record.unresolvedDepthBinding = false;
}

void clearCommandListRecord(ID3D12GraphicsCommandList* list)
{
    std::lock_guard<std::mutex> lock(g_gpuMutex);
    const auto iterator = g_commandListRecords.find(list);
    if (iterator == g_commandListRecords.end())
    {
        return;
    }
    clearCommandListRecordLocked(iterator->second);
}

void recordTransition(
    ID3D12GraphicsCommandList* list,
    const D3D12_RESOURCE_TRANSITION_BARRIER& transition)
{
    if (list == nullptr || transition.pResource == nullptr ||
        (transition.Subresource != D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES &&
         transition.Subresource != 0))
    {
        return;
    }
    std::lock_guard<std::mutex> lock(g_gpuMutex);
    CommandListRecord& record = g_commandListRecords[list];
    bool knownDepthResource =
        containsDepthState(transition.StateBefore) ||
        containsDepthState(transition.StateAfter);
    if (!knownDepthResource)
    {
        knownDepthResource = std::any_of(
            record.transitions.begin(),
            record.transitions.end(),
            [&transition](const RecordedTransition& recorded) {
                return recorded.resource == transition.pResource;
            });
    }
    if (!knownDepthResource)
    {
        knownDepthResource = std::any_of(
            g_candidates.begin(),
            g_candidates.end(),
            [&transition](const DepthCandidate& candidate) {
                return candidate.resource == transition.pResource;
            });
    }
    if (!knownDepthResource)
    {
        return;
    }
    transition.pResource->AddRef();
    record.transitions.push_back(RecordedTransition{
        transition.pResource,
        transition.StateBefore,
        transition.StateAfter,
        transition.Subresource});
    if (record.transitions.size() > 256)
    {
        RecordedTransition& oldest = record.transitions.front();
        oldest.resource->Release();
        record.transitions.erase(record.transitions.begin());
    }
}

void recordDepthBinding(
    ID3D12GraphicsCommandList* list,
    const D3D12_CPU_DESCRIPTOR_HANDLE* depthStencil)
{
    if (list == nullptr || depthStencil == nullptr || depthStencil->ptr == 0)
    {
        return;
    }
    std::lock_guard<std::mutex> lock(g_gpuMutex);
    CommandListRecord& record = g_commandListRecords[list];
    const auto descriptor = g_dsvResources.find(depthStencil->ptr);
    if (descriptor == g_dsvResources.end() || descriptor->second == nullptr)
    {
        record.unresolvedDepthBinding = true;
        return;
    }
    if (std::find(
            record.boundDepthResources.begin(),
            record.boundDepthResources.end(),
            descriptor->second) == record.boundDepthResources.end())
    {
        descriptor->second->AddRef();
        record.boundDepthResources.push_back(descriptor->second);
    }
}

void applyExecutedRecord(
    ID3D12CommandQueue* queue,
    ID3D12GraphicsCommandList* list)
{
    if (queue == nullptr || list == nullptr ||
        queue->GetDesc().Type != D3D12_COMMAND_LIST_TYPE_DIRECT)
    {
        return;
    }
    std::lock_guard<std::mutex> lock(g_gpuMutex);
    const auto iterator = g_commandListRecords.find(list);
    if (iterator == g_commandListRecords.end())
    {
        return;
    }
    CommandListRecord& record = iterator->second;
    ID3D12Resource* inferredBoundResource = nullptr;
    if (record.boundDepthResources.empty() && record.unresolvedDepthBinding)
    {
        ID3D12Resource* onlyDepthResource = nullptr;
        bool ambiguous = false;
        for (const RecordedTransition& transition : record.transitions)
        {
            if (onlyDepthResource == nullptr)
            {
                onlyDepthResource = transition.resource;
            }
            else if (onlyDepthResource != transition.resource)
            {
                ambiguous = true;
                break;
            }
        }
        if (!ambiguous)
        {
            inferredBoundResource = onlyDepthResource;
        }
    }
    for (const RecordedTransition& transition : record.transitions)
    {
        const bool directlyBound = std::find(
            record.boundDepthResources.begin(),
            record.boundDepthResources.end(),
            transition.resource) != record.boundDepthResources.end();
        const DepthBindingEvidence evidence = directlyBound
            ? DepthBindingEvidence::dsvDescriptor
            : (transition.resource == inferredBoundResource
                ? DepthBindingEvidence::inferredUniqueTransition
                : DepthBindingEvidence::none);
        rememberDepthResourceLocked(
            transition.resource,
            queue,
            transition.before,
            transition.after,
            evidence);
    }
    clearCommandListRecordLocked(record);
}

void rememberDepthStencilView(
    ID3D12Resource* resource,
    const D3D12_CPU_DESCRIPTOR_HANDLE destination)
{
    if (resource == nullptr || destination.ptr == 0)
    {
        return;
    }
    std::lock_guard<std::mutex> lock(g_gpuMutex);
    if (g_dsvResources.size() >= 512 &&
        g_dsvResources.find(destination.ptr) == g_dsvResources.end())
    {
        g_dsvResources.clear();
    }
    const auto existing = g_dsvResources.find(destination.ptr);
    if (existing != g_dsvResources.end())
    {
        if (existing->second == resource)
        {
            return;
        }
        existing->second = resource;
        return;
    }
    g_dsvResources.emplace(destination.ptr, resource);
}

template <typename Function>
bool patchVtableSlot(
    void* object,
    const std::size_t index,
    Function replacement,
    void*** storedSlot,
    Function* original)
{
    if (object == nullptr || storedSlot == nullptr || original == nullptr)
    {
        return false;
    }
    void** vtable = *reinterpret_cast<void***>(object);
    void** slot = &vtable[index];
    void* current = *slot;
    if (current == reinterpret_cast<void*>(replacement))
    {
        *storedSlot = slot;
        return *original != nullptr;
    }
    DWORD oldProtection = 0;
    if (!VirtualProtect(slot, sizeof(void*), PAGE_EXECUTE_READWRITE, &oldProtection))
    {
        return false;
    }
    *original = reinterpret_cast<Function>(current);
    InterlockedExchangePointer(
        reinterpret_cast<PVOID volatile*>(slot),
        reinterpret_cast<void*>(replacement));
    DWORD ignored = 0;
    VirtualProtect(slot, sizeof(void*), oldProtection, &ignored);
    FlushInstructionCache(GetCurrentProcess(), slot, sizeof(void*));
    *storedSlot = slot;
    return true;
}

template <typename Function>
void restoreVtableSlot(void** slot, Function replacement, Function original)
{
    if (slot == nullptr || original == nullptr ||
        *slot != reinterpret_cast<void*>(replacement))
    {
        return;
    }
    DWORD oldProtection = 0;
    if (!VirtualProtect(slot, sizeof(void*), PAGE_EXECUTE_READWRITE, &oldProtection))
    {
        return;
    }
    InterlockedExchangePointer(
        reinterpret_cast<PVOID volatile*>(slot),
        reinterpret_cast<void*>(original));
    DWORD ignored = 0;
    VirtualProtect(slot, sizeof(void*), oldProtection, &ignored);
    FlushInstructionCache(GetCurrentProcess(), slot, sizeof(void*));
}

HRESULT STDMETHODCALLTYPE depthPresentHook(
    IDXGISwapChain* swapchain, UINT syncInterval, UINT flags);
HRESULT STDMETHODCALLTYPE depthPresent1Hook(
    IDXGISwapChain1* swapchain,
    UINT syncInterval,
    UINT flags,
    const DXGI_PRESENT_PARAMETERS* parameters);
void STDMETHODCALLTYPE depthExecuteCommandListsHook(
    ID3D12CommandQueue* queue,
    UINT count,
    ID3D12CommandList* const* commandLists);
void STDMETHODCALLTYPE depthCreateDepthStencilViewHook(
    ID3D12Device* device,
    ID3D12Resource* resource,
    const D3D12_DEPTH_STENCIL_VIEW_DESC* description,
    D3D12_CPU_DESCRIPTOR_HANDLE destination);
HRESULT STDMETHODCALLTYPE depthResetCommandListHook(
    ID3D12GraphicsCommandList* list,
    ID3D12CommandAllocator* allocator,
    ID3D12PipelineState* initialState);
void STDMETHODCALLTYPE depthResourceBarrierHook(
    ID3D12GraphicsCommandList* list,
    UINT count,
    const D3D12_RESOURCE_BARRIER* barriers);
void STDMETHODCALLTYPE depthOmSetRenderTargetsHook(
    ID3D12GraphicsCommandList* list,
    UINT renderTargetCount,
    const D3D12_CPU_DESCRIPTOR_HANDLE* renderTargets,
    BOOL singleHandleRange,
    const D3D12_CPU_DESCRIPTOR_HANDLE* depthStencil);

void restoreHooks()
{
    std::lock_guard<std::mutex> lock(g_hookMutex);
    if (!g_hooksInstalled)
    {
        return;
    }
    restoreVtableSlot(
        g_omSetRenderTargetsSlot,
        depthOmSetRenderTargetsHook,
        g_originalOmSetRenderTargets);
    restoreVtableSlot(
        g_resourceBarrierSlot, depthResourceBarrierHook, g_originalResourceBarrier);
    restoreVtableSlot(
        g_resetCommandListSlot,
        depthResetCommandListHook,
        g_originalResetCommandList);
    restoreVtableSlot(
        g_createDepthStencilViewSlot,
        depthCreateDepthStencilViewHook,
        g_originalCreateDepthStencilView);
    restoreVtableSlot(
        g_executeCommandListsSlot,
        depthExecuteCommandListsHook,
        g_originalExecuteCommandLists);
    restoreVtableSlot(g_present1Slot, depthPresent1Hook, g_originalPresent1);
    restoreVtableSlot(g_presentSlot, depthPresentHook, g_originalPresent);
    g_hooksInstalled = false;
}

bool installHooks()
{
    std::lock_guard<std::mutex> lock(g_hookMutex);
    if (g_hooksInstalled)
    {
        return true;
    }

    ComPtr<IDXGIFactory4> factory;
    ComPtr<ID3D12Device> device;
    ComPtr<ID3D12CommandQueue> queue;
    ComPtr<ID3D12CommandAllocator> allocator;
    ComPtr<ID3D12GraphicsCommandList> list;
    ComPtr<IDXGISwapChain1> swapchain;
    if (FAILED(CreateDXGIFactory1(IID_PPV_ARGS(&factory))) ||
        FAILED(D3D12CreateDevice(
            nullptr, D3D_FEATURE_LEVEL_11_0, IID_PPV_ARGS(&device))))
    {
        return false;
    }
    D3D12_COMMAND_QUEUE_DESC queueDescription{};
    queueDescription.Type = D3D12_COMMAND_LIST_TYPE_DIRECT;
    if (FAILED(device->CreateCommandQueue(
            &queueDescription, IID_PPV_ARGS(&queue))) ||
        FAILED(device->CreateCommandAllocator(
            D3D12_COMMAND_LIST_TYPE_DIRECT, IID_PPV_ARGS(&allocator))) ||
        FAILED(device->CreateCommandList(
            0,
            D3D12_COMMAND_LIST_TYPE_DIRECT,
            allocator.Get(),
            nullptr,
            IID_PPV_ARGS(&list))))
    {
        return false;
    }
    DXGI_SWAP_CHAIN_DESC1 swapchainDescription{};
    swapchainDescription.Width = 2;
    swapchainDescription.Height = 2;
    swapchainDescription.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    swapchainDescription.SampleDesc.Count = 1;
    swapchainDescription.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
    swapchainDescription.BufferCount = 2;
    swapchainDescription.Scaling = DXGI_SCALING_STRETCH;
    swapchainDescription.SwapEffect = DXGI_SWAP_EFFECT_FLIP_DISCARD;
    swapchainDescription.AlphaMode = DXGI_ALPHA_MODE_PREMULTIPLIED;
    if (FAILED(factory->CreateSwapChainForComposition(
            queue.Get(), &swapchainDescription, nullptr, &swapchain)))
    {
        return false;
    }

    const bool presentPatched = patchVtableSlot(
        swapchain.Get(),
        kPresentVtableIndex,
        depthPresentHook,
        &g_presentSlot,
        &g_originalPresent);
    const bool present1Patched = patchVtableSlot(
        swapchain.Get(),
        kPresent1VtableIndex,
        depthPresent1Hook,
        &g_present1Slot,
        &g_originalPresent1);
    const bool executePatched = patchVtableSlot(
        queue.Get(),
        kExecuteCommandListsVtableIndex,
        depthExecuteCommandListsHook,
        &g_executeCommandListsSlot,
        &g_originalExecuteCommandLists);
    const bool dsvPatched = patchVtableSlot(
        device.Get(),
        kCreateDepthStencilViewVtableIndex,
        depthCreateDepthStencilViewHook,
        &g_createDepthStencilViewSlot,
        &g_originalCreateDepthStencilView);
    const bool resetPatched = patchVtableSlot(
        list.Get(),
        kResetCommandListVtableIndex,
        depthResetCommandListHook,
        &g_resetCommandListSlot,
        &g_originalResetCommandList);
    const bool barriersPatched = patchVtableSlot(
        list.Get(),
        kResourceBarrierVtableIndex,
        depthResourceBarrierHook,
        &g_resourceBarrierSlot,
        &g_originalResourceBarrier);
    const bool omPatched = patchVtableSlot(
        list.Get(),
        kOmSetRenderTargetsVtableIndex,
        depthOmSetRenderTargetsHook,
        &g_omSetRenderTargetsSlot,
        &g_originalOmSetRenderTargets);
    list->Close();
    if (!presentPatched || !present1Patched || !executePatched || !dsvPatched ||
        !resetPatched || !barriersPatched || !omPatched)
    {
        restoreVtableSlot(
            g_omSetRenderTargetsSlot,
            depthOmSetRenderTargetsHook,
            g_originalOmSetRenderTargets);
        restoreVtableSlot(
            g_resourceBarrierSlot, depthResourceBarrierHook, g_originalResourceBarrier);
        restoreVtableSlot(
            g_resetCommandListSlot,
            depthResetCommandListHook,
            g_originalResetCommandList);
        restoreVtableSlot(
            g_createDepthStencilViewSlot,
            depthCreateDepthStencilViewHook,
            g_originalCreateDepthStencilView);
        restoreVtableSlot(
            g_executeCommandListsSlot,
            depthExecuteCommandListsHook,
            g_originalExecuteCommandLists);
        restoreVtableSlot(g_present1Slot, depthPresent1Hook, g_originalPresent1);
        restoreVtableSlot(g_presentSlot, depthPresentHook, g_originalPresent);
        return false;
    }
    g_hooksInstalled = true;
    return true;
}

std::optional<DepthCandidate> chooseCandidate(
    ID3D12Device* swapchainDevice,
    const std::uint32_t targetWidth,
    const std::uint32_t targetHeight)
{
    if (swapchainDevice == nullptr || targetWidth == 0 || targetHeight == 0)
    {
        return std::nullopt;
    }
    const ULONGLONG now = GetTickCount64();
    std::lock_guard<std::mutex> lock(g_gpuMutex);
    pruneCandidatesLocked(now);
    double bestScore = -1.0e30;
    double secondBestScore = -1.0e30;
    DepthCandidate* best = nullptr;
    const double targetAspect = static_cast<double>(targetWidth) /
        static_cast<double>(targetHeight);
    for (DepthCandidate& candidate : g_candidates)
    {
        if (candidate.bindingEvidence == DepthBindingEvidence::none ||
            candidate.queue == nullptr || candidate.queueConflict)
        {
            continue;
        }
        ComPtr<ID3D12Device> resourceDevice;
        if (FAILED(candidate.resource->GetDevice(IID_PPV_ARGS(&resourceDevice))) ||
            resourceDevice.Get() != swapchainDevice)
        {
            continue;
        }
        const double width = static_cast<double>(candidate.description.Width);
        const double height = static_cast<double>(candidate.description.Height);
        const double aspect = width / height;
        const double aspectError = std::abs(aspect - targetAspect) / targetAspect;
        if (aspectError > 0.12)
        {
            continue;
        }
        const double areaRatio = (width * height) /
            (static_cast<double>(targetWidth) * static_cast<double>(targetHeight));
        if (areaRatio < 0.20 || areaRatio > 2.25)
        {
            continue;
        }
        double score = 500.0 - aspectError * 1000.0 -
            std::abs(std::log(areaRatio)) * 120.0;
        if (candidate.description.Width == targetWidth &&
            candidate.description.Height == targetHeight)
        {
            score += 500.0;
        }
        if (candidate.bindingEvidence == DepthBindingEvidence::dsvDescriptor)
        {
            score += 75.0;
        }
        score += std::max<double>(
            0.0,
            100.0 - static_cast<double>(now - candidate.lastSeenMilliseconds) * 0.08);
        if (score > bestScore)
        {
            secondBestScore = bestScore;
            bestScore = score;
            best = &candidate;
        }
        else if (score > secondBestScore)
        {
            secondBestScore = score;
        }
    }
    if (best == nullptr || secondBestScore > bestScore - 20.0)
    {
        return std::nullopt;
    }
    best->resource->AddRef();
    best->queue->AddRef();
    return *best;
}

D3D12_RESOURCE_DESC readbackBufferDescription(const std::uint64_t size)
{
    D3D12_RESOURCE_DESC description{};
    description.Dimension = D3D12_RESOURCE_DIMENSION_BUFFER;
    description.Alignment = 0;
    description.Width = size;
    description.Height = 1;
    description.DepthOrArraySize = 1;
    description.MipLevels = 1;
    description.Format = DXGI_FORMAT_UNKNOWN;
    description.SampleDesc.Count = 1;
    description.SampleDesc.Quality = 0;
    description.Layout = D3D12_TEXTURE_LAYOUT_ROW_MAJOR;
    description.Flags = D3D12_RESOURCE_FLAG_NONE;
    return description;
}

bool submitDepthReadback(
    IDXGISwapChain* swapchain,
    const PendingRequest& request,
    InFlightCapture& inFlight,
    bool& transientFailure)
{
    transientFailure = false;
    CapturePayload& payload = inFlight.payload;
    payload.request = request;
    snapshotHookDiagnostics(payload);
    if (swapchain == nullptr || g_originalResourceBarrier == nullptr ||
        g_originalExecuteCommandLists == nullptr)
    {
        payload.error = "native D3D12 hooks are not fully initialized";
        return false;
    }
    ComPtr<IDXGISwapChain1> swapchain1;
    ComPtr<ID3D12Device> device;
    if (FAILED(swapchain->QueryInterface(IID_PPV_ARGS(&swapchain1))) ||
        FAILED(swapchain->GetDevice(IID_PPV_ARGS(&device))))
    {
        payload.error = "active swapchain is not backed by D3D12";
        return false;
    }
    payload.swapchainD3d12Verified = true;
    DXGI_SWAP_CHAIN_DESC1 swapchainDescription{};
    if (FAILED(swapchain1->GetDesc1(&swapchainDescription)))
    {
        payload.error = "failed to read active swapchain dimensions";
        return false;
    }
    std::uint32_t targetWidth = swapchainDescription.Width;
    std::uint32_t targetHeight = swapchainDescription.Height;
    if (targetWidth == 0 || targetHeight == 0)
    {
        ComPtr<ID3D12Resource> backBuffer;
        if (FAILED(swapchain->GetBuffer(0, IID_PPV_ARGS(&backBuffer))))
        {
            payload.error = "failed to resolve the active swapchain dimensions";
            return false;
        }
        const D3D12_RESOURCE_DESC backBufferDescription = backBuffer->GetDesc();
        targetWidth = static_cast<std::uint32_t>(backBufferDescription.Width);
        targetHeight = backBufferDescription.Height;
    }

    std::optional<DepthCandidate> selected = chooseCandidate(
        device.Get(), targetWidth, targetHeight);
    if (!selected.has_value())
    {
        transientFailure = true;
        payload.error = "waiting for a full-frame D3D12 depth resource";
        return false;
    }
    inFlight.source.Attach(selected->resource);
    inFlight.queue.Attach(selected->queue);
    selected->resource = nullptr;
    selected->queue = nullptr;
    ComPtr<ID3D12Device> queueDevice;
    payload.resourceDeviceMatchesSwapchain = true;
    if (FAILED(inFlight.queue->GetDevice(IID_PPV_ARGS(&queueDevice))) ||
        queueDevice.Get() != device.Get())
    {
        transientFailure = true;
        payload.error = "waiting for a submitted D3D12 depth resource on the swapchain device";
        return false;
    }
    payload.queueDeviceMatchesSwapchain = true;
    const D3D12_RESOURCE_DESC sourceDescription = selected->description;

    D3D12_PLACED_SUBRESOURCE_FOOTPRINT footprint{};
    UINT rowCount = 0;
    UINT64 rowSize = 0;
    UINT64 totalSize = 0;
    device->GetCopyableFootprints(
        &sourceDescription,
        0,
        1,
        0,
        &footprint,
        &rowCount,
        &rowSize,
        &totalSize);
    if (totalSize == 0 || rowCount == 0 ||
        footprint.Footprint.RowPitch == 0)
    {
        payload.error = "D3D12 returned an invalid depth copy footprint";
        return false;
    }
    const std::string decodeFormat = decodeFormatName(
        sourceDescription.Format,
        footprint.Footprint.Format,
        rowSize,
        static_cast<std::uint32_t>(sourceDescription.Width));
    if (decodeFormat == "unsupported")
    {
        payload.error = "the selected D3D12 depth format is unsupported";
        return false;
    }

    if (FAILED(device->CreateCommandAllocator(
            D3D12_COMMAND_LIST_TYPE_DIRECT, IID_PPV_ARGS(&inFlight.allocator))) ||
        FAILED(device->CreateCommandList(
            0,
            D3D12_COMMAND_LIST_TYPE_DIRECT,
            inFlight.allocator.Get(),
            nullptr,
            IID_PPV_ARGS(&inFlight.commandList))))
    {
        payload.error = "failed to create the native depth copy command list";
        return false;
    }
    const D3D12_HEAP_PROPERTIES readbackHeap{
        D3D12_HEAP_TYPE_READBACK,
        D3D12_CPU_PAGE_PROPERTY_UNKNOWN,
        D3D12_MEMORY_POOL_UNKNOWN,
        1,
        1};
    const D3D12_RESOURCE_DESC bufferDescription =
        readbackBufferDescription(totalSize);
    if (FAILED(device->CreateCommittedResource(
            &readbackHeap,
            D3D12_HEAP_FLAG_NONE,
            &bufferDescription,
            D3D12_RESOURCE_STATE_COPY_DEST,
            nullptr,
            IID_PPV_ARGS(&inFlight.readback))))
    {
        payload.error = "failed to allocate the native depth readback buffer";
        return false;
    }

    const D3D12_RESOURCE_STATES sourceState = selected->state;
    if (sourceState != D3D12_RESOURCE_STATE_COPY_SOURCE)
    {
        const D3D12_RESOURCE_BARRIER barrier{
            D3D12_RESOURCE_BARRIER_TYPE_TRANSITION,
            D3D12_RESOURCE_BARRIER_FLAG_NONE,
            {inFlight.source.Get(),
             0,
             sourceState,
             D3D12_RESOURCE_STATE_COPY_SOURCE}};
        g_originalResourceBarrier(inFlight.commandList.Get(), 1, &barrier);
    }
    D3D12_TEXTURE_COPY_LOCATION destination{};
    destination.pResource = inFlight.readback.Get();
    destination.Type = D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT;
    destination.PlacedFootprint = footprint;
    D3D12_TEXTURE_COPY_LOCATION source{};
    source.pResource = inFlight.source.Get();
    source.Type = D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX;
    source.SubresourceIndex = 0;
    inFlight.commandList->CopyTextureRegion(
        &destination, 0, 0, 0, &source, nullptr);
    if (sourceState != D3D12_RESOURCE_STATE_COPY_SOURCE)
    {
        const D3D12_RESOURCE_BARRIER barrier{
            D3D12_RESOURCE_BARRIER_TYPE_TRANSITION,
            D3D12_RESOURCE_BARRIER_FLAG_NONE,
            {inFlight.source.Get(),
             0,
             D3D12_RESOURCE_STATE_COPY_SOURCE,
             sourceState}};
        g_originalResourceBarrier(inFlight.commandList.Get(), 1, &barrier);
    }
    if (FAILED(inFlight.commandList->Close()) ||
        FAILED(device->CreateFence(
            0, D3D12_FENCE_FLAG_NONE, IID_PPV_ARGS(&inFlight.fence))))
    {
        payload.error = "failed to finalize native depth GPU synchronization";
        return false;
    }
    const std::uint32_t height = std::min<std::uint32_t>(
        rowCount, sourceDescription.Height);
    const std::uint64_t byteCount =
        static_cast<std::uint64_t>(footprint.Footprint.RowPitch) * height;
    if (byteCount > totalSize)
    {
        payload.error = "native depth readback footprint exceeds its buffer";
        return false;
    }
    payload.width = static_cast<std::uint32_t>(sourceDescription.Width);
    payload.height = height;
    payload.rowPitch = footprint.Footprint.RowPitch;
    payload.slicePitch = byteCount;
    payload.capturedUnixNanoseconds = unixTimeNanoseconds();
    payload.resourceFormat = sourceDescription.Format;
    payload.copyFormat = footprint.Footprint.Format;
    payload.decodeFormat = decodeFormat;
    payload.sourceState = sourceState;
    payload.candidateAgeMilliseconds =
        GetTickCount64() - selected->lastSeenMilliseconds;
    payload.dsvBindingVerified =
        selected->bindingEvidence == DepthBindingEvidence::dsvDescriptor;
    payload.bindingEvidence = depthBindingEvidenceName(selected->bindingEvidence);
    inFlight.totalSize = totalSize;
    inFlight.fenceValue = 1;
    inFlight.submittedMilliseconds = GetTickCount64();
    ID3D12CommandList* commandLists[] = {inFlight.commandList.Get()};
    g_originalExecuteCommandLists(inFlight.queue.Get(), 1, commandLists);
    inFlight.fenceSignalSucceeded = SUCCEEDED(
        inFlight.queue->Signal(inFlight.fence.Get(), inFlight.fenceValue));
    if (!inFlight.fenceSignalSucceeded)
    {
        payload.error = "native depth copy was submitted but its fence signal failed";
    }
    return true;
}

void detachInFlightGpuObjects(InFlightCapture& capture)
{
    // If the queue never signaled completion, releasing these objects can race
    // the GPU. Leaking this one failed capture until process exit is safer than
    // risking a use-after-free inside the game process.
    capture.allocator.Detach();
    capture.commandList.Detach();
    capture.source.Detach();
    capture.readback.Detach();
    capture.fence.Detach();
    capture.queue.Detach();
}

std::optional<CapturePayload> pollInFlightCapture()
{
    InFlightCapture finished;
    {
        std::lock_guard<std::mutex> lock(g_requestMutex);
        if (!g_inFlightCapture.has_value())
        {
            return std::nullopt;
        }
        const UINT64 completedValue = g_inFlightCapture->fence->GetCompletedValue();
        bool deviceRemoved = completedValue == UINT64_MAX;
        if (!deviceRemoved && completedValue < g_inFlightCapture->fenceValue)
        {
            ComPtr<ID3D12Device> device;
            if (SUCCEEDED(g_inFlightCapture->source->GetDevice(
                    IID_PPV_ARGS(&device))) &&
                FAILED(device->GetDeviceRemovedReason()))
            {
                deviceRemoved = true;
            }
        }
        const bool timedOut =
            GetTickCount64() - g_inFlightCapture->submittedMilliseconds >
            kReadbackTimeoutMilliseconds;
        if (!deviceRemoved && !timedOut &&
            completedValue < g_inFlightCapture->fenceValue)
        {
            return std::nullopt;
        }
        finished = std::move(*g_inFlightCapture);
        g_inFlightCapture.reset();
        if (deviceRemoved)
        {
            finished.payload.error =
                "D3D12 device was removed before native depth readback completed";
            snapshotHookDiagnostics(finished.payload);
            return std::move(finished.payload);
        }
        if (timedOut && completedValue < finished.fenceValue)
        {
            finished.payload.error = finished.fenceSignalSucceeded
                ? "native D3D12 depth readback timed out without blocking Present"
                : "native depth copy was submitted but its fence signal failed";
            snapshotHookDiagnostics(finished.payload);
            detachInFlightGpuObjects(finished);
            return std::move(finished.payload);
        }
    }

    CapturePayload& payload = finished.payload;
    void* mapped = nullptr;
    const D3D12_RANGE readRange{
        0, static_cast<SIZE_T>(finished.totalSize)};
    if (FAILED(finished.readback->Map(0, &readRange, &mapped)) ||
        mapped == nullptr)
    {
        payload.error = "failed to map the completed native depth readback buffer";
        return std::move(payload);
    }
    try
    {
        payload.raw.resize(static_cast<std::size_t>(payload.slicePitch));
        std::memcpy(payload.raw.data(), mapped, payload.raw.size());
    }
    catch (const std::exception& error)
    {
        const D3D12_RANGE writtenRange{0, 0};
        finished.readback->Unmap(0, &writtenRange);
        payload.error = std::string("failed to allocate native depth payload: ") +
            error.what();
        snapshotHookDiagnostics(payload);
        return std::move(payload);
    }
    const D3D12_RANGE writtenRange{0, 0};
    finished.readback->Unmap(0, &writtenRange);
    payload.capturedUnixNanoseconds = unixTimeNanoseconds();
    payload.completed = true;
    snapshotHookDiagnostics(payload);
    return std::move(payload);
}

void publishCapture(CapturePayload payload)
{
    const fs::path responses = channelRoot() / L"responses";
    std::error_code error;
    fs::create_directories(responses, error);
    if (!payload.completed)
    {
        snapshotHookDiagnostics(payload);
        std::ostringstream json;
        json << "{\n"
             << "  \"protocol\": \"game-camera-depth-bridge/v2\",\n"
             << "  \"backend\": \"native_d3d12_runtime\",\n"
             << "  \"request_id\": \"" << jsonEscape(payload.request.id) << "\",\n"
             << "  \"status\": \"failed\",\n"
             << "  \"error\": \"" << jsonEscape(payload.error) << "\",\n"
             << "  \"hook_calls\": {\n"
             << "    \"present\": " << payload.presentHookCalls << ",\n"
             << "    \"present1\": " << payload.present1HookCalls << ",\n"
             << "    \"execute_command_lists\": " << payload.executeHookCalls << ",\n"
             << "    \"create_dsv\": " << payload.createDsvHookCalls << ",\n"
             << "    \"reset_command_list\": " << payload.resetHookCalls << ",\n"
             << "    \"resource_barrier\": " << payload.resourceBarrierHookCalls << ",\n"
             << "    \"transition_barriers\": " << payload.transitionBarrierCount << ",\n"
             << "    \"om_set_render_targets\": "
             << payload.omSetRenderTargetsHookCalls << ",\n"
             << "    \"exceptions\": " << payload.hookExceptionCount << "\n"
             << "  }\n"
             << "}\n";
        atomicTextWrite(responses / (payload.request.id + ".json"), json.str());
        fs::remove(payload.request.path, error);
        writeRuntimeStatus("error", payload.error);
        return;
    }

    const fs::path rawTarget = responses / (payload.request.id + ".raw");
    const fs::path rawTemporary = rawTarget.wstring() + L".tmp";
    {
        std::ofstream stream(rawTemporary, std::ios::binary | std::ios::trunc);
        stream.write(
            reinterpret_cast<const char*>(payload.raw.data()),
            static_cast<std::streamsize>(payload.raw.size()));
        if (!stream)
        {
            throw std::runtime_error("failed to write native depth payload");
        }
    }
    fs::remove(rawTarget, error);
    error.clear();
    fs::rename(rawTemporary, rawTarget, error);
    if (error)
    {
        throw std::runtime_error("failed to publish native depth payload");
    }

    std::ostringstream json;
    json << "{\n"
         << "  \"protocol\": \"game-camera-depth-bridge/v2\",\n"
         << "  \"backend\": \"native_d3d12_runtime\",\n"
         << "  \"request_id\": \"" << jsonEscape(payload.request.id) << "\",\n"
         << "  \"status\": \"completed\",\n"
         << "  \"captured_unix_ns\": " << payload.capturedUnixNanoseconds << ",\n"
         << "  \"api\": \"d3d12\",\n"
         << "  \"width\": " << payload.width << ",\n"
         << "  \"height\": " << payload.height << ",\n"
         << "  \"row_pitch\": " << payload.rowPitch << ",\n"
         << "  \"slice_pitch\": " << payload.slicePitch << ",\n"
         << "  \"resource_format\": \""
         << resourceFormatName(payload.resourceFormat) << "\",\n"
         << "  \"shader_view_format\": \"not_applicable\",\n"
         << "  \"copy_format\": \""
         << resourceFormatName(payload.copyFormat) << "\",\n"
         << "  \"format\": \"" << jsonEscape(payload.decodeFormat) << "\",\n"
         << "  \"format_value\": "
         << static_cast<std::uint32_t>(payload.copyFormat) << ",\n"
         << "  \"source_state\": "
         << static_cast<std::uint32_t>(payload.sourceState) << ",\n"
         << "  \"candidate_age_ms\": " << payload.candidateAgeMilliseconds << ",\n"
         << "  \"dsv_binding_verified\": "
         << (payload.dsvBindingVerified ? "true" : "false") << ",\n"
         << "  \"binding_evidence\": \""
         << jsonEscape(payload.bindingEvidence) << "\",\n"
         << "  \"swapchain_d3d12_verified\": "
         << (payload.swapchainD3d12Verified ? "true" : "false") << ",\n"
         << "  \"resource_device_matches_swapchain\": "
         << (payload.resourceDeviceMatchesSwapchain ? "true" : "false") << ",\n"
         << "  \"queue_device_matches_swapchain\": "
         << (payload.queueDeviceMatchesSwapchain ? "true" : "false") << ",\n"
         << "  \"hook_calls\": {\n"
         << "    \"present\": " << payload.presentHookCalls << ",\n"
         << "    \"present1\": " << payload.present1HookCalls << ",\n"
         << "    \"execute_command_lists\": " << payload.executeHookCalls << ",\n"
         << "    \"create_dsv\": " << payload.createDsvHookCalls << ",\n"
         << "    \"reset_command_list\": " << payload.resetHookCalls << ",\n"
         << "    \"resource_barrier\": " << payload.resourceBarrierHookCalls << ",\n"
         << "    \"transition_barriers\": " << payload.transitionBarrierCount << ",\n"
         << "    \"om_set_render_targets\": "
         << payload.omSetRenderTargetsHookCalls << ",\n"
         << "    \"exceptions\": " << payload.hookExceptionCount << "\n"
         << "  },\n"
         << "  \"reversed_z\": null,\n"
         << "  \"reversed_z_source\": \"unknown_inferred_by_client\",\n"
         << "  \"metric_depth\": false,\n"
         << "  \"raw_path\": \"" << payload.request.id << ".raw\"\n"
         << "}\n";
    atomicTextWrite(responses / (payload.request.id + ".json"), json.str());
    fs::remove(payload.request.path, error);
    writeRuntimeStatus("ready", "last native D3D12 depth request completed");
}

void trySubmitDepthFromPresent(IDXGISwapChain* swapchain)
{
    if (InterlockedCompareExchange(&g_captureBusy, 1, 0) != 0)
    {
        return;
    }
    CaptureBusyReset busyReset;
    try
    {
        std::optional<PendingRequest> pending;
        {
            std::lock_guard<std::mutex> lock(g_requestMutex);
            if (!g_inFlightCapture.has_value())
            {
                pending = g_pendingRequest;
            }
        }
        if (pending.has_value())
        {
            InFlightCapture capture;
            bool transientFailure = false;
            const bool submitted = submitDepthReadback(
                swapchain, *pending, capture, transientFailure);
            if (submitted)
            {
                std::lock_guard<std::mutex> lock(g_requestMutex);
                if (!g_inFlightCapture.has_value())
                {
                    if (g_pendingRequest.has_value() &&
                        g_pendingRequest->id == pending->id)
                    {
                        g_pendingRequest.reset();
                    }
                    g_inFlightCapture.emplace(std::move(capture));
                }
                else
                {
                    detachInFlightGpuObjects(capture);
                }
            }
            else if (!transientFailure)
            {
                snapshotHookDiagnostics(capture.payload);
                std::lock_guard<std::mutex> lock(g_requestMutex);
                if (g_pendingRequest.has_value() &&
                    g_pendingRequest->id == pending->id)
                {
                    g_pendingRequest.reset();
                    g_finishedCapture = std::move(capture.payload);
                }
            }
        }
    }
    catch (const std::exception& error)
    {
        g_hookExceptionCount.fetch_add(1, std::memory_order_relaxed);
        try
        {
            std::lock_guard<std::mutex> lock(g_requestMutex);
            if (g_pendingRequest.has_value())
            {
                CapturePayload failure;
                failure.request = *g_pendingRequest;
                failure.error = std::string("native depth submission failed: ") +
                    error.what();
                snapshotHookDiagnostics(failure);
                g_pendingRequest.reset();
                g_finishedCapture = std::move(failure);
            }
        }
        catch (...)
        {
        }
    }
    catch (...)
    {
        g_hookExceptionCount.fetch_add(1, std::memory_order_relaxed);
    }
}

HRESULT STDMETHODCALLTYPE depthPresentHook(
    IDXGISwapChain* swapchain, const UINT syncInterval, const UINT flags)
{
    HookActivity activity;
    g_presentHookCalls.fetch_add(1, std::memory_order_relaxed);
    trySubmitDepthFromPresent(swapchain);
    return g_originalPresent != nullptr
        ? g_originalPresent(swapchain, syncInterval, flags)
        : E_FAIL;
}

HRESULT STDMETHODCALLTYPE depthPresent1Hook(
    IDXGISwapChain1* swapchain,
    const UINT syncInterval,
    const UINT flags,
    const DXGI_PRESENT_PARAMETERS* parameters)
{
    HookActivity activity;
    g_present1HookCalls.fetch_add(1, std::memory_order_relaxed);
    trySubmitDepthFromPresent(static_cast<IDXGISwapChain*>(swapchain));
    return g_originalPresent1 != nullptr
        ? g_originalPresent1(swapchain, syncInterval, flags, parameters)
        : E_FAIL;
}

void STDMETHODCALLTYPE depthExecuteCommandListsHook(
    ID3D12CommandQueue* queue,
    const UINT count,
    ID3D12CommandList* const* commandLists)
{
    HookActivity activity;
    g_executeHookCalls.fetch_add(1, std::memory_order_relaxed);
    if (g_originalExecuteCommandLists != nullptr)
    {
        g_originalExecuteCommandLists(queue, count, commandLists);
    }
    if (queue == nullptr || commandLists == nullptr)
    {
        return;
    }
    try
    {
        for (UINT index = 0; index < count; ++index)
        {
            if (commandLists[index] == nullptr ||
                commandLists[index]->GetType() != D3D12_COMMAND_LIST_TYPE_DIRECT)
            {
                continue;
            }
            ComPtr<ID3D12GraphicsCommandList> graphicsList;
            if (SUCCEEDED(commandLists[index]->QueryInterface(
                    IID_PPV_ARGS(&graphicsList))))
            {
                applyExecutedRecord(queue, graphicsList.Get());
            }
        }
    }
    catch (...)
    {
        g_hookExceptionCount.fetch_add(1, std::memory_order_relaxed);
    }
}

void STDMETHODCALLTYPE depthCreateDepthStencilViewHook(
    ID3D12Device* device,
    ID3D12Resource* resource,
    const D3D12_DEPTH_STENCIL_VIEW_DESC* description,
    const D3D12_CPU_DESCRIPTOR_HANDLE destination)
{
    HookActivity activity;
    g_createDsvHookCalls.fetch_add(1, std::memory_order_relaxed);
    if (g_originalCreateDepthStencilView != nullptr)
    {
        g_originalCreateDepthStencilView(device, resource, description, destination);
    }
    try
    {
        rememberDepthStencilView(resource, destination);
    }
    catch (...)
    {
        g_hookExceptionCount.fetch_add(1, std::memory_order_relaxed);
    }
}

HRESULT STDMETHODCALLTYPE depthResetCommandListHook(
    ID3D12GraphicsCommandList* list,
    ID3D12CommandAllocator* allocator,
    ID3D12PipelineState* initialState)
{
    HookActivity activity;
    g_resetHookCalls.fetch_add(1, std::memory_order_relaxed);
    const HRESULT result = g_originalResetCommandList != nullptr
        ? g_originalResetCommandList(list, allocator, initialState)
        : E_FAIL;
    if (SUCCEEDED(result))
    {
        try
        {
            clearCommandListRecord(list);
        }
        catch (...)
        {
            g_hookExceptionCount.fetch_add(1, std::memory_order_relaxed);
        }
    }
    return result;
}

void STDMETHODCALLTYPE depthResourceBarrierHook(
    ID3D12GraphicsCommandList* list,
    const UINT count,
    const D3D12_RESOURCE_BARRIER* barriers)
{
    HookActivity activity;
    g_resourceBarrierHookCalls.fetch_add(1, std::memory_order_relaxed);
    if (g_originalResourceBarrier != nullptr)
    {
        g_originalResourceBarrier(list, count, barriers);
    }
    if (barriers == nullptr)
    {
        return;
    }
    try
    {
        for (UINT index = 0; index < count; ++index)
        {
            const D3D12_RESOURCE_BARRIER& barrier = barriers[index];
            if (barrier.Type != D3D12_RESOURCE_BARRIER_TYPE_TRANSITION ||
                barrier.Flags == D3D12_RESOURCE_BARRIER_FLAG_BEGIN_ONLY ||
                (barrier.Transition.Subresource !=
                     D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES &&
                 barrier.Transition.Subresource != 0))
            {
                continue;
            }
            if (containsDepthState(barrier.Transition.StateBefore) ||
                containsDepthState(barrier.Transition.StateAfter))
            {
                g_transitionBarrierCount.fetch_add(1, std::memory_order_relaxed);
            }
            recordTransition(list, barrier.Transition);
        }
    }
    catch (...)
    {
        g_hookExceptionCount.fetch_add(1, std::memory_order_relaxed);
    }
}

void STDMETHODCALLTYPE depthOmSetRenderTargetsHook(
    ID3D12GraphicsCommandList* list,
    const UINT renderTargetCount,
    const D3D12_CPU_DESCRIPTOR_HANDLE* renderTargets,
    const BOOL singleHandleRange,
    const D3D12_CPU_DESCRIPTOR_HANDLE* depthStencil)
{
    HookActivity activity;
    g_omSetRenderTargetsHookCalls.fetch_add(1, std::memory_order_relaxed);
    if (g_originalOmSetRenderTargets != nullptr)
    {
        g_originalOmSetRenderTargets(
            list,
            renderTargetCount,
            renderTargets,
            singleHandleRange,
            depthStencil);
    }
    try
    {
        recordDepthBinding(list, depthStencil);
    }
    catch (...)
    {
        g_hookExceptionCount.fetch_add(1, std::memory_order_relaxed);
    }
}

bool waitForHookQuiescence()
{
    const ULONGLONG started = GetTickCount64();
    ULONGLONG zeroSince = 0;
    while (GetTickCount64() - started <= 1000)
    {
        if (g_activeHookCalls.load(std::memory_order_acquire) == 0)
        {
            if (zeroSince == 0)
            {
                zeroSince = GetTickCount64();
            }
            else if (GetTickCount64() - zeroSince >= 10)
            {
                return true;
            }
        }
        else
        {
            zeroSince = 0;
        }
        Sleep(1);
    }
    return false;
}

void releaseGpuObjects()
{
    if (!waitForHookQuiescence())
    {
        // The vtables are already restored, but a thread may still be inside a
        // previously loaded hook pointer. Keep its raw COM references alive
        // until process exit rather than racing that thread during teardown.
        std::lock_guard<std::mutex> requestLock(g_requestMutex);
        if (g_inFlightCapture.has_value())
        {
            detachInFlightGpuObjects(*g_inFlightCapture);
            g_inFlightCapture.reset();
        }
        g_pendingRequest.reset();
        g_finishedCapture.reset();
        return;
    }
    {
        std::lock_guard<std::mutex> lock(g_requestMutex);
        if (g_inFlightCapture.has_value())
        {
            const bool completed = g_inFlightCapture->fence != nullptr &&
                g_inFlightCapture->fence->GetCompletedValue() >=
                    g_inFlightCapture->fenceValue;
            if (!completed)
            {
                detachInFlightGpuObjects(*g_inFlightCapture);
            }
            g_inFlightCapture.reset();
        }
        g_pendingRequest.reset();
        g_finishedCapture.reset();
    }
    std::lock_guard<std::mutex> lock(g_gpuMutex);
    for (DepthCandidate& candidate : g_candidates)
    {
        releaseCandidate(candidate);
    }
    g_candidates.clear();
    for (auto& entry : g_commandListRecords)
    {
        clearCommandListRecordLocked(entry.second);
    }
    g_commandListRecords.clear();
    g_dsvResources.clear();
}

DWORD WINAPI nativeDepthWorker(LPVOID)
{
    writeRuntimeStatus("idle", "native D3D12 hooks are lazy until depth is requested");
    while (!stopRequested())
    {
        std::optional<CapturePayload> finished = pollInFlightCapture();
        if (!finished.has_value())
        {
            std::lock_guard<std::mutex> lock(g_requestMutex);
            if (g_finishedCapture.has_value())
            {
                finished = std::move(g_finishedCapture);
                g_finishedCapture.reset();
            }
        }
        if (finished.has_value())
        {
            try
            {
                publishCapture(std::move(*finished));
            }
            catch (const std::exception& error)
            {
                writeRuntimeStatus("error", error.what());
            }
        }

        bool hasPending = false;
        bool hasInFlight = false;
        bool hasFinishedQueued = false;
        {
            std::lock_guard<std::mutex> lock(g_requestMutex);
            hasPending = g_pendingRequest.has_value();
            hasInFlight = g_inFlightCapture.has_value();
            if (hasPending &&
                InterlockedCompareExchange(&g_captureBusy, 0, 0) == 0 &&
                GetTickCount64() - g_pendingRequest->startedMilliseconds >
                    kRequestTimeoutMilliseconds)
            {
                CapturePayload timeout;
                timeout.request = *g_pendingRequest;
                timeout.error = depthSelectionTimeoutDetail();
                snapshotHookDiagnostics(timeout);
                g_pendingRequest.reset();
                g_finishedCapture = std::move(timeout);
                hasPending = false;
                hasFinishedQueued = true;
            }
            else
            {
                hasFinishedQueued = g_finishedCapture.has_value();
            }
        }
        if (!hasPending && !hasInFlight && !hasFinishedQueued)
        {
            const std::optional<PendingRequest> request = nextRequest();
            if (request.has_value())
            {
                if (!installHooks())
                {
                    CapturePayload failure;
                    failure.request = *request;
                    failure.error =
                        "failed to install repository-owned D3D12 depth hooks";
                    try
                    {
                        publishCapture(std::move(failure));
                    }
                    catch (...)
                    {
                    }
                }
                else
                {
                    std::lock_guard<std::mutex> lock(g_requestMutex);
                    if (!g_pendingRequest.has_value())
                    {
                        g_pendingRequest = request;
                        writeRuntimeStatus(
                            "capturing", "native D3D12 depth hooks installed");
                    }
                }
            }
        }
        Sleep(20);
    }
    restoreHooks();
    releaseGpuObjects();
    writeRuntimeStatus("stopped", "camera runtime is shutting down");
    return 0;
}
} // namespace

HANDLE startNativeDepthCapture(volatile LONG* stopRequestedPointer)
{
    g_externalStopRequested = stopRequestedPointer;
    return CreateThread(nullptr, 0, nativeDepthWorker, nullptr, 0, nullptr);
}

void stopNativeDepthCapture(HANDLE thread)
{
    if (thread != nullptr)
    {
        WaitForSingleObject(thread, 3000);
        CloseHandle(thread);
    }
    restoreHooks();
    releaseGpuObjects();
    g_externalStopRequested = nullptr;
}
} // namespace bmw_camera
