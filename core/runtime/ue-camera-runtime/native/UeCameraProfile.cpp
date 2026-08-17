#include "UeCameraProfile.h"

#include <windows.h>

#include <algorithm>
#include <cwctype>
#include <iterator>
#include <string>

namespace ue_camera_runtime
{
namespace
{
constexpr int kBlackMythCameraPattern[] = {
    0x0F, 0x10, 0x02,
    -1, -1, -1, -1, -1, -1,
    0x0F, 0x11, 0x01,
    0xF2, 0x0F, 0x10, 0x4A, 0x10,
    0xF2, 0x0F, 0x11, 0x49, 0x10,
    0x0F, 0x10, 0x42, 0x18,
    0x0F, 0x11, 0x41, 0x18,
    0xF2, 0x0F, 0x10, 0x4A, 0x28,
    0xF2, 0x0F, 0x11, 0x49, 0x28,
    0x8B, 0x42, 0x30, 0x89, 0x41, 0x30,
    0x8B, 0x42, 0x34, 0x89, 0x41, 0x34,
    0x8B, 0x42, 0x38, 0x89, 0x41, 0x38,
};

constexpr int kBlackMythHudPattern[] = {
    0x0F, 0x10, 0x83, 0x70, 0x02, 0x00, 0x00,
    0x0F, 0x11, 0x4D, 0x8C,
    0x0F, 0x59, 0xF0,
    0x0F, 0x11, 0x45, 0x08,
    0x0F, 0x11, 0x74, 0x24, 0x6C,
};

constexpr wchar_t kBlackMythProcesses[] = L"b1-Win64-Shipping.exe";
constexpr wchar_t kBlackMythProcessesAlt[] = L"BlackMythWukong.exe";
constexpr const wchar_t* kBlackMythProcessNames[] = {
    kBlackMythProcesses,
    kBlackMythProcessesAlt,
};

constexpr wchar_t kBackroomsLostRunnersProcess[] =
    L"BackroomsLostRunners-Win64-Shipping.exe";
constexpr const wchar_t* kBackroomsLostRunnersProcessNames[] = {
    kBackroomsLostRunnersProcess,
};

const HookProfile kBlackMythHudHook{
    "black_myth_hud_opacity_v1",
    kBlackMythHudPattern,
    std::size(kBlackMythHudPattern),
    0,
    14,
    1,
    1,
};

const GameProfile kBlackMythProfile{
    "black-myth-wukong",
    "Black Myth: Wukong",
    "ue5",
    kBlackMythProcessNames,
    std::size(kBlackMythProcessNames),
    {
        "fminimal_view_info_copy_lwc_v1",
        kBlackMythCameraPattern,
        std::size(kBlackMythCameraPattern),
        9,
        0x25,
        1,
        2,
    },
    &kBlackMythHudHook,
};

// UE 5.6.0, ProductVersion ++UE5+Release-5.6-CL-44394996.
// The three validated copy sites use the same RCX destination / RDX source
// LWC FMinimalViewInfo prefix as the existing adapter. HUD control remains
// disabled until a game-specific draw path has been validated.
const GameProfile kBackroomsLostRunnersProfile{
    "backrooms-lost-runners",
    "Backrooms Lost Runners",
    "ue5",
    kBackroomsLostRunnersProcessNames,
    std::size(kBackroomsLostRunnersProcessNames),
    {
        "fminimal_view_info_copy_lwc_v1",
        kBlackMythCameraPattern,
        std::size(kBlackMythCameraPattern),
        9,
        0x25,
        3,
        3,
    },
    nullptr,
};

std::vector<const GameProfile*> makeRegistry()
{
    return {&kBlackMythProfile, &kBackroomsLostRunnersProfile};
}

bool sameName(const wchar_t* first, const wchar_t* second)
{
    if (first == nullptr || second == nullptr)
    {
        return false;
    }
    while (*first != L'\0' && *second != L'\0')
    {
        if (std::towlower(*first) != std::towlower(*second))
        {
            return false;
        }
        ++first;
        ++second;
    }
    return *first == L'\0' && *second == L'\0';
}

bool matchesPattern(const std::uint8_t* address, const HookProfile& profile)
{
    for (std::size_t index = 0; index < profile.patternSize; ++index)
    {
        const int expected = profile.pattern[index];
        if (expected >= 0 && address[index] != static_cast<std::uint8_t>(expected))
        {
            return false;
        }
    }
    return true;
}
} // namespace

const std::vector<const GameProfile*>& registeredProfiles()
{
    static const std::vector<const GameProfile*> registry = makeRegistry();
    return registry;
}

const GameProfile* currentProcessProfile()
{
    wchar_t processPath[32768]{};
    const DWORD length = GetModuleFileNameW(nullptr, processPath, std::size(processPath));
    if (length == 0 || length >= std::size(processPath))
    {
        return nullptr;
    }
    const wchar_t* executable = processPath + length;
    while (executable > processPath && executable[-1] != L'\\' && executable[-1] != L'/')
    {
        --executable;
    }
    for (const GameProfile* profile : registeredProfiles())
    {
        for (std::size_t index = 0; index < profile->processNameCount; ++index)
        {
            if (sameName(executable, profile->processNames[index]))
            {
                return profile;
            }
        }
    }
    return nullptr;
}

std::vector<std::uint8_t*> locateProfileHooks(const HookProfile& profile)
{
    std::vector<std::uint8_t*> result;
    auto* base = reinterpret_cast<std::uint8_t*>(GetModuleHandleW(nullptr));
    if (base == nullptr)
    {
        return result;
    }
    const auto* dos = reinterpret_cast<const IMAGE_DOS_HEADER*>(base);
    if (dos->e_magic != IMAGE_DOS_SIGNATURE)
    {
        return result;
    }
    const auto* nt = reinterpret_cast<const IMAGE_NT_HEADERS64*>(base + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE)
    {
        return result;
    }
    const auto* sections = IMAGE_FIRST_SECTION(nt);
    for (WORD sectionIndex = 0; sectionIndex < nt->FileHeader.NumberOfSections; ++sectionIndex)
    {
        const auto& section = sections[sectionIndex];
        if ((section.Characteristics & IMAGE_SCN_MEM_EXECUTE) == 0 ||
            section.Misc.VirtualSize < profile.patternSize)
        {
            continue;
        }
        auto* start = base + section.VirtualAddress;
        const std::size_t size = section.Misc.VirtualSize;
        for (std::size_t offset = 0; offset <= size - profile.patternSize; ++offset)
        {
            if (matchesPattern(start + offset, profile))
            {
                result.push_back(start + offset + profile.hookOffset);
                offset += profile.patternSize - 1;
            }
        }
    }
    return result;
}
} // namespace ue_camera_runtime
