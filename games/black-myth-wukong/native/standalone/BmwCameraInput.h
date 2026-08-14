#pragma once

#include <windows.h>

namespace bmw_camera
{
bool startInputCapture();
void stopInputCapture();
bool inputCaptureReady();
bool inputCaptureActive();
bool inputGameHasFocus();
bool inputKeyDown(int virtualKey);
bool inputConsumePress(int virtualKey);
void inputConsumeMouseDelta(LONG& deltaX, LONG& deltaY);
} // namespace bmw_camera
