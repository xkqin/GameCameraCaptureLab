param([string]$TrajectoryFile = "")

$ErrorActionPreference = "Stop"

$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvDir = Join-Path $projectDir ".venv"
$pythonExe = Join-Path $venvDir "Scripts\python.exe"
$pythonwExe = Join-Path $venvDir "Scripts\pythonw.exe"

if (-not $env:GAME_CAMERA_GAME_ID) {
    $env:GAME_CAMERA_GAME_ID = "black-myth-wukong"
    $env:GAME_CAMERA_GAME_NAME = "Black Myth: Wukong"
    $env:GAME_CAMERA_GAME_SHORT_NAME = "Black Myth"
    $env:GAME_CAMERA_PROCESS_NAMES = "b1-Win64-Shipping.exe,BlackMythWukong.exe"
    $env:GAME_CAMERA_WINDOW_PATTERNS = "Black Myth,Wukong,b1-Win64"
    $env:GAME_CAMERA_HUD_REQUIRED = "1"
}
if (-not $env:GAME_CAMERA_ADAPTER_ROOT) {
    $env:GAME_CAMERA_ADAPTER_ROOT = $projectDir
}
if (-not $env:UE_CAMERA_NATIVE_DIR) {
    $env:UE_CAMERA_NATIVE_DIR = Join-Path $projectDir "native"
}

if (-not (Test-Path -LiteralPath $pythonExe)) {
    Write-Host "First launch: creating the Python environment..."
    if (Get-Command py.exe -ErrorAction SilentlyContinue) {
        & py.exe -3.11 -m venv $venvDir
    }
    elseif (Get-Command python.exe -ErrorAction SilentlyContinue) {
        & python.exe -m venv $venvDir
    }
    else {
        throw "Python 3.10 or newer was not found."
    }
    & $pythonExe -m pip install --disable-pip-version-check -e $projectDir
}

if (-not (Test-Path -LiteralPath $pythonwExe)) {
    throw "Python GUI executable was not found: $pythonwExe"
}

& $pythonExe -c "import obsws_python; import PIL; import yaml; import requests" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Updating capture-studio dependencies..."
    & $pythonExe -m pip install --disable-pip-version-check -e $projectDir
    if ($LASTEXITCODE -ne 0) {
        throw "Could not install capture-studio dependencies."
    }
}

$arguments = @("-m", "bmw_capture_studio")
if ($TrajectoryFile) {
    $arguments += @("--trajectory-file", ('"{0}"' -f $TrajectoryFile.Replace('"', '\"')))
}
Start-Process -FilePath $pythonwExe -ArgumentList $arguments -WorkingDirectory $projectDir
