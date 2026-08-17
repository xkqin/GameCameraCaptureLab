#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

namespace ue_camera_runtime
{
struct HookProfile
{
    const char* abi{};
    const int* pattern{};
    std::size_t patternSize{};
    std::size_t hookOffset{};
    std::size_t continuationOffset{};
    std::size_t minMatches{};
    std::size_t maxMatches{};
};

struct GameProfile
{
    const char* id{};
    const char* name{};
    const char* engine{};
    const wchar_t* const* processNames{};
    std::size_t processNameCount{};
    HookProfile cameraHook{};
    const HookProfile* hudHook{};
};

// The registry is deliberately small and explicit. A new game adds a profile
// and a compatible ABI adapter; it must never fall back to a guessed hook.
const GameProfile* currentProcessProfile();
const std::vector<const GameProfile*>& registeredProfiles();

// Scan executable PE sections in the current process and return addresses at
// the profile's hook offset. This function is read-only until the caller
// explicitly installs a detour after validating the match count.
std::vector<std::uint8_t*> locateProfileHooks(const HookProfile& profile);
}
