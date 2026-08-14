#include "UeCameraProfile.h"

#include <windows.h>
#include <tlhelp32.h>

#include <algorithm>
#include <cwchar>
#include <cwctype>
#include <iostream>
#include <string>
#include <vector>

namespace
{
constexpr wchar_t kRuntimeName[] = L"UeCameraRuntime.dll";
constexpr wchar_t kLegacyRuntimeName[] = L"BmwCameraBridge.dll";
constexpr const wchar_t* kForeignConflicts[] = {
    L"UniversalUE5Unlocker.dll",
    L"IgcsConnector.addon64",
};

struct Handle
{
    HANDLE value{nullptr};
    ~Handle()
    {
        if (value != nullptr && value != INVALID_HANDLE_VALUE)
        {
            CloseHandle(value);
        }
    }
    Handle() = default;
    explicit Handle(HANDLE input) : value(input) {}
    Handle(const Handle&) = delete;
    Handle& operator=(const Handle&) = delete;
};

std::wstring lower(std::wstring value)
{
    std::transform(value.begin(), value.end(), value.begin(),
        [](wchar_t character) { return static_cast<wchar_t>(std::towlower(character)); });
    return value;
}

bool sameName(const wchar_t* first, const wchar_t* second)
{
    return first != nullptr && second != nullptr && lower(first) == lower(second);
}

bool supportedProcessName(const wchar_t* name)
{
    for (const auto* profile : ue_camera_runtime::registeredProfiles())
    {
        for (std::size_t index = 0; index < profile->processNameCount; ++index)
        {
            if (sameName(name, profile->processNames[index]))
            {
                return true;
            }
        }
    }
    return false;
}

DWORD findProcess(const wchar_t* requestedName)
{
    Handle snapshot(CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0));
    if (snapshot.value == INVALID_HANDLE_VALUE)
    {
        return 0;
    }
    PROCESSENTRY32W entry{};
    entry.dwSize = sizeof(entry);
    if (!Process32FirstW(snapshot.value, &entry))
    {
        return 0;
    }
    do
    {
        if ((requestedName != nullptr && sameName(entry.szExeFile, requestedName)) ||
            (requestedName == nullptr && supportedProcessName(entry.szExeFile)))
        {
            return entry.th32ProcessID;
        }
    } while (Process32NextW(snapshot.value, &entry));
    return 0;
}

std::vector<std::wstring> listModules(const DWORD processId)
{
    std::vector<std::wstring> result;
    Handle snapshot(CreateToolhelp32Snapshot(
        TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, processId));
    if (snapshot.value == INVALID_HANDLE_VALUE)
    {
        return result;
    }
    MODULEENTRY32W entry{};
    entry.dwSize = sizeof(entry);
    if (!Module32FirstW(snapshot.value, &entry))
    {
        return result;
    }
    do
    {
        result.push_back(lower(entry.szModule));
    } while (Module32NextW(snapshot.value, &entry));
    return result;
}

bool contains(const std::vector<std::wstring>& values, const wchar_t* name)
{
    return std::find(values.begin(), values.end(), lower(name)) != values.end();
}

std::wstring defaultRuntimePath()
{
    std::vector<wchar_t> path(32768);
    const DWORD length = GetModuleFileNameW(nullptr, path.data(), static_cast<DWORD>(path.size()));
    if (length == 0 || length >= path.size())
    {
        return kRuntimeName;
    }
    std::wstring value(path.data(), length);
    const std::size_t separator = value.find_last_of(L"\\/");
    return separator == std::wstring::npos
        ? std::wstring(kRuntimeName)
        : value.substr(0, separator + 1) + kRuntimeName;
}

bool parsePid(const wchar_t* text, DWORD& result)
{
    if (text == nullptr || *text == L'\0')
    {
        return false;
    }
    wchar_t* end = nullptr;
    const unsigned long value = std::wcstoul(text, &end, 10);
    if (end == text || *end != L'\0' || value == 0)
    {
        return false;
    }
    result = static_cast<DWORD>(value);
    return true;
}

int fail(const wchar_t* message)
{
    std::wcerr << L"UE_CAMERA_INJECT_ERROR " << message
               << L" winerr=" << GetLastError() << std::endl;
    return 1;
}

void printProfiles()
{
    for (const auto* profile : ue_camera_runtime::registeredProfiles())
    {
        std::wcout << L"profile=" << profile->id << L" engine=" << profile->engine << L" processes=";
        for (std::size_t index = 0; index < profile->processNameCount; ++index)
        {
            if (index != 0) std::wcout << L",";
            std::wcout << profile->processNames[index];
        }
        std::wcout << std::endl;
    }
}
} // namespace

