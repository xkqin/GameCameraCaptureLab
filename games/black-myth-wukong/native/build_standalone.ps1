param([string]$Configuration = "Release")

$ErrorActionPreference = "Stop"
$nativeRoot = $PSScriptRoot
$buildRoot = Join-Path $nativeRoot "build_standalone_v1"
$generator = "Visual Studio 17 2022"

cmake --fresh -S $nativeRoot -B $buildRoot -G $generator -A x64
if ($LASTEXITCODE -ne 0) { throw "CMake configure failed" }
cmake --build $buildRoot --config $Configuration --target BmwCameraBridge BmwCameraInjector UeCameraRuntime UeCameraInjector
if ($LASTEXITCODE -ne 0) { throw "Standalone camera runtime build failed" }

$output = Join-Path $buildRoot "$Configuration\BmwCameraBridge.dll"
if (-not (Test-Path -LiteralPath $output)) {
    throw "Standalone bridge output not found: $output"
}
$injector = Join-Path $buildRoot "$Configuration\BmwCameraInjector.exe"
if (-not (Test-Path -LiteralPath $injector)) {
    throw "Standalone bridge injector output not found: $injector"
}
$runtime = Join-Path $buildRoot "$Configuration\UeCameraRuntime.dll"
if (-not (Test-Path -LiteralPath $runtime)) {
    throw "UE Camera Runtime output not found: $runtime"
}
$runtimeInjector = Join-Path $buildRoot "$Configuration\UeCameraInjector.exe"
if (-not (Test-Path -LiteralPath $runtimeInjector)) {
    throw "UE Camera Runtime injector output not found: $runtimeInjector"
}
Write-Output $output
Write-Output $injector
Write-Output $runtime
Write-Output $runtimeInjector
