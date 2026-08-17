[CmdletBinding()]
param(
    [string]$REFrameworkSource = "",
    [string]$BuildDirectory = "",
    [ValidateSet("Release", "RelWithDebInfo", "Debug")]
    [string]$Configuration = "Release"
)

$ErrorActionPreference = "Stop"
$ProjectDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $BuildDirectory) {
    $BuildDirectory = Join-Path $ProjectDirectory "build"
}
if (-not $REFrameworkSource) {
    $REFrameworkSource = Join-Path $ProjectDirectory "third_party\REFramework"
}
$JsonIncludeDirectory = Join-Path $ProjectDirectory "third_party\nlohmann"
$JsonHeader = Join-Path $JsonIncludeDirectory "nlohmann\json.hpp"

if (-not (Test-Path -LiteralPath (Join-Path $REFrameworkSource "include\reframework\API.hpp"))) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $REFrameworkSource) | Out-Null
    git clone --depth 1 https://github.com/praydog/REFramework.git $REFrameworkSource
}
if (-not (Test-Path -LiteralPath $JsonHeader)) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $JsonHeader) | Out-Null
    $JsonUrl = "https://raw.githubusercontent.com/nlohmann/json/v3.11.3/single_include/nlohmann/json.hpp"
    & curl.exe -L --fail --retry 3 --max-time 180 -o $JsonHeader $JsonUrl
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $JsonHeader)) {
        throw "Failed to download the pinned nlohmann/json v3.11.3 header."
    }
}

cmake -S $ProjectDirectory -B $BuildDirectory -A x64 `
    "-DREFRAMEWORK_SDK_DIR=$REFrameworkSource" `
    "-DNLOHMANN_JSON_INCLUDE_DIR=$JsonIncludeDirectory"
cmake --build $BuildDirectory --config $Configuration --target re9_depth_bridge

$DllPath = Join-Path $BuildDirectory "bin\re9_depth_bridge.dll"
if (-not (Test-Path -LiteralPath $DllPath)) {
    throw "Build completed without the expected DLL: $DllPath"
}
Write-Host "Built: $DllPath"
