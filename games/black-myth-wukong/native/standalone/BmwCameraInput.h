#pragma once

#include <windows.h>
#include <cstdint>

namespace bmw_camera
{
bool startInputCapture();
void stopInputCapture();
bool inputCaptureReady();
bool inputCaptureActive();
bool inputUsesWindowCapture();
std::uint32_t inputCaptureDiagnostic();
bool inputGameHasFocus();
bool inputKeyDown(int virtualKey);
bool inputConsumePress(int virtualKey);
void inputConsumeMouseDelta(LONG& deltaX, LONG& deltaY);
} // namespace bmw_camera
