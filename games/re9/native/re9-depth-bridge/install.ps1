[CmdletBinding()]
param(
    [string]$GameDirectory = "D:\steam\steamapps\common\RESIDENT EVIL requiem BIOHAZARD requiem",
    [string]$DllPath = ""
)

$ErrorActionPreference = "Stop"
$ProjectDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $DllPath) {
    $DllPath = Join-Path $ProjectDirectory "build\bin\re9_depth_bridge.dll"
}
if (-not (Test-Path -LiteralPath $DllPath)) {
    throw "Depth plugin DLL was not found: $DllPath. Run build.ps1 first."
}

$PluginDirectory = Join-Path $GameDirectory "reframework\plugins"
if (-not (Test-Path -LiteralPath (Join-Path $GameDirectory "reframework"))) {
    throw "REFramework directory was not found under: $GameDirectory"
}
New-Item -ItemType Directory -Force -Path $PluginDirectory | Out-Null
$Destination = Join-Path $PluginDirectory "re9_depth_bridge.dll"
Copy-Item -LiteralPath $DllPath -Destination $Destination -Force

$SourceHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $DllPath).Hash
$DestinationHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Destination).Hash
if ($SourceHash -ne $DestinationHash) {
    throw "Installed DLL hash does not match the build output."
}
Write-Host "Installed: $Destination"
Write-Host "SHA256: $DestinationHash"
