cmake_minimum_required(VERSION 3.20)
project(BmwUuuPoseBridge LANGUAGES CXX)

add_library(IgcsConnectorBridge SHARED BmwUuuPoseBridge.cpp)
target_compile_features(IgcsConnectorBridge PRIVATE cxx_std_17)
target_compile_definitions(IgcsConnectorBridge PRIVATE WIN32_LEAN_AND_MEAN NOMINMAX)
target_link_libraries(IgcsConnectorBridge PRIVATE ws2_32)
set_target_properties(
    IgcsConnectorBridge
    PROPERTIES
        OUTPUT_NAME "IgcsConnector"
        PREFIX ""
        SUFFIX ".addon64"
)
