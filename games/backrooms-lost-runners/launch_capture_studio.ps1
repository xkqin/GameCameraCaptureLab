param([string]$TrajectoryFile = "")

$ErrorActionPreference = "Stop"
$adapterDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$gamesDir = Split-Path -Parent $adapterDir
$sharedStudioDir = Join-Path $gamesDir "black-myth-wukong"
$sharedLauncher = Join-Path $sharedStudioDir "launch_bmw_capture_studio.ps1"

if (-not (Test-Path -LiteralPath $sharedLauncher)) {
    throw "Shared UE capture studio was not found: $sharedLauncher"
}

$env:GAME_CAMERA_GAME_ID = "backrooms-lost-runners"
$env:GAME_CAMERA_GAME_NAME = "Backrooms Lost Runners"
$env:GAME_CAMERA_GAME_SHORT_NAME = "Backrooms"
$env:GAME_CAMERA_PROCESS_NAMES = "BackroomsLostRunners-Win64-Shipping.exe"
$env:GAME_CAMERA_WINDOW_PATTERNS = "Backrooms Lost Runners,BackroomsLostRunners"
$env:GAME_CAMERA_HUD_REQUIRED = "0"
$env:GAME_CAMERA_ADAPTER_ROOT = $adapterDir
$env:UE_CAMERA_NATIVE_DIR = Join-Path $sharedStudioDir "native"

& $sharedLauncher -TrajectoryFile $TrajectoryFile
