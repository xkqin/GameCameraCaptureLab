cmake_minimum_required(VERSION 3.24)

project(re9_depth_bridge VERSION 0.1.0 LANGUAGES CXX)

if(NOT WIN32)
    message(FATAL_ERROR "re9_depth_bridge is a Windows D3D12 REFramework plugin.")
endif()

set(REFRAMEWORK_SDK_DIR "" CACHE PATH "Path to a REFramework source checkout")
if(NOT EXISTS "${REFRAMEWORK_SDK_DIR}/include/reframework/API.hpp")
    message(FATAL_ERROR "Set REFRAMEWORK_SDK_DIR to a REFramework source checkout containing include/reframework/API.hpp")
endif()

set(NLOHMANN_JSON_INCLUDE_DIR "" CACHE PATH "Directory containing nlohmann/json.hpp")
if(NOT EXISTS "${NLOHMANN_JSON_INCLUDE_DIR}/nlohmann/json.hpp")
    message(FATAL_ERROR "Set NLOHMANN_JSON_INCLUDE_DIR to a directory containing nlohmann/json.hpp")
endif()

add_library(re9_depth_bridge SHARED src/re9_depth_bridge.cpp)
target_compile_features(re9_depth_bridge PRIVATE cxx_std_23)
target_compile_definitions(re9_depth_bridge PRIVATE WIN32_LEAN_AND_MEAN NOMINMAX)
target_include_directories(
    re9_depth_bridge
    PRIVATE
        "${REFRAMEWORK_SDK_DIR}/include"
        "${NLOHMANN_JSON_INCLUDE_DIR}"
)
target_link_libraries(re9_depth_bridge PRIVATE d3d12 dxgi)

if(MSVC)
    target_compile_options(re9_depth_bridge PRIVATE /W4 /EHsc /permissive-)
endif()

set_target_properties(
    re9_depth_bridge
    PROPERTIES
        OUTPUT_NAME "re9_depth_bridge"
        RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin"
        RUNTIME_OUTPUT_DIRECTORY_RELEASE "${CMAKE_BINARY_DIR}/bin"
        RUNTIME_OUTPUT_DIRECTORY_RELWITHDEBINFO "${CMAKE_BINARY_DIR}/bin"
)
