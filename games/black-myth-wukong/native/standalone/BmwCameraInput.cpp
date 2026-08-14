#include "BmwCameraInput.h"

#include <windows.h>

#include <array>
#include <cstdint>

extern "C" volatile LONG g_bmwCameraEnabled;

namespace bmw_camera
{
namespace
{
constexpr DWORD kInputStartTimeoutMilliseconds = 2000;

HANDLE g_inputThread = nullptr;
HANDLE g_inputReadyEvent = nullptr;
DWORD g_inputThreadId = 0;
HHOOK g_keyboardHook = nullptr;
HHOOK g_mouseHook = nullptr;
volatile LONG g_inputStopRequested = 0;
volatile LONG g_inputReady = 0;
volatile LONG g_mouseCaptureActive = 0;
volatile LONG g_mouseDeltaX = 0;
volatile LONG g_mouseDeltaY = 0;
std::array<volatile LONG, 256> g_keyDown{};
std::array<volatile LONG, 256> g_keyPressed{};
std::array<volatile LONG, 256> g_swallowed{};

int normalizedKey(const int virtualKey)
{
    switch (virtualKey)
    {
    case VK_LSHIFT:
    case VK_RSHIFT:
        return VK_SHIFT;
    case VK_LCONTROL:
    case VK_RCONTROL:
        return VK_CONTROL;
    default:
        return virtualKey;
    }
}

bool validKey(const int virtualKey)
{
    return virtualKey >= 0 && virtualKey < static_cast<int>(g_keyDown.size());
}

void clearInputState()
{
    for (std::size_t index = 0; index < g_keyDown.size(); ++index)
    {
        InterlockedExchange(&g_keyDown[index], 0);
        InterlockedExchange(&g_keyPressed[index], 0);
        InterlockedExchange(&g_swallowed[index], 0);
    }
    InterlockedExchange(&g_mouseCaptureActive, 0);
    InterlockedExchange(&g_mouseDeltaX, 0);
    InterlockedExchange(&g_mouseDeltaY, 0);
}

bool foregroundBelongsToGame()
{
    DWORD processId = 0;
    const HWND foreground = GetForegroundWindow();
    if (foreground == nullptr)
    {
        return false;
    }
    GetWindowThreadProcessId(foreground, &processId);
    return processId == GetCurrentProcessId();
}

bool gameWindowCenter(POINT& center)
{
    const HWND window = GetForegroundWindow();
    DWORD processId = 0;
    if (window == nullptr ||
        GetWindowThreadProcessId(window, &processId) == 0 ||
        processId != GetCurrentProcessId())
    {
        return false;
    }
    RECT client{};
    POINT topLeft{};
    if (!GetClientRect(window, &client) ||
        !ClientToScreen(window, &topLeft) ||
        client.right <= client.left || client.bottom <= client.top)
    {
        return false;
    }
    center.x = topLeft.x + (client.right - client.left) / 2;
    center.y = topLeft.y + (client.bottom - client.top) / 2;
    return true;
}

bool isBridgeHotkey(const int virtualKey)
{
    return virtualKey == VK_INSERT || virtualKey == VK_HOME || virtualKey == VK_DELETE;
}

bool isCameraControlKey(const int virtualKey)
{
    switch (virtualKey)
    {
    case 'W':
    case 'A':
    case 'S':
    case 'D':
    case 'Q':
    case 'E':
    case 'Z':
    case 'C':
    case VK_LEFT:
    case VK_UP:
    case VK_RIGHT:
    case VK_DOWN:
    case VK_NUMPAD1:
    case VK_NUMPAD3:
    case VK_NUMPAD4:
    case VK_NUMPAD5:
    case VK_NUMPAD6:
    case VK_NUMPAD7:
    case VK_NUMPAD8:
    case VK_NUMPAD9:
    case VK_ADD:
    case VK_SUBTRACT:
    case VK_SHIFT:
    case VK_CONTROL:
        return true;
    default:
        return false;
    }
}

LRESULT CALLBACK lowLevelKeyboardProc(
    const int code,
    const WPARAM message,
    const LPARAM parameter)
{
    if (code != HC_ACTION || parameter == 0)
    {
        return CallNextHookEx(g_keyboardHook, code, message, parameter);
    }
    const auto* event = reinterpret_cast<const KBDLLHOOKSTRUCT*>(parameter);
    const int key = normalizedKey(static_cast<int>(event->vkCode));
    if (!validKey(key))
    {
        return CallNextHookEx(g_keyboardHook, code, message, parameter);
    }

    const bool keyDownMessage = message == WM_KEYDOWN || message == WM_SYSKEYDOWN;
    const bool keyUpMessage = message == WM_KEYUP || message == WM_SYSKEYUP;
    if (!keyDownMessage && !keyUpMessage)
    {
        return CallNextHookEx(g_keyboardHook, code, message, parameter);
    }

    const bool gameFocused = foregroundBelongsToGame();
    if (!gameFocused)
    {
        clearInputState();
        return CallNextHookEx(g_keyboardHook, code, message, parameter);
    }

    const bool cameraEnabled =
        InterlockedCompareExchange(&g_bmwCameraEnabled, 0, 0) != 0;
    const bool capture = isBridgeHotkey(key) ||
        (cameraEnabled && isCameraControlKey(key));

    if (keyDownMessage)
    {
        if (!capture)
        {
            return CallNextHookEx(g_keyboardHook, code, message, parameter);
        }
        if (InterlockedExchange(&g_keyDown[key], 1) == 0)
        {
            InterlockedExchange(&g_keyPressed[key], 1);
        }
        InterlockedExchange(&g_swallowed[key], 1);
        return 1;
    }

    const bool wasSwallowed = InterlockedExchange(&g_swallowed[key], 0) != 0;
    InterlockedExchange(&g_keyDown[key], 0);
    // Only consume the key-up when this hook also consumed its key-down. If a
    // movement key was pressed before Camera ON, the game must receive the
    // matching release or the character can remain stuck moving.
    return wasSwallowed
        ? 1
        : CallNextHookEx(g_keyboardHook, code, message, parameter);
}

LRESULT CALLBACK lowLevelMouseProc(
    const int code,
    const WPARAM message,
    const LPARAM parameter)
{
    if (code != HC_ACTION || message != WM_MOUSEMOVE || parameter == 0)
    {
        return CallNextHookEx(g_mouseHook, code, message, parameter);
    }
    const bool capture = foregroundBelongsToGame() &&
        InterlockedCompareExchange(&g_bmwCameraEnabled, 0, 0) != 0;
    if (!capture)
    {
        InterlockedExchange(&g_mouseCaptureActive, 0);
        InterlockedExchange(&g_mouseDeltaX, 0);
        InterlockedExchange(&g_mouseDeltaY, 0);
        return CallNextHookEx(g_mouseHook, code, message, parameter);
    }

    POINT center{};
    if (!gameWindowCenter(center))
    {
        return CallNextHookEx(g_mouseHook, code, message, parameter);
    }
    const auto* event = reinterpret_cast<const MSLLHOOKSTRUCT*>(parameter);
    if (InterlockedExchange(&g_mouseCaptureActive, 1) == 0)
    {
        InterlockedExchange(&g_mouseDeltaX, 0);
        InterlockedExchange(&g_mouseDeltaY, 0);
        SetCursorPos(center.x, center.y);
        return 1;
    }

    // SetCursorPos generates a synthetic center event. Consume it without
    // converting it to rotation, otherwise cursor recentering feeds itself.
    if ((event->flags & LLMHF_INJECTED) != 0)
    {
        return 1;
    }
    const LONG deltaX = event->pt.x - center.x;
    const LONG deltaY = event->pt.y - center.y;
    if (deltaX != 0 || deltaY != 0)
    {
        InterlockedExchangeAdd(&g_mouseDeltaX, deltaX);
        InterlockedExchangeAdd(&g_mouseDeltaY, deltaY);
        SetCursorPos(center.x, center.y);
    }
    return 1;
}

DWORD WINAPI inputThreadWorker(LPVOID)
{
    HMODULE module = nullptr;
    GetModuleHandleExW(
        GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
            GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
        reinterpret_cast<LPCWSTR>(&lowLevelKeyboardProc),
        &module);
    g_keyboardHook = SetWindowsHookExW(
        WH_KEYBOARD_LL, lowLevelKeyboardProc, module, 0);
    g_mouseHook = SetWindowsHookExW(
        WH_MOUSE_LL, lowLevelMouseProc, module, 0);
    const bool hooksReady = g_keyboardHook != nullptr && g_mouseHook != nullptr;
    InterlockedExchange(&g_inputReady, hooksReady ? 1 : 0);
    if (g_inputReadyEvent != nullptr)
    {
        SetEvent(g_inputReadyEvent);
    }
    if (!hooksReady)
    {
        if (g_keyboardHook != nullptr)
        {
            UnhookWindowsHookEx(g_keyboardHook);
            g_keyboardHook = nullptr;
        }
        if (g_mouseHook != nullptr)
        {
            UnhookWindowsHookEx(g_mouseHook);
            g_mouseHook = nullptr;
        }
        return 0;
    }

    MSG message{};
    while (InterlockedCompareExchange(&g_inputStopRequested, 0, 0) == 0 &&
        GetMessageW(&message, nullptr, 0, 0) > 0)
    {
        TranslateMessage(&message);
        DispatchMessageW(&message);
    }

    UnhookWindowsHookEx(g_mouseHook);
    g_mouseHook = nullptr;
    UnhookWindowsHookEx(g_keyboardHook);
    g_keyboardHook = nullptr;
    clearInputState();
    InterlockedExchange(&g_inputReady, 0);
    return 0;
}
} // namespace

bool startInputCapture()
{
    if (g_inputThread != nullptr)
    {
        return inputCaptureReady();
    }
    clearInputState();
    InterlockedExchange(&g_inputStopRequested, 0);
    InterlockedExchange(&g_inputReady, 0);
    g_inputReadyEvent = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    if (g_inputReadyEvent == nullptr)
    {
        return false;
    }
    g_inputThread = CreateThread(
        nullptr, 0, inputThreadWorker, nullptr, 0, &g_inputThreadId);
    if (g_inputThread == nullptr)
    {
        CloseHandle(g_inputReadyEvent);
        g_inputReadyEvent = nullptr;
        g_inputThreadId = 0;
        return false;
    }
    WaitForSingleObject(g_inputReadyEvent, kInputStartTimeoutMilliseconds);
    CloseHandle(g_inputReadyEvent);
    g_inputReadyEvent = nullptr;
    return inputCaptureReady();
}

void stopInputCapture()
{
    InterlockedExchange(&g_inputStopRequested, 1);
    if (g_inputThreadId != 0)
    {
        PostThreadMessageW(g_inputThreadId, WM_QUIT, 0, 0);
    }
    if (g_inputThread != nullptr)
    {
        WaitForSingleObject(g_inputThread, 2000);
        CloseHandle(g_inputThread);
        g_inputThread = nullptr;
    }
    g_inputThreadId = 0;
    clearInputState();
    InterlockedExchange(&g_inputReady, 0);
}

bool inputCaptureReady()
{
    return InterlockedCompareExchange(&g_inputReady, 0, 0) != 0;
}

bool inputCaptureActive()
{
    return inputCaptureReady() && inputGameHasFocus() &&
        InterlockedCompareExchange(&g_bmwCameraEnabled, 0, 0) != 0;
}

bool inputGameHasFocus()
{
    return foregroundBelongsToGame();
}

bool inputKeyDown(const int virtualKey)
{
    const int key = normalizedKey(virtualKey);
    return validKey(key) && InterlockedCompareExchange(&g_keyDown[key], 0, 0) != 0;
}

bool inputConsumePress(const int virtualKey)
{
    const int key = normalizedKey(virtualKey);
    return validKey(key) && InterlockedExchange(&g_keyPressed[key], 0) != 0;
}

void inputConsumeMouseDelta(LONG& deltaX, LONG& deltaY)
{
    deltaX = InterlockedExchange(&g_mouseDeltaX, 0);
    deltaY = InterlockedExchange(&g_mouseDeltaY, 0);
}
} // namespace bmw_camera
