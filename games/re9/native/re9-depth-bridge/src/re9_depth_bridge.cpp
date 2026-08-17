#include <windows.h>

#include <d3d12.h>
#include <dxgi1_6.h>
#include <wrl/client.h>

#include <reframework/API.hpp>

#include <nlohmann/json.hpp>

#include <algorithm>
#include <array>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <optional>
#include <string>
#include <string_view>
#include <unordered_set>
#include <utility>
#include <vector>

using Microsoft::WRL::ComPtr;
using json = nlohmann::json;
using reframework::API;

namespace {
constexpr std::string_view kPluginVersion{"0.1.0"};
constexpr int kSchemaVersion = 1;
constexpr std::wstring_view kRequestFilename{L"re9_depth_request.json"};
constexpr std::wstring_view kStatusFilename{L"re9_depth_status.json"};
constexpr std::wstring_view kHeartbeatFilename{L"re9_depth_heartbeat.json"};
constexpr size_t kRe9RenderResourceSize = 0x20;

const REFrameworkPluginInitializeParam* g_param{};
std::filesystem::path g_data_directory{};
std::string g_last_handled_capture_id{};
std::optional<size_t> g_texture_resource_offset{};
bool g_depth_source_discovered{false};

std::chrono::steady_clock::time_point g_last_request_poll{};
std::chrono::steady_clock::time_point g_last_heartbeat{};

struct Matrix4x4 {
    std::array<float, 16> values{};
};

struct SceneInfoLayout {
    Matrix4x4 view_projection_matrix{};
    Matrix4x4 view_matrix{};
    Matrix4x4 inverse_view_matrix{};
    Matrix4x4 projection_matrix{};
    Matrix4x4 inverse_projection_matrix{};
    Matrix4x4 inverse_view_projection_matrix{};
    Matrix4x4 old_view_projection_matrix{};
};

struct NativeArrayView {
    void** elements{};
    uint32_t count{};
    uint32_t capacity{};
};

struct CaptureRequest {
    std::string capture_id{};
    std::filesystem::path raw_output_path{};
    uint32_t expected_width{};
    uint32_t expected_height{};
    double requested_at_unix{};
};

struct CaptureMetadata {
    uint32_t width{};
    uint32_t height{};
    uint32_t row_pitch{};
    uint32_t pixel_stride_bytes{};
    std::string depth_encoding{};
    std::string dxgi_format_name{};
    uint32_t dxgi_format{};
    float near_clip{};
    float far_clip{};
    float fov{};
    Matrix4x4 projection_matrix{};
    Matrix4x4 inverse_projection_matrix{};
    uint32_t render_frame_id{};
    uint32_t scene_view_id{};
};

struct PendingCapture {
    CaptureRequest request{};
    CaptureMetadata metadata{};
    ComPtr<ID3D12Resource> source{};
    ComPtr<ID3D12Resource> readback{};
    D3D12_PLACED_SUBRESOURCE_FOOTPRINT footprint{};
    uint32_t row_count{};
    uint64_t total_bytes{};
    uint64_t fence_value{};
};

struct GpuState {
    ComPtr<ID3D12CommandAllocator> allocator{};
    ComPtr<ID3D12GraphicsCommandList> command_list{};
    ComPtr<ID3D12Fence> fence{};
    uint64_t next_fence_value{};

    void reset() {
        allocator.Reset();
        command_list.Reset();
        fence.Reset();
        next_fence_value = 0;
    }

