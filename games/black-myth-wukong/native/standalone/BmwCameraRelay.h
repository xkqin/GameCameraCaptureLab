#pragma once

#include <cstdint>
#include <windows.h>

namespace bmw_camera
{
HANDLE startRelay(std::uint8_t* mapping, volatile LONG* stopRequested);
void stopRelay(HANDLE& thread);
} // namespace bmw_camera
