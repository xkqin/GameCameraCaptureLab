cmake_minimum_required(VERSION 3.20)
project(GameCameraRuntime LANGUAGES CXX)

if(MSVC)
    enable_language(ASM_MASM)
    set(UE_CAMERA_RUNTIME_NATIVE_DIR
        "${CMAKE_CURRENT_SOURCE_DIR}/../../../core/runtime/ue-camera-runtime/native")
    set(CAMERA_BRIDGE_SOURCES
        standalone/BmwCameraBridge.cpp
        standalone/BmwCameraInput.cpp
        standalone/BmwCameraMath.cpp
        standalone/BmwNativeDepth.cpp
        standalone/BmwCameraRelay.cpp
        standalone/BmwCameraTrajectory.cpp
        standalone/BmwCameraHooks.asm
        "${UE_CAMERA_RUNTIME_NATIVE_DIR}/UeCameraProfile.cpp")

    add_library(
        BmwCameraBridge
        SHARED
        ${CAMERA_BRIDGE_SOURCES}
    )
    target_compile_features(BmwCameraBridge PRIVATE cxx_std_17)
    target_compile_definitions(BmwCameraBridge PRIVATE WIN32_LEAN_AND_MEAN NOMINMAX)
    target_include_directories(
        BmwCameraBridge PRIVATE standalone "${UE_CAMERA_RUNTIME_NATIVE_DIR}"
    )
    target_link_libraries(BmwCameraBridge PRIVATE ws2_32 d3d12 dxgi)
    set_property(
        TARGET BmwCameraBridge
        PROPERTY MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>"
    )
    set_target_properties(
        BmwCameraBridge
        PROPERTIES
            OUTPUT_NAME "BmwCameraBridge"
            PREFIX ""
            SUFFIX ".dll"
    )

    add_library(
        UeCameraRuntime
        SHARED
        ${CAMERA_BRIDGE_SOURCES}
    )
    target_compile_features(UeCameraRuntime PRIVATE cxx_std_17)
    target_compile_definitions(UeCameraRuntime PRIVATE WIN32_LEAN_AND_MEAN NOMINMAX)
    target_include_directories(
        UeCameraRuntime PRIVATE standalone "${UE_CAMERA_RUNTIME_NATIVE_DIR}"
    )
    target_link_libraries(UeCameraRuntime PRIVATE ws2_32 d3d12 dxgi)
    set_property(
        TARGET UeCameraRuntime
        PROPERTY MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>"
    )
    set_target_properties(
        UeCameraRuntime
        PROPERTIES
            OUTPUT_NAME "UeCameraRuntime"
            PREFIX ""
            SUFFIX ".dll"
    )

    add_executable(
        BmwCameraInjector
        standalone/BmwCameraInjector.cpp
    )
    target_compile_features(BmwCameraInjector PRIVATE cxx_std_17)
    target_compile_definitions(BmwCameraInjector PRIVATE WIN32_LEAN_AND_MEAN NOMINMAX)
    set_property(
        TARGET BmwCameraInjector
        PROPERTY MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>"
    )
    set_target_properties(
        BmwCameraInjector
        PROPERTIES
            OUTPUT_NAME "BmwCameraInjector"
    )

    add_executable(
        UeCameraInjector
        "${UE_CAMERA_RUNTIME_NATIVE_DIR}/UeCameraInjector.cpp"
        "${UE_CAMERA_RUNTIME_NATIVE_DIR}/UeCameraProfile.cpp"
    )
    target_compile_features(UeCameraInjector PRIVATE cxx_std_17)
    target_compile_definitions(UeCameraInjector PRIVATE WIN32_LEAN_AND_MEAN NOMINMAX)
    set_property(
        TARGET UeCameraInjector
        PROPERTY MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>"
    )
    set_target_properties(
        UeCameraInjector
        PROPERTIES OUTPUT_NAME "UeCameraInjector"
    )
endif()
