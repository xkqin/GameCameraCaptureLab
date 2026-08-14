param([string]$Configuration = "Release")

$ErrorActionPreference = "Stop"
$nativeRoot = $PSScriptRoot
$buildRoot = Join-Path $nativeRoot "build_smooth_v4"
$generator = "Visual Studio 17 2022"

cmake --fresh -S $nativeRoot -B $buildRoot -G $generator -A x64
if ($LASTEXITCODE -ne 0) { throw "CMake configure failed" }
cmake --build $buildRoot --config $Configuration --target IgcsConnectorBridge
if ($LASTEXITCODE -ne 0) { throw "Bridge build failed" }

$output = Join-Path $buildRoot "$Configuration\IgcsConnector.addon64"
if (-not (Test-Path -LiteralPath $output)) {
    throw "Bridge output not found: $output"
}
Write-Output $output
