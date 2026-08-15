option casemap:none

EXTERN g_bmwOverrideViews:BYTE
EXTERN g_bmwActivePoseIndex:DWORD
EXTERN g_bmwCameraEnabled:DWORD
EXTERN g_bmwObservedView:BYTE
EXTERN g_bmwObservedLock:DWORD
EXTERN g_bmwObservedSequence:DWORD
EXTERN g_bmwLastCameraSource:QWORD
EXTERN g_bmwLastCameraDestination:QWORD
EXTERN g_bmwHook1Return:QWORD
EXTERN g_bmwHook2Return:QWORD
EXTERN g_bmwHook3Return:QWORD
EXTERN g_bmwHudHidden:DWORD
EXTERN g_bmwHudHookReturn:QWORD

.code

; The two hook sites have the same register contract:
;   RCX -> destination FMinimalViewInfo
;   RDX -> source FMinimalViewInfo
; The original 0x25-byte sequence copies LWC location, FRotator and FOV.  We
; perform that copy here and then skip the original sequence.  No C++ call is
; made from the mid-function hook, so stack alignment and volatile register
; preservation are not delegated to an external detour library.

BMW_CAMERA_COPY MACRO returnTarget
    LOCAL selected_pose, observation_done
    mov qword ptr [g_bmwLastCameraSource], rdx
    mov qword ptr [g_bmwLastCameraDestination], rcx
    push rdx

    cmp dword ptr [g_bmwCameraEnabled], 0
    je selected_pose
    mov eax, dword ptr [g_bmwActivePoseIndex]
    shl rax, 6
    lea rdx, g_bmwOverrideViews
    add rdx, rax

selected_pose:
    movups xmm0, xmmword ptr [rdx]
    movups xmmword ptr [rcx], xmm0
    movsd xmm1, qword ptr [rdx+10h]
    movsd qword ptr [rcx+10h], xmm1
    movups xmm0, xmmword ptr [rdx+18h]
    movups xmmword ptr [rcx+18h], xmm0
    movsd xmm1, qword ptr [rdx+28h]
    movsd qword ptr [rcx+28h], xmm1
    mov eax, dword ptr [rdx+30h]
    mov dword ptr [rcx+30h], eax

    ; Publish the exact view that was sent to the renderer. If two view paths
    ; run concurrently, the second path skips only this diagnostic copy; it
    ; still receives the requested camera pose above.
    mov eax, 1
    xchg eax, dword ptr [g_bmwObservedLock]
    test eax, eax
    jne observation_done
    movups xmm0, xmmword ptr [rdx]
    movups xmmword ptr [g_bmwObservedView], xmm0
    movsd xmm1, qword ptr [rdx+10h]
    movsd qword ptr [g_bmwObservedView+10h], xmm1
    movups xmm0, xmmword ptr [rdx+18h]
    movups xmmword ptr [g_bmwObservedView+18h], xmm0
    movsd xmm1, qword ptr [rdx+28h]
    movsd qword ptr [g_bmwObservedView+28h], xmm1
    mov eax, dword ptr [rdx+30h]
    mov dword ptr [g_bmwObservedView+30h], eax
    inc dword ptr [g_bmwObservedSequence]
    mov dword ptr [g_bmwObservedLock], 0

observation_done:
    ; Match the original sequence's final EAX value before restoring RDX.
    mov eax, dword ptr [rdx+30h]
    pop rdx
    jmp qword ptr [returnTarget]
ENDM

PUBLIC BmwCameraHook1
BmwCameraHook1 PROC
    BMW_CAMERA_COPY g_bmwHook1Return
BmwCameraHook1 ENDP

PUBLIC BmwCameraHook2
BmwCameraHook2 PROC
    BMW_CAMERA_COPY g_bmwHook2Return
BmwCameraHook2 ENDP

PUBLIC BmwCameraHook3
BmwCameraHook3 PROC
    BMW_CAMERA_COPY g_bmwHook3Return
BmwCameraHook3 ENDP

; Black Myth multiplies the HUD draw color/opacity by the vector loaded from
; [rbx+270h]. Keep the detour installed and switch visibility atomically, so
; Delete/UI toggles never rewrite executable bytes while render threads are live.
PUBLIC BmwHudHook
BmwHudHook PROC
    pushfq
    cmp dword ptr [g_bmwHudHidden], 0
    jne hud_hidden
    popfq
    movups xmm0, xmmword ptr [rbx+270h]
    jmp hud_selected

hud_hidden:
    popfq
    xorps xmm0, xmm0

hud_selected:
    movups xmmword ptr [rbp-74h], xmm1
    mulps xmm6, xmm0
    jmp qword ptr [g_bmwHudHookReturn]
BmwHudHook ENDP

END