    bool ensure(ID3D12Device* device, std::string& error) {
        if (allocator && command_list && fence) {
            return true;
        }
        reset();
        if (device == nullptr) {
            error = "D3D12 device is unavailable";
            return false;
        }
        HRESULT result = device->CreateCommandAllocator(
            D3D12_COMMAND_LIST_TYPE_DIRECT,
            IID_PPV_ARGS(allocator.ReleaseAndGetAddressOf()));
        if (FAILED(result)) {
            error = "CreateCommandAllocator failed";
            return false;
        }
        result = device->CreateCommandList(
            0,
            D3D12_COMMAND_LIST_TYPE_DIRECT,
            allocator.Get(),
            nullptr,
            IID_PPV_ARGS(command_list.ReleaseAndGetAddressOf()));
        if (FAILED(result)) {
            error = "CreateCommandList failed";
            reset();
            return false;
        }
        if (FAILED(command_list->Close())) {
            error = "Closing the initial command list failed";
            reset();
            return false;
        }
        result = device->CreateFence(0, D3D12_FENCE_FLAG_NONE, IID_PPV_ARGS(fence.ReleaseAndGetAddressOf()));
        if (FAILED(result)) {
            error = "CreateFence failed";
            reset();
            return false;
        }
        return true;
    }
};

GpuState g_gpu{};
std::optional<PendingCapture> g_pending{};

double unix_time_seconds() {
    const auto now = std::chrono::system_clock::now().time_since_epoch();
    return std::chrono::duration<double>(now).count();
}

void log_info(const std::string& message) {
    if (g_param != nullptr && g_param->functions != nullptr) {
        g_param->functions->log_info("[RE9DepthBridge] %s", message.c_str());
    }
}

void log_error(const std::string& message) {
    if (g_param != nullptr && g_param->functions != nullptr) {
        g_param->functions->log_error("[RE9DepthBridge] %s", message.c_str());
    }
}

std::string path_to_utf8(const std::filesystem::path& path) {
    const auto value = path.u8string();
    return std::string(reinterpret_cast<const char*>(value.data()), value.size());
}

std::filesystem::path path_from_utf8(const std::string& value) {
    return std::filesystem::path(std::u8string(
        reinterpret_cast<const char8_t*>(value.data()),
        reinterpret_cast<const char8_t*>(value.data() + value.size())));
}

std::filesystem::path data_directory_from_module(HMODULE module) {
    std::array<wchar_t, 32768> buffer{};
    const DWORD length = GetModuleFileNameW(module, buffer.data(), static_cast<DWORD>(buffer.size()));
    if (length == 0 || length >= buffer.size()) {
        return std::filesystem::current_path() / L"reframework" / L"data";
    }
    return std::filesystem::path(buffer.data(), buffer.data() + length).parent_path() / L"reframework" / L"data";
}

bool write_json_atomic(const std::filesystem::path& path, const json& payload, std::string& error) {
    std::error_code filesystem_error{};
    std::filesystem::create_directories(path.parent_path(), filesystem_error);
    if (filesystem_error) {
        error = "Cannot create JSON output directory: " + filesystem_error.message();
        return false;
    }

    const auto temporary = path.parent_path() /
        (L"." + path.filename().wstring() + L"." + std::to_wstring(GetCurrentProcessId()) + L"." +
         std::to_wstring(GetTickCount64()) + L".tmp");
    {
        std::ofstream stream(temporary, std::ios::binary | std::ios::trunc);
        if (!stream) {
            error = "Cannot open temporary JSON file";
            return false;
        }
        const std::string text = payload.dump(2);
        stream.write(text.data(), static_cast<std::streamsize>(text.size()));
        stream.flush();
        if (!stream) {
            error = "Cannot finish writing temporary JSON file";
            stream.close();
            std::filesystem::remove(temporary, filesystem_error);
            return false;
        }
    }

    if (!MoveFileExW(
            temporary.c_str(),
            path.c_str(),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) {
        error = "MoveFileExW failed while publishing JSON (" + std::to_string(GetLastError()) + ")";
        std::filesystem::remove(temporary, filesystem_error);
        return false;
    }
    return true;
}

std::optional<json> read_json(const std::filesystem::path& path) {
    std::ifstream stream(path, std::ios::binary);
    if (!stream) {
        return std::nullopt;
    }
    try {
        json payload{};
        stream >> payload;
        if (!payload.is_object()) {
            return std::nullopt;
        }
        return payload;
    } catch (...) {
        return std::nullopt;
    }
}

json matrix_json(const Matrix4x4& matrix) {
    json values = json::array();
    for (const float value : matrix.values) {
        values.push_back(value);
    }
    return values;
}

void write_capture_error(const std::string& capture_id, const std::string& error_message) {
    json payload{
        {"schema_version", kSchemaVersion},
        {"plugin_version", kPluginVersion},
        {"capture_id", capture_id},
        {"status", "error"},
        {"error", error_message},
        {"updated_at_unix", unix_time_seconds()},
    };
    std::string write_error{};
    if (!write_json_atomic(g_data_directory / kStatusFilename, payload, write_error)) {
        log_error(error_message + "; status write also failed: " + write_error);
    }
}

void write_processing_status(const CaptureRequest& request) {
    json payload{
        {"schema_version", kSchemaVersion},
        {"plugin_version", kPluginVersion},
        {"capture_id", request.capture_id},
        {"status", "processing"},
        {"requested_at_unix", request.requested_at_unix},
        {"updated_at_unix", unix_time_seconds()},
    };
    std::string ignored{};
    write_json_atomic(g_data_directory / kStatusFilename, payload, ignored);
}

void write_heartbeat() {
    const auto now = std::chrono::steady_clock::now();
    if (now - g_last_heartbeat < std::chrono::seconds(1)) {
        return;
    }
    g_last_heartbeat = now;
    const bool d3d12 = g_param != nullptr && g_param->renderer_data != nullptr &&
        g_param->renderer_data->renderer_type == REFRAMEWORK_RENDERER_D3D12 &&
        g_param->renderer_data->device != nullptr && g_param->renderer_data->command_queue != nullptr;
    json payload{
        {"schema_version", kSchemaVersion},
        {"plugin_version", kPluginVersion},
        {"status", d3d12 ? "ready" : "error"},
        {"renderer", d3d12 ? "D3D12" : "unsupported"},
        {"depth_source_discovered", g_depth_source_discovered},
        {"updated_at_unix", unix_time_seconds()},
    };
    std::string ignored{};
    write_json_atomic(g_data_directory / kHeartbeatFilename, payload, ignored);
}

std::optional<CaptureRequest> read_new_request() {
    const auto now = std::chrono::steady_clock::now();
    if (now - g_last_request_poll < std::chrono::milliseconds(50)) {
        return std::nullopt;
    }
    g_last_request_poll = now;
    const auto payload = read_json(g_data_directory / kRequestFilename);
    if (!payload) {
        return std::nullopt;
    }
    try {
        if (payload->value("schema_version", 0) != kSchemaVersion) {
            return std::nullopt;
        }
        CaptureRequest request{};
        request.capture_id = payload->value("capture_id", "");
        if (request.capture_id.empty() || request.capture_id == g_last_handled_capture_id) {
            return std::nullopt;
        }
        request.raw_output_path = path_from_utf8(payload->value("raw_output_path", ""));
        request.expected_width = payload->value("expected_width", 0U);
        request.expected_height = payload->value("expected_height", 0U);
        request.requested_at_unix = payload->value("requested_at_unix", 0.0);
        if (request.raw_output_path.empty() || !request.raw_output_path.is_absolute()) {
            write_capture_error(request.capture_id, "raw_output_path must be an absolute path");
            g_last_handled_capture_id = request.capture_id;
            return std::nullopt;
        }
        return request;
    } catch (const std::exception& error) {
        log_error(std::string{"Invalid depth request: "} + error.what());
        return std::nullopt;
    }
}

bool memory_has_protection(DWORD protection, DWORD forbidden) {
    return (protection & forbidden) != 0;
}

bool is_readable_memory(const void* pointer, size_t size) {
    if (pointer == nullptr || size == 0) {
        return false;
    }
    MEMORY_BASIC_INFORMATION info{};
    if (VirtualQuery(pointer, &info, sizeof(info)) == 0 || info.State != MEM_COMMIT) {
        return false;
    }
    if (memory_has_protection(info.Protect, PAGE_NOACCESS | PAGE_GUARD)) {
        return false;
    }
    const auto start = reinterpret_cast<uintptr_t>(pointer);
    const auto end = start + size;
    const auto region_end = reinterpret_cast<uintptr_t>(info.BaseAddress) + info.RegionSize;
    return end >= start && end <= region_end;
}

bool is_executable_memory(const void* pointer) {
    if (pointer == nullptr) {
        return false;
    }
    MEMORY_BASIC_INFORMATION info{};
    if (VirtualQuery(pointer, &info, sizeof(info)) == 0 || info.State != MEM_COMMIT) {
        return false;
    }
    const DWORD executable = PAGE_EXECUTE | PAGE_EXECUTE_READ | PAGE_EXECUTE_READWRITE | PAGE_EXECUTE_WRITECOPY;
    return memory_has_protection(info.Protect, executable) && !memory_has_protection(info.Protect, PAGE_GUARD);
}

bool safe_resource_desc(ID3D12Resource* resource, D3D12_RESOURCE_DESC* output) {
    __try {
        if (resource == nullptr || output == nullptr) {
            return false;
        }
        *output = resource->GetDesc();
        return true;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return false;
    }
}

REFrameworkTypeInfoHandle safe_type_info(void* function) {
    __try {
        using TypeInfoFunction = REFrameworkTypeInfoHandle (*)();
        return reinterpret_cast<TypeInfoFunction>(function)();
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return nullptr;
    }
}

std::string managed_type_name(void* object) {
    if (object == nullptr || g_param == nullptr || g_param->sdk == nullptr) {
        return {};
    }
    auto* managed = reinterpret_cast<API::ManagedObject*>(object);
    auto* type = managed->get_type_definition();
    return type != nullptr ? type->get_full_name() : std::string{};
}

reframework::InvokeRet invoke_no_args(void* object, std::string_view method_name) {
    if (object == nullptr) {
        return {};
    }
    auto* managed = reinterpret_cast<API::ManagedObject*>(object);
    auto* type = managed->get_type_definition();
    if (type == nullptr) {
        return {};
    }
    auto* method = type->find_method(method_name);
    if (method == nullptr) {
        return {};
    }
    const std::vector<void*> arguments{};
    return method->invoke(managed, arguments);
}

bool reflection_get_pointer(void* object, std::string_view property_name, void*& output) {
    output = nullptr;
    if (object == nullptr) {
        return false;
    }
    auto* managed = reinterpret_cast<API::ManagedObject*>(object);
    auto* property = managed->get_reflection_property_descriptor(property_name);
    if (property == nullptr) {
        return false;
    }
    const auto getter = property->get_getter();
    if (getter == nullptr) {
        return false;
    }
    getter(*property, *managed, &output);
    return output != nullptr;
}

bool reflection_get_native_array(void* object, std::string_view property_name, NativeArrayView& output) {
    output = {};
    if (object == nullptr) {
        return false;
    }
    auto* managed = reinterpret_cast<API::ManagedObject*>(object);
    auto* property = managed->get_reflection_property_descriptor(property_name);
    if (property == nullptr) {
        return false;
    }
    const auto getter = property->get_getter();
    if (getter == nullptr) {
        return false;
    }
    getter(*property, *managed, &output);
    return output.count == 0 ||
        (output.elements != nullptr && output.count <= output.capacity && output.count <= 4096 &&
         is_readable_memory(output.elements, sizeof(void*) * output.count));
}

void collect_scene_layers(
    void* layer,
    std::vector<void*>& scenes,
    std::unordered_set<void*>& visited,
    uint32_t depth = 0) {
    if (layer == nullptr || depth > 32 || visited.contains(layer)) {
        return;
    }
    visited.insert(layer);
    const std::string name = managed_type_name(layer);
    if (name == "via.render.layer.Scene" || name.starts_with("via.render.layer.Scene.")) {
        scenes.push_back(layer);
    }

    NativeArrayView children{};
    if (!reflection_get_native_array(layer, "Layers", children)) {
        return;
    }
    for (uint32_t index = 0; index < children.count; ++index) {
        collect_scene_layers(children.elements[index], scenes, visited, depth + 1);
    }
}

void* find_root_render_layer() {
    auto* renderer = API::get()->get_native_singleton("via.render.Renderer");
    if (renderer == nullptr) {
        return nullptr;
    }
    const auto result = invoke_no_args(renderer, "getOutputLayer");
    return result.exception_thrown ? nullptr : result.ptr;
}

ID3D12Resource* find_native_depth_resource(void* texture) {
    if (texture == nullptr || g_param == nullptr || g_param->sdk == nullptr) {
        return nullptr;
    }

    auto resource_at_offset = [texture](size_t offset) -> ID3D12Resource* {
        if (!is_readable_memory(reinterpret_cast<uint8_t*>(texture) + offset, sizeof(void*))) {
            return nullptr;
        }
        void* container = *reinterpret_cast<void**>(reinterpret_cast<uint8_t*>(texture) + offset);
        if (!is_readable_memory(container, kRe9RenderResourceSize + sizeof(void*))) {
            return nullptr;
        }
        auto* resource = *reinterpret_cast<ID3D12Resource**>(
            reinterpret_cast<uint8_t*>(container) + kRe9RenderResourceSize);
        D3D12_RESOURCE_DESC description{};
        return safe_resource_desc(resource, &description) ? resource : nullptr;
    };

    if (g_texture_resource_offset) {
        if (auto* resource = resource_at_offset(*g_texture_resource_offset); resource != nullptr) {
            return resource;
        }
        g_texture_resource_offset.reset();
    }

    for (size_t offset = 0x98; offset < 0x200; offset += sizeof(void*)) {
        if (!is_readable_memory(reinterpret_cast<uint8_t*>(texture) + offset, sizeof(void*))) {
            continue;
        }
        void* candidate = *reinterpret_cast<void**>(reinterpret_cast<uint8_t*>(texture) + offset);
        if (!is_readable_memory(candidate, sizeof(void*))) {
            continue;
        }
        auto** vtable = *reinterpret_cast<void***>(candidate);
        if (!is_readable_memory(vtable, sizeof(void*) * 4) || !is_executable_memory(vtable[3])) {
            continue;
        }
        const auto type_info = safe_type_info(vtable[3]);
        if (type_info == nullptr) {
            continue;
        }
        const char* type_name = g_param->sdk->type_info->get_name(type_info);
        if (type_name == nullptr || std::string_view{type_name} != "via.render.RenderResource") {
            continue;
        }
        g_texture_resource_offset = offset;
        return resource_at_offset(offset);
    }
    return nullptr;
}

bool camera_metadata(void* scene, CaptureMetadata& metadata) {
    const auto camera_result = invoke_no_args(scene, "get_Camera");
    if (camera_result.exception_thrown || camera_result.ptr == nullptr) {
        return false;
    }
    void* camera = camera_result.ptr;
    const auto near_result = invoke_no_args(camera, "get_NearClipPlane");
    const auto far_result = invoke_no_args(camera, "get_FarClipPlane");
    const auto fov_result = invoke_no_args(camera, "get_FOV");
    if (near_result.exception_thrown || far_result.exception_thrown) {
        return false;
    }
    metadata.near_clip = near_result.f;
    metadata.far_clip = far_result.f;
    metadata.fov = fov_result.exception_thrown ? 0.0F : fov_result.f;

    void* scene_info_pointer{};
    if (!reflection_get_pointer(scene, "SceneInfo", scene_info_pointer) ||
        !is_readable_memory(scene_info_pointer, sizeof(SceneInfoLayout))) {
        return false;
    }
    const auto* scene_info = reinterpret_cast<const SceneInfoLayout*>(scene_info_pointer);
    metadata.projection_matrix = scene_info->projection_matrix;
    metadata.inverse_projection_matrix = scene_info->inverse_projection_matrix;
    const auto view_result = invoke_no_args(scene, "get_ViewID");
    metadata.scene_view_id = view_result.exception_thrown ? 0U : view_result.dword;

    auto* renderer = API::get()->get_native_singleton("via.render.Renderer");
    const auto frame_result = invoke_no_args(renderer, "get_RenderFrame");
    metadata.render_frame_id = frame_result.exception_thrown ? 0U : frame_result.dword;
    return metadata.near_clip > 0.0F && metadata.far_clip > metadata.near_clip;
}

struct DepthSource {
    ComPtr<ID3D12Resource> resource{};
    void* scene{};
    CaptureMetadata metadata{};
};

std::optional<DepthSource> find_depth_source(std::string& error) {
    void* root = find_root_render_layer();
    if (root == nullptr) {
        error = "via.render.Renderer output layer was not found";
        return std::nullopt;
    }
    std::vector<void*> scenes{};
    std::unordered_set<void*> visited{};
    collect_scene_layers(root, scenes, visited);
    if (scenes.empty()) {
        error = "No via.render.layer.Scene was found under the output layer";
        return std::nullopt;
    }

    std::optional<DepthSource> best{};
    uint64_t best_pixels{};
    for (void* scene : scenes) {
        const auto enabled = invoke_no_args(scene, "get_Enable");
        if (enabled.exception_thrown || enabled.byte == 0) {
            continue;
        }
        void* texture{};
        if (!reflection_get_pointer(scene, "DepthStencilTex", texture)) {
            continue;
        }
        ID3D12Resource* resource = find_native_depth_resource(texture);
        D3D12_RESOURCE_DESC description{};
        if (!safe_resource_desc(resource, &description) ||
            description.Dimension != D3D12_RESOURCE_DIMENSION_TEXTURE2D ||
            description.Width == 0 || description.Height == 0) {
            continue;
        }
        CaptureMetadata metadata{};
        if (!camera_metadata(scene, metadata)) {
            continue;
        }
        const uint64_t pixels = description.Width * static_cast<uint64_t>(description.Height);
        if (pixels <= best_pixels) {
            continue;
        }
        resource->AddRef();
        DepthSource source{};
        source.resource.Attach(resource);
        source.scene = scene;
        source.metadata = metadata;
        best = std::move(source);
        best_pixels = pixels;
    }
    if (!best) {
        error = "Scene depth texture or camera metadata could not be resolved";
        return std::nullopt;
    }
    g_depth_source_discovered = true;
    return best;
}

std::string format_name(DXGI_FORMAT format) {
    switch (format) {
    case DXGI_FORMAT_R32G8X24_TYPELESS:
        return "R32G8X24_TYPELESS";
    case DXGI_FORMAT_D32_FLOAT_S8X24_UINT:
        return "D32_FLOAT_S8X24_UINT";
    case DXGI_FORMAT_R32_TYPELESS:
        return "R32_TYPELESS";
    case DXGI_FORMAT_D32_FLOAT:
        return "D32_FLOAT";
    case DXGI_FORMAT_R24G8_TYPELESS:
        return "R24G8_TYPELESS";
    case DXGI_FORMAT_D24_UNORM_S8_UINT:
        return "D24_UNORM_S8_UINT";
    case DXGI_FORMAT_R16_TYPELESS:
        return "R16_TYPELESS";
    case DXGI_FORMAT_D16_UNORM:
        return "D16_UNORM";
    default:
        return "DXGI_FORMAT_" + std::to_string(static_cast<uint32_t>(format));
    }
}

bool depth_layout(
    DXGI_FORMAT format,
    uint64_t unpadded_row_bytes,
    uint32_t width,
    std::string& encoding,
    uint32_t& pixel_stride,
    std::string& error) {
    if (width == 0 || unpadded_row_bytes == 0 || unpadded_row_bytes % width != 0) {
        error = "D3D12 returned an invalid depth row layout";
        return false;
    }
    pixel_stride = static_cast<uint32_t>(unpadded_row_bytes / width);
    switch (format) {
    case DXGI_FORMAT_R32G8X24_TYPELESS:
    case DXGI_FORMAT_D32_FLOAT_S8X24_UINT:
    case DXGI_FORMAT_R32_TYPELESS:
    case DXGI_FORMAT_D32_FLOAT:
        encoding = "float32";
        return pixel_stride >= 4;
    case DXGI_FORMAT_R24G8_TYPELESS:
    case DXGI_FORMAT_D24_UNORM_S8_UINT:
        encoding = "d24_unorm";
        return pixel_stride >= 4;
    case DXGI_FORMAT_R16_TYPELESS:
    case DXGI_FORMAT_D16_UNORM:
        encoding = "d16_unorm";
        return pixel_stride >= 2;
    default:
        error = "Unsupported RE9 depth format: " + format_name(format);
        return false;
    }
}

bool issue_capture(const CaptureRequest& request, std::string& error) {
    if (g_pending) {
        error = "A previous GPU depth readback is still pending";
        return false;
    }
    if (g_param == nullptr || g_param->renderer_data == nullptr ||
        g_param->renderer_data->renderer_type != REFRAMEWORK_RENDERER_D3D12) {
        error = "RE9 depth bridge requires the D3D12 renderer";
        return false;
    }
    auto* device = static_cast<ID3D12Device*>(g_param->renderer_data->device);
    auto* queue = static_cast<ID3D12CommandQueue*>(g_param->renderer_data->command_queue);
    if (!g_gpu.ensure(device, error)) {
        return false;
    }
    auto source = find_depth_source(error);
    if (!source) {
        return false;
    }
    const D3D12_RESOURCE_DESC source_description = source->resource->GetDesc();
    if (source_description.SampleDesc.Count != 1) {
        error = "Multisampled depth buffers are not supported by this readback path";
        return false;
    }
    if (source_description.Width > UINT32_MAX) {
        error = "Depth width exceeds the supported range";
        return false;
    }

    PendingCapture pending{};
    pending.request = request;
    pending.source = source->resource;
    pending.metadata = source->metadata;
    pending.metadata.width = static_cast<uint32_t>(source_description.Width);
    pending.metadata.height = source_description.Height;
    pending.metadata.dxgi_format = static_cast<uint32_t>(source_description.Format);
    pending.metadata.dxgi_format_name = format_name(source_description.Format);

    uint64_t unpadded_row_bytes{};
    device->GetCopyableFootprints(
        &source_description,
        0,
        1,
        0,
        &pending.footprint,
        &pending.row_count,
        &unpadded_row_bytes,
        &pending.total_bytes);
    pending.metadata.row_pitch = pending.footprint.Footprint.RowPitch;
    if (!depth_layout(
            source_description.Format,
            unpadded_row_bytes,
            pending.metadata.width,
            pending.metadata.depth_encoding,
            pending.metadata.pixel_stride_bytes,
            error)) {
        return false;
    }

    D3D12_HEAP_PROPERTIES heap{};
    heap.Type = D3D12_HEAP_TYPE_READBACK;
    heap.CPUPageProperty = D3D12_CPU_PAGE_PROPERTY_UNKNOWN;
    heap.MemoryPoolPreference = D3D12_MEMORY_POOL_UNKNOWN;
    heap.CreationNodeMask = 1;
    heap.VisibleNodeMask = 1;
    D3D12_RESOURCE_DESC buffer{};
    buffer.Dimension = D3D12_RESOURCE_DIMENSION_BUFFER;
    buffer.Alignment = 0;
    buffer.Width = pending.total_bytes;
    buffer.Height = 1;
    buffer.DepthOrArraySize = 1;
    buffer.MipLevels = 1;
    buffer.Format = DXGI_FORMAT_UNKNOWN;
    buffer.SampleDesc.Count = 1;
    buffer.Layout = D3D12_TEXTURE_LAYOUT_ROW_MAJOR;
    if (FAILED(device->CreateCommittedResource(
            &heap,
            D3D12_HEAP_FLAG_NONE,
            &buffer,
            D3D12_RESOURCE_STATE_COPY_DEST,
            nullptr,
            IID_PPV_ARGS(pending.readback.ReleaseAndGetAddressOf())))) {
        error = "Creating the depth readback buffer failed";
        return false;
    }

    if (FAILED(g_gpu.allocator->Reset()) ||
        FAILED(g_gpu.command_list->Reset(g_gpu.allocator.Get(), nullptr))) {
        error = "Resetting the depth copy command objects failed";
        return false;
    }
    D3D12_RESOURCE_BARRIER barrier{};
    barrier.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
    barrier.Transition.pResource = pending.source.Get();
    barrier.Transition.Subresource = 0;
    barrier.Transition.StateBefore = D3D12_RESOURCE_STATE_DEPTH_WRITE;
    barrier.Transition.StateAfter = D3D12_RESOURCE_STATE_COPY_SOURCE;
    g_gpu.command_list->ResourceBarrier(1, &barrier);

    D3D12_TEXTURE_COPY_LOCATION source_location{};
    source_location.pResource = pending.source.Get();
    source_location.Type = D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX;
    source_location.SubresourceIndex = 0;
    D3D12_TEXTURE_COPY_LOCATION destination_location{};
    destination_location.pResource = pending.readback.Get();
    destination_location.Type = D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT;
    destination_location.PlacedFootprint = pending.footprint;
    g_gpu.command_list->CopyTextureRegion(&destination_location, 0, 0, 0, &source_location, nullptr);

    std::swap(barrier.Transition.StateBefore, barrier.Transition.StateAfter);
    g_gpu.command_list->ResourceBarrier(1, &barrier);
    if (FAILED(g_gpu.command_list->Close())) {
        error = "Closing the depth copy command list failed";
        return false;
    }
    ID3D12CommandList* lists[]{g_gpu.command_list.Get()};
    queue->ExecuteCommandLists(1, lists);
    pending.fence_value = ++g_gpu.next_fence_value;
    if (FAILED(queue->Signal(g_gpu.fence.Get(), pending.fence_value))) {
        error = "Signaling the depth readback fence failed";
        return false;
    }
    g_pending = std::move(pending);
    return true;
}

bool write_pending_raw(PendingCapture& pending, std::string& error) {
    std::error_code filesystem_error{};
    std::filesystem::create_directories(pending.request.raw_output_path.parent_path(), filesystem_error);
    if (filesystem_error) {
        error = "Cannot create raw depth output directory: " + filesystem_error.message();
        return false;
    }

    void* mapped{};
    const D3D12_RANGE read_range{0, static_cast<SIZE_T>(pending.total_bytes)};
    if (FAILED(pending.readback->Map(0, &read_range, &mapped)) || mapped == nullptr) {
        error = "Mapping the completed depth readback failed";
        return false;
    }
    const auto unmap = [&pending]() {
        const D3D12_RANGE written_range{0, 0};
        pending.readback->Unmap(0, &written_range);
    };

    std::ofstream stream(pending.request.raw_output_path, std::ios::binary | std::ios::trunc);
    if (!stream) {
        unmap();
        error = "Cannot open raw depth output file";
        return false;
    }
    const auto* source = static_cast<const uint8_t*>(mapped) + pending.footprint.Offset;
    const uint64_t bytes_to_write =
        static_cast<uint64_t>(pending.metadata.row_pitch) * pending.metadata.height;
    stream.write(reinterpret_cast<const char*>(source), static_cast<std::streamsize>(bytes_to_write));
    stream.flush();
    const bool success = static_cast<bool>(stream);
    stream.close();
    unmap();
    if (!success) {
        error = "Writing the raw depth file failed";
        return false;
    }
    return true;
}

void finish_pending_capture() {
    if (!g_pending || !g_gpu.fence || g_gpu.fence->GetCompletedValue() < g_pending->fence_value) {
        return;
    }
    PendingCapture pending = std::move(*g_pending);
    g_pending.reset();
    std::string error{};
    if (!write_pending_raw(pending, error)) {
        write_capture_error(pending.request.capture_id, error);
        return;
    }

    const CaptureMetadata& metadata = pending.metadata;
    json payload{
        {"schema_version", kSchemaVersion},
        {"plugin_version", kPluginVersion},
        {"capture_id", pending.request.capture_id},
        {"status", "ok"},
        {"raw_path", path_to_utf8(pending.request.raw_output_path)},
        {"width", metadata.width},
        {"height", metadata.height},
        {"row_pitch", metadata.row_pitch},
        {"pixel_stride_bytes", metadata.pixel_stride_bytes},
        {"depth_encoding", metadata.depth_encoding},
        {"dxgi_format", metadata.dxgi_format},
        {"dxgi_format_name", metadata.dxgi_format_name},
        {"near_clip", metadata.near_clip},
        {"far_clip", metadata.far_clip},
        {"fov", metadata.fov},
        {"projection_matrix", matrix_json(metadata.projection_matrix)},
        {"inverse_projection_matrix", matrix_json(metadata.inverse_projection_matrix)},
        {"render_frame_id", metadata.render_frame_id},
        {"scene_view_id", metadata.scene_view_id},
        {"requested_at_unix", pending.request.requested_at_unix},
        {"captured_at_unix", unix_time_seconds()},
    };
    if (!write_json_atomic(g_data_directory / kStatusFilename, payload, error)) {
        log_error("Depth raw file was written, but status publishing failed: " + error);
        return;
    }
    log_info("Captured per-pixel depth " + pending.request.capture_id + " (" +
        std::to_string(metadata.width) + "x" + std::to_string(metadata.height) + ")");
}

void on_present() {
    write_heartbeat();
    finish_pending_capture();
    const auto request = read_new_request();
    if (!request) {
        return;
    }
    g_last_handled_capture_id = request->capture_id;
    if (g_pending) {
        write_capture_error(request->capture_id, "A previous depth capture is still pending");
        return;
    }
    write_processing_status(*request);
    std::string error{};
    if (!issue_capture(*request, error)) {
        write_capture_error(request->capture_id, error);
    }
}

void on_device_reset() {
    if (g_pending) {
        write_capture_error(g_pending->request.capture_id, "D3D12 device reset during depth capture");
        g_pending.reset();
    }
    g_gpu.reset();
    g_texture_resource_offset.reset();
    g_depth_source_discovered = false;
}
} // namespace

extern "C" __declspec(dllexport) void reframework_plugin_required_version(REFrameworkPluginVersion* version) {
    version->major = REFRAMEWORK_PLUGIN_VERSION_MAJOR;
    version->minor = REFRAMEWORK_PLUGIN_VERSION_MINOR;
    version->patch = REFRAMEWORK_PLUGIN_VERSION_PATCH;
    version->game_name = "RE9";
}

extern "C" __declspec(dllexport) bool reframework_plugin_initialize(
    const REFrameworkPluginInitializeParam* param) {
    if (param == nullptr || param->functions == nullptr || param->renderer_data == nullptr || param->sdk == nullptr) {
        return false;
    }
    API::initialize(param);
    g_param = param;
    g_data_directory = data_directory_from_module(static_cast<HMODULE>(param->reframework_module));
    std::error_code ignored{};
    std::filesystem::create_directories(g_data_directory, ignored);
    param->functions->on_present(on_present);
    param->functions->on_device_reset(on_device_reset);
    log_info("Initialized v" + std::string{kPluginVersion} + "; data directory: " + path_to_utf8(g_data_directory));
    return true;
}
