#include "BmwCameraInput.h"

#include <windows.h>

#include <array>
#include <cstdint>
#include <vector>

extern "C" volatile LONG g_bmwCameraEnabled;

namespace bmw_camera
{
namespace
{
constexpr DWORD kInputStartTimeoutMilliseconds = 2000;
constexpr LONG kWindowMouseEdgeInset = 8;
constexpr ULONGLONG kRawMouseFallbackDelayMilliseconds = 100;

HANDLE g_inputThread = nullptr;
HANDLE g_inputReadyEvent = nullptr;
DWORD g_inputThreadId = 0;
HHOOK g_keyboardHook = nullptr;
HHOOK g_mouseHook = nullptr;
HWND g_gameWindow = nullptr;
WNDPROC g_originalWindowProc = nullptr;
volatile LONG g_inputStopRequested = 0;
volatile LONG g_inputReady = 0;
volatile LONG g_windowCaptureReady = 0;
volatile LONG g_mouseCaptureActive = 0;
volatile LONG g_mouseDeltaX = 0;
volatile LONG g_mouseDeltaY = 0;
volatile LONG g_legacyMouseValid = 0;
volatile LONG g_legacyMouseX = 0;
volatile LONG g_legacyMouseY = 0;
volatile LONG g_windowRecenterPending = 0;
volatile ULONGLONG g_lastRawMouseTick = 0;
UINT g_installWindowCaptureMessage = 0;
UINT g_restoreWindowCaptureMessage = 0;
volatile LONG g_windowActionSucceeded = 0;
volatile LONG g_inputCaptureDiagnostic = 0;
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
    InterlockedExchange(&g_legacyMouseValid, 0);
    InterlockedExchange(&g_windowRecenterPending, 0);
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
    case VK_SPACE:
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

bool captureKeyboardEvent(
    const int virtualKey,
    const bool keyDownMessage,
    const bool keyUpMessage)
{
    const int key = normalizedKey(virtualKey);
    if (!validKey(key) || (!keyDownMessage && !keyUpMessage))
    {
        return false;
    }
    const bool cameraEnabled =
        InterlockedCompareExchange(&g_bmwCameraEnabled, 0, 0) != 0;
    const bool capture = isBridgeHotkey(key) ||
        (cameraEnabled && isCameraControlKey(key));
    if (keyDownMessage)
    {
        if (!capture)
        {
            return false;
        }
        if (InterlockedExchange(&g_keyDown[key], 1) == 0)
        {
            InterlockedExchange(&g_keyPressed[key], 1);
        }
        InterlockedExchange(&g_swallowed[key], 1);
        return true;
    }

    const bool wasSwallowed = InterlockedExchange(&g_swallowed[key], 0) != 0;
    InterlockedExchange(&g_keyDown[key], 0);
    // Only consume the key-up when this input layer also consumed its
    // key-down. A movement key pressed before Camera ON must still deliver
    // its matching release to the game, otherwise the character can remain
    // stuck moving.
    return wasSwallowed;
}

struct GameWindowSearch
{
    HWND window{};
    LONG area{};
};

BOOL CALLBACK findGameWindowCallback(const HWND window, const LPARAM parameter)
{
    DWORD processId = 0;
    GetWindowThreadProcessId(window, &processId);
    if (processId != GetCurrentProcessId() || !IsWindowVisible(window) ||
        GetWindow(window, GW_OWNER) != nullptr)
    {
        return TRUE;
    }
    RECT client{};
    if (!GetClientRect(window, &client))
    {
        return TRUE;
    }
    const LONG width = client.right - client.left;
    const LONG height = client.bottom - client.top;
    const LONG area = width > 0 && height > 0 ? width * height : 0;
    auto* search = reinterpret_cast<GameWindowSearch*>(parameter);
    if (search != nullptr && area > search->area)
    {
        search->window = window;
        search->area = area;
    }
    return TRUE;
}

HWND findGameWindow()
{
    GameWindowSearch search{};
    EnumWindows(findGameWindowCallback, reinterpret_cast<LPARAM>(&search));
    return search.window;
}

LRESULT callOriginalWindowProc(
    const HWND window,
    const UINT message,
    const WPARAM wordParameter,
    const LPARAM longParameter)
{
    const WNDPROC original = g_originalWindowProc;
    return original != nullptr
        ? CallWindowProcW(original, window, message, wordParameter, longParameter)
        : DefWindowProcW(window, message, wordParameter, longParameter);
}

bool readRawInput(const LPARAM longParameter, RAWINPUT& input)
{
    UINT size = 0;
    const HRAWINPUT handle = reinterpret_cast<HRAWINPUT>(longParameter);
    if (GetRawInputData(
            handle, RID_INPUT, nullptr, &size, sizeof(RAWINPUTHEADER)) != 0 ||
        size < sizeof(RAWINPUTHEADER))
    {
        return false;
    }
    std::vector<std::uint8_t> buffer(size);
    if (GetRawInputData(
            handle, RID_INPUT, buffer.data(), &size, sizeof(RAWINPUTHEADER)) != size ||
        size < sizeof(RAWINPUTHEADER))
    {
        return false;
    }
    const auto* raw = reinterpret_cast<const RAWINPUT*>(buffer.data());
    if (raw->header.dwType != RIM_TYPEMOUSE && raw->header.dwType != RIM_TYPEKEYBOARD)
    {
        return false;
    }
    input = *raw;
    return true;
}

bool captureRawInput(const RAWINPUT& input)
{
    const bool cameraEnabled =
        InterlockedCompareExchange(&g_bmwCameraEnabled, 0, 0) != 0;
    if (input.header.dwType == RIM_TYPEMOUSE)
    {
        if (!cameraEnabled)
        {
            return false;
        }
        const RAWMOUSE& mouse = input.data.mouse;
        const bool relativeMovement =
            (mouse.usFlags & MOUSE_MOVE_ABSOLUTE) == 0;
        if (relativeMovement)
        {
            InterlockedExchangeAdd(&g_mouseDeltaX, mouse.lLastX);
            InterlockedExchangeAdd(&g_mouseDeltaY, mouse.lLastY);
            InterlockedExchange64(
                reinterpret_cast<volatile LONG64*>(&g_lastRawMouseTick),
                static_cast<LONG64>(GetTickCount64()));
            InterlockedExchange(&g_legacyMouseValid, 0);
        }
        // SetCursorPos can emit an absolute raw-mouse packet. Consume it so UE
        // cannot rotate the gameplay camera, but do not treat it as usable
        // relative input or suppress the legacy fallback.
        return true;
    }
    if (input.header.dwType == RIM_TYPEKEYBOARD)
    {
        const RAWKEYBOARD& keyboard = input.data.keyboard;
        if (keyboard.VKey == 0xFF)
        {
            return false;
        }
        const bool keyUp = (keyboard.Flags & RI_KEY_BREAK) != 0;
        return captureKeyboardEvent(
            static_cast<int>(keyboard.VKey), !keyUp, keyUp);
    }
    return false;
}

void captureWindowMouseMove(const HWND window, const LPARAM longParameter)
{
    RECT client{};
    if (!GetClientRect(window, &client) || client.right <= client.left ||
        client.bottom <= client.top)
    {
        return;
    }

    const LONG x = static_cast<short>(LOWORD(longParameter));
    const LONG y = static_cast<short>(HIWORD(longParameter));
    POINT center{
        client.left + (client.right - client.left) / 2,
        client.top + (client.bottom - client.top) / 2};

    // Ignore the synthetic centre event generated by the edge recenter. It
    // establishes a fresh legacy baseline but must not become a large camera
    // delta from the edge back to the centre.
    const bool isRecenterEvent =
        InterlockedCompareExchange(&g_windowRecenterPending, 0, 0) != 0 &&
        x >= center.x - 1 && x <= center.x + 1 &&
        y >= center.y - 1 && y <= center.y + 1;
    if (isRecenterEvent)
    {
        InterlockedExchange(&g_windowRecenterPending, 0);
        InterlockedExchange(&g_legacyMouseX, x);
        InterlockedExchange(&g_legacyMouseY, y);
        InterlockedExchange(&g_legacyMouseValid, 1);
        return;
    }

    const LONG previousX = InterlockedExchange(&g_legacyMouseX, x);
    const LONG previousY = InterlockedExchange(&g_legacyMouseY, y);
    const bool previousPositionValid =
        InterlockedExchange(&g_legacyMouseValid, 1) != 0;
    const ULONGLONG lastRaw = static_cast<ULONGLONG>(InterlockedCompareExchange64(
        reinterpret_cast<volatile LONG64*>(&g_lastRawMouseTick), 0, 0));
    if (previousPositionValid &&
        GetTickCount64() - lastRaw >= kRawMouseFallbackDelayMilliseconds)
    {
        InterlockedExchangeAdd(&g_mouseDeltaX, x - previousX);
        InterlockedExchangeAdd(&g_mouseDeltaY, y - previousY);
    }

    const bool nearEdge =
        x <= client.left + kWindowMouseEdgeInset ||
        x >= client.right - kWindowMouseEdgeInset ||
        y <= client.top + kWindowMouseEdgeInset ||
        y >= client.bottom - kWindowMouseEdgeInset;
    if (!nearEdge ||
        InterlockedCompareExchange(&g_windowRecenterPending, 1, 0) != 0)
    {
        return;
    }

    POINT screenCenter = center;
    if (!ClientToScreen(window, &screenCenter) ||
        !SetCursorPos(screenCenter.x, screenCenter.y))
    {
        InterlockedExchange(&g_windowRecenterPending, 0);
    }
}

bool isMouseMessage(const UINT message)
{
    switch (message)
    {
    case WM_MOUSEMOVE:
    case WM_LBUTTONDOWN:
    case WM_LBUTTONUP:
    case WM_LBUTTONDBLCLK:
    case WM_RBUTTONDOWN:
    case WM_RBUTTONUP:
    case WM_RBUTTONDBLCLK:
    case WM_MBUTTONDOWN:
    case WM_MBUTTONUP:
    case WM_MBUTTONDBLCLK:
    case WM_XBUTTONDOWN:
    case WM_XBUTTONUP:
    case WM_XBUTTONDBLCLK:
    case WM_MOUSEWHEEL:
    case WM_MOUSEHWHEEL:
        return true;
    default:
        return false;
    }
}

LRESULT CALLBACK gameWindowProc(
    const HWND window,
    const UINT message,
    const WPARAM wordParameter,
    const LPARAM longParameter)
{
    if (message == WM_NCDESTROY)
    {
        const WNDPROC original = g_originalWindowProc;
        InterlockedExchange(&g_windowCaptureReady, 0);
        clearInputState();
        if (original != nullptr)
        {
            SetWindowLongPtrW(
                window, GWLP_WNDPROC, reinterpret_cast<LONG_PTR>(original));
        }
        g_gameWindow = nullptr;
        g_originalWindowProc = nullptr;
        return original != nullptr
            ? CallWindowProcW(original, window, message, wordParameter, longParameter)
            : DefWindowProcW(window, message, wordParameter, longParameter);
    }

    if (!foregroundBelongsToGame())
    {
        clearInputState();
        return callOriginalWindowProc(window, message, wordParameter, longParameter);
    }

    if (message == WM_INPUT)
    {
        RAWINPUT input{};
        if (readRawInput(longParameter, input) && captureRawInput(input))
        {
            // WM_INPUT with RIM_INPUT must reach DefWindowProc for system
            // cleanup. Calling it directly consumes the packet without
            // forwarding the raw keyboard/mouse event into UE.
            return DefWindowProcW(window, message, wordParameter, longParameter);
        }
    }
    else if (message == WM_KEYDOWN || message == WM_SYSKEYDOWN ||
        message == WM_KEYUP || message == WM_SYSKEYUP)
    {
        if (captureKeyboardEvent(
                static_cast<int>(wordParameter),
                message == WM_KEYDOWN || message == WM_SYSKEYDOWN,
                message == WM_KEYUP || message == WM_SYSKEYUP))
        {
            return 0;
        }
    }
    else if (isMouseMessage(message) &&
        InterlockedCompareExchange(&g_bmwCameraEnabled, 0, 0) != 0)
    {
        if (message == WM_MOUSEMOVE)
        {
            captureWindowMouseMove(window, longParameter);
        }
        return 0;
    }
    return callOriginalWindowProc(window, message, wordParameter, longParameter);
}

LRESULT CALLBACK windowThreadActionProc(
    const int code,
    const WPARAM wordParameter,
    const LPARAM longParameter)
{
    if (code >= 0 && longParameter != 0)
    {
        const auto* event = reinterpret_cast<const CWPSTRUCT*>(longParameter);
        if (event->hwnd == g_gameWindow &&
            event->message == g_installWindowCaptureMessage)
        {
            SetLastError(ERROR_SUCCESS);
            const LONG_PTR original = SetWindowLongPtrW(
                event->hwnd,
                GWLP_WNDPROC,
                reinterpret_cast<LONG_PTR>(&gameWindowProc));
            if (original != 0 || GetLastError() == ERROR_SUCCESS)
            {
                g_originalWindowProc = reinterpret_cast<WNDPROC>(original);
                MemoryBarrier();
                InterlockedExchange(&g_windowCaptureReady, 1);
                InterlockedExchange(&g_windowActionSucceeded, 1);
            }
        }
        else if (event->hwnd == g_gameWindow &&
            event->message == g_restoreWindowCaptureMessage)
        {
            const WNDPROC original = g_originalWindowProc;
            const LONG_PTR current = GetWindowLongPtrW(event->hwnd, GWLP_WNDPROC);
            bool restored = true;
            if (current == reinterpret_cast<LONG_PTR>(&gameWindowProc) &&
                original != nullptr)
            {
                SetLastError(ERROR_SUCCESS);
                restored = SetWindowLongPtrW(
                    event->hwnd,
                    GWLP_WNDPROC,
                    reinterpret_cast<LONG_PTR>(original)) != 0 ||
                    GetLastError() == ERROR_SUCCESS;
            }
            if (restored)
            {
                InterlockedExchange(&g_windowCaptureReady, 0);
                clearInputState();
                g_originalWindowProc = nullptr;
                InterlockedExchange(&g_windowActionSucceeded, 1);
            }
        }
    }
    return CallNextHookEx(nullptr, code, wordParameter, longParameter);
}

bool runWindowThreadAction(const HWND window, const UINT message)
{
    DWORD processId = 0;
    const DWORD threadId = GetWindowThreadProcessId(window, &processId);
    if (threadId == 0 || processId != GetCurrentProcessId())
    {
        return false;
    }
    HMODULE module = nullptr;
    if (!GetModuleHandleExW(
            GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
                GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
            reinterpret_cast<LPCWSTR>(&windowThreadActionProc),
            &module))
    {
        return false;
    }
    const HHOOK hook = SetWindowsHookExW(
        WH_CALLWNDPROC, windowThreadActionProc, module, threadId);
    if (hook == nullptr)
    {
        return false;
    }
    InterlockedExchange(&g_windowActionSucceeded, 0);
    DWORD_PTR ignored = 0;
    const LRESULT sent = SendMessageTimeoutW(
        window,
        message,
        0,
        0,
        SMTO_ABORTIFHUNG | SMTO_BLOCK,
        kInputStartTimeoutMilliseconds,
        &ignored);
    UnhookWindowsHookEx(hook);
    return sent != 0 &&
        InterlockedCompareExchange(&g_windowActionSucceeded, 0, 0) != 0;
}

bool installGameWindowCapture()
{
    const HWND window = findGameWindow();
    if (window == nullptr)
    {
        InterlockedExchange(&g_inputCaptureDiagnostic, 101);
        return false;
    }
    g_installWindowCaptureMessage = RegisterWindowMessageW(
        L"GameCameraCaptureLab.InstallWindowInput.v1");
    g_restoreWindowCaptureMessage = RegisterWindowMessageW(
        L"GameCameraCaptureLab.RestoreWindowInput.v1");
    if (g_installWindowCaptureMessage == 0 ||
        g_restoreWindowCaptureMessage == 0)
    {
        InterlockedExchange(&g_inputCaptureDiagnostic, 102);
        return false;
    }
    g_gameWindow = window;
    if (runWindowThreadAction(window, g_installWindowCaptureMessage))
    {
        InterlockedExchange(&g_inputCaptureDiagnostic, 1);
        return InterlockedCompareExchange(&g_windowCaptureReady, 0, 0) != 0;
    }

    // Same-process SetWindowLongPtr is permitted by Win32. Keep it only as a
    // fallback for games whose window thread does not dispatch the one-shot
    // WH_CALLWNDPROC action during startup. Installation happens once after a
    // stable visible HWND is present; WM_NCDESTROY restores on its owner
    // thread, and normal process exit does not unload this code in-place.
    SetLastError(ERROR_SUCCESS);
    const LONG_PTR original = SetWindowLongPtrW(
        window, GWLP_WNDPROC, reinterpret_cast<LONG_PTR>(&gameWindowProc));
    if (original == 0 && GetLastError() != ERROR_SUCCESS)
    {
        g_gameWindow = nullptr;
        g_originalWindowProc = nullptr;
        InterlockedExchange(&g_inputCaptureDiagnostic, 103);
        return false;
    }
    g_originalWindowProc = reinterpret_cast<WNDPROC>(original);
    MemoryBarrier();
    InterlockedExchange(&g_windowCaptureReady, 1);
    InterlockedExchange(&g_inputCaptureDiagnostic, 2);
    return true;
}

void restoreGameWindowCapture()
{
    const HWND window = g_gameWindow;
    if (window != nullptr && IsWindow(window) &&
        g_restoreWindowCaptureMessage != 0)
    {
        runWindowThreadAction(window, g_restoreWindowCaptureMessage);
    }
    InterlockedExchange(&g_windowCaptureReady, 0);
    g_gameWindow = nullptr;
    g_originalWindowProc = nullptr;
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
    if (InterlockedCompareExchange(&g_windowCaptureReady, 0, 0) != 0)
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

    return captureKeyboardEvent(key, keyDownMessage, keyUpMessage)
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
    if (InterlockedCompareExchange(&g_windowCaptureReady, 0, 0) != 0)
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
    }
    // Returning a non-zero value suppresses this cursor event, so the cursor
    // remains at the centre without another SetCursorPos call. Recentring on
    // every hardware event created a synthetic-event feedback loop in games
    // that also consume high-frequency raw mouse input.
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
    const bool windowCaptureReady = installGameWindowCapture();
    if (!windowCaptureReady)
    {
        g_keyboardHook = SetWindowsHookExW(
            WH_KEYBOARD_LL, lowLevelKeyboardProc, module, 0);
        g_mouseHook = SetWindowsHookExW(
            WH_MOUSE_LL, lowLevelMouseProc, module, 0);
    }
    const bool hooksReady = windowCaptureReady ||
        (g_keyboardHook != nullptr && g_mouseHook != nullptr);
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

    restoreGameWindowCapture();
    if (g_mouseHook != nullptr)
    {
        UnhookWindowsHookEx(g_mouseHook);
        g_mouseHook = nullptr;
    }
    if (g_keyboardHook != nullptr)
    {
        UnhookWindowsHookEx(g_keyboardHook);
        g_keyboardHook = nullptr;
    }
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
    if (!inputCaptureReady())
    {
        return false;
    }
    if (!inputGameHasFocus())
    {
        // A focus transition does not always generate matching key-up events.
        // Clear held keys here so camera controls cannot remain latched when the user
        // returns to the game window.
        clearInputState();
        return false;
    }
    return InterlockedCompareExchange(&g_bmwCameraEnabled, 0, 0) != 0;
}

bool inputUsesWindowCapture()
{
    return InterlockedCompareExchange(&g_windowCaptureReady, 0, 0) != 0;
}

std::uint32_t inputCaptureDiagnostic()
{
    return static_cast<std::uint32_t>(InterlockedCompareExchange(
        &g_inputCaptureDiagnostic, 0, 0));
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
