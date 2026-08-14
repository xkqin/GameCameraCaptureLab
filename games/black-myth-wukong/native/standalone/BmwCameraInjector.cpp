#include <windows.h>
#include <tlhelp32.h>

#include <algorithm>
#include <cwctype>
#include <iostream>
#include <string>
#include <vector>

namespace
{
constexpr wchar_t kBridgeName[] = L"BmwCameraBridge.dll";
constexpr const wchar_t* kGameNames[] = {
    L"b1-Win64-Shipping.exe",
    L"BlackMythWukong.exe",
};
constexpr const wchar_t* kConflicts[] = {
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

std::wstring lowercase(std::wstring value)
{
    std::transform(value.begin(), value.end(), value.begin(),
        [](wchar_t character) { return static_cast<wchar_t>(std::towlower(character)); });
    return value;
}

bool sameName(const wchar_t* first, const wchar_t* second)
{
    return first != nullptr && second != nullptr &&
        lowercase(first) == lowercase(second);
}

DWORD findGameProcess()
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
        for (const wchar_t* name : kGameNames)
        {
            if (sameName(entry.szExeFile, name))
            {
                return entry.th32ProcessID;
            }
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
        result.push_back(lowercase(entry.szModule));
    } while (Module32NextW(snapshot.value, &entry));
    return result;
}

bool contains(const std::vector<std::wstring>& values, const wchar_t* name)
{
    return std::find(values.begin(), values.end(), lowercase(name)) != values.end();
}

std::wstring defaultBridgePath()
{
    std::vector<wchar_t> path(32768);
    const DWORD length = GetModuleFileNameW(nullptr, path.data(),
        static_cast<DWORD>(path.size()));
    if (length == 0 || length >= path.size())
    {
        return kBridgeName;
    }
    std::wstring value(path.data(), length);
    const std::size_t separator = value.find_last_of(L"\\/");
    if (separator == std::wstring::npos)
    {
        return kBridgeName;
    }
    return value.substr(0, separator + 1) + kBridgeName;
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
    std::wcerr << L"BMW_CAMERA_INJECT_ERROR " << message
               << L" winerr=" << GetLastError() << std::endl;
    return 1;
}
} // namespace

int wmain(int argc, wchar_t** argv)
{
    DWORD processId = 0;
    std::wstring bridgePath = defaultBridgePath();
    for (int index = 1; index < argc; ++index)
    {
        if (std::wstring(argv[index]) == L"--pid" && index + 1 < argc)
        {
            if (!parsePid(argv[++index], processId))
            {
                return fail(L"invalid --pid value");
            }
        }
        else
        {
            bridgePath = argv[index];
        }
    }
    if (processId == 0)
    {
        processId = findGameProcess();
    }
    if (processId == 0)
    {
        return fail(L"Black Myth game process was not found");
    }

    const DWORD attributes = GetFileAttributesW(bridgePath.c_str());
    if (attributes == INVALID_FILE_ATTRIBUTES || (attributes & FILE_ATTRIBUTE_DIRECTORY) != 0)
    {
        return fail(L"BmwCameraBridge.dll was not found");
    }

    const auto modules = listModules(processId);
    for (const wchar_t* conflict : kConflicts)
    {
        if (contains(modules, conflict))
        {
            return fail(L"UUU or an old Connector is already loaded; restart the game first");
        }
    }
    if (contains(modules, kBridgeName))
    {
        std::wcout << L"BMW_CAMERA_INJECT_OK pid=" << processId
                   << L" already_loaded=1" << std::endl;
        return 0;
    }

    Handle process(OpenProcess(
        PROCESS_CREATE_THREAD | PROCESS_QUERY_INFORMATION | PROCESS_VM_OPERATION |
            PROCESS_VM_WRITE | PROCESS_VM_READ,
        FALSE,
        processId));
    if (process.value == nullptr)
    {
        return fail(L"OpenProcess failed; run the injector at the same privilege level as the game");
    }

    const std::size_t bytes = (bridgePath.size() + 1) * sizeof(wchar_t);
    void* remote = VirtualAllocEx(
        process.value, nullptr, bytes, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (remote == nullptr)
    {
        return fail(L"VirtualAllocEx failed");
    }
    SIZE_T written = 0;
    if (!WriteProcessMemory(
            process.value, remote, bridgePath.c_str(), bytes, &written) || written != bytes)
    {
        VirtualFreeEx(process.value, remote, 0, MEM_RELEASE);
        return fail(L"WriteProcessMemory failed");
    }

    const HMODULE kernel32 = GetModuleHandleW(L"kernel32.dll");
    const FARPROC loadLibrary = kernel32 != nullptr
        ? GetProcAddress(kernel32, "LoadLibraryW")
        : nullptr;
    if (loadLibrary == nullptr)
    {
        VirtualFreeEx(process.value, remote, 0, MEM_RELEASE);
        return fail(L"LoadLibraryW was not found");
    }

    Handle thread(CreateRemoteThread(
        process.value,
        nullptr,
        0,
        reinterpret_cast<LPTHREAD_START_ROUTINE>(loadLibrary),
        remote,
        0,
        nullptr));
    if (thread.value == nullptr)
    {
        VirtualFreeEx(process.value, remote, 0, MEM_RELEASE);
        return fail(L"CreateRemoteThread failed");
    }
    if (WaitForSingleObject(thread.value, 15000) != WAIT_OBJECT_0)
    {
        VirtualFreeEx(process.value, remote, 0, MEM_RELEASE);
        return fail(L"LoadLibraryW timed out");
    }
    DWORD exitCode = 0;
    if (!GetExitCodeThread(thread.value, &exitCode) || exitCode == 0)
    {
        VirtualFreeEx(process.value, remote, 0, MEM_RELEASE);
        return fail(L"LoadLibraryW failed in the game process");
    }
    VirtualFreeEx(process.value, remote, 0, MEM_RELEASE);

    for (int attempt = 0; attempt < 30; ++attempt)
    {
        if (contains(listModules(processId), kBridgeName))
        {
            std::wcout << L"BMW_CAMERA_INJECT_OK pid=" << processId
                       << L" already_loaded=0" << std::endl;
            return 0;
        }
        Sleep(100);
    }
    return fail(L"the remote thread returned but the bridge module was not visible");
}