int wmain(int argc, wchar_t** argv)
{
    DWORD processId = 0;
    std::wstring requestedProcess;
    std::wstring runtimePath = defaultRuntimePath();
    bool listOnly = false;

    for (int index = 1; index < argc; ++index)
    {
        const std::wstring argument(argv[index]);
        if (argument == L"--list")
        {
            listOnly = true;
        }
        else if (argument == L"--pid" && index + 1 < argc)
        {
            if (!parsePid(argv[++index], processId)) return fail(L"invalid --pid value");
        }
        else if (argument == L"--process" && index + 1 < argc)
        {
            requestedProcess = argv[++index];
        }
        else if (argument == L"--dll" && index + 1 < argc)
        {
            runtimePath = argv[++index];
        }
        else
        {
            return fail(L"unknown argument; use --list, --pid, --process, or --dll");
        }
    }
    if (listOnly)
    {
        printProfiles();
        return 0;
    }
    if (processId == 0)
    {
        processId = findProcess(requestedProcess.empty() ? nullptr : requestedProcess.c_str());
    }
    if (processId == 0)
    {
        return fail(L"no supported UE game process was found; use --process or --pid");
    }

    const DWORD attributes = GetFileAttributesW(runtimePath.c_str());
    if (attributes == INVALID_FILE_ATTRIBUTES || (attributes & FILE_ATTRIBUTE_DIRECTORY) != 0)
    {
        return fail(L"UeCameraRuntime.dll was not found");
    }
    const auto modules = listModules(processId);
    for (const wchar_t* conflict : kForeignConflicts)
    {
        if (contains(modules, conflict))
        {
            return fail(L"a camera runtime or conflicting UE connector is already loaded; restart the game first");
        }
    }
    if (contains(modules, kRuntimeName) || contains(modules, kLegacyRuntimeName))
    {
        std::wcout << L"UE_CAMERA_INJECT_OK pid=" << processId
                   << L" runtime=UeCameraRuntime.dll already_loaded=1" << std::endl;
        return 0;
    }

    Handle process(OpenProcess(
        PROCESS_CREATE_THREAD | PROCESS_QUERY_INFORMATION | PROCESS_VM_OPERATION |
            PROCESS_VM_WRITE | PROCESS_VM_READ,
        FALSE, processId));
    if (process.value == nullptr)
    {
        return fail(L"OpenProcess failed; run the injector at the same privilege level as the game");
    }
    const std::size_t bytes = (runtimePath.size() + 1) * sizeof(wchar_t);
    void* remote = VirtualAllocEx(process.value, nullptr, bytes, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (remote == nullptr)
    {
        return fail(L"VirtualAllocEx failed");
    }
    SIZE_T written = 0;
    if (!WriteProcessMemory(process.value, remote, runtimePath.c_str(), bytes, &written) || written != bytes)
    {
        VirtualFreeEx(process.value, remote, 0, MEM_RELEASE);
        return fail(L"WriteProcessMemory failed");
    }
    const HMODULE kernel32 = GetModuleHandleW(L"kernel32.dll");
    const FARPROC loadLibrary = kernel32 == nullptr ? nullptr : GetProcAddress(kernel32, "LoadLibraryW");
    if (loadLibrary == nullptr)
    {
        VirtualFreeEx(process.value, remote, 0, MEM_RELEASE);
        return fail(L"LoadLibraryW was not found");
    }
    Handle thread(CreateRemoteThread(process.value, nullptr, 0,
        reinterpret_cast<LPTHREAD_START_ROUTINE>(loadLibrary), remote, 0, nullptr));
    if (thread.value == nullptr)
    {
        VirtualFreeEx(process.value, remote, 0, MEM_RELEASE);
        return fail(L"CreateRemoteThread failed");
    }
    if (WaitForSingleObject(thread.value, 15000) != WAIT_OBJECT_0)
    {
        VirtualFreeEx(process.value, remote, 0, MEM_RELEASE);
        return fail(L"waiting for the runtime load timed out");
    }
    DWORD exitCode = 0;
    if (!GetExitCodeThread(thread.value, &exitCode) || exitCode == 0)
    {
        VirtualFreeEx(process.value, remote, 0, MEM_RELEASE);
        return fail(L"LoadLibraryW failed in the target process");
    }
    VirtualFreeEx(process.value, remote, 0, MEM_RELEASE);
    std::wcout << L"UE_CAMERA_INJECT_OK pid=" << processId
               << L" runtime=UeCameraRuntime.dll" << std::endl;
    return 0;
}
