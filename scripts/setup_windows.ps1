param(
  [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

& $Python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt

if (-not (Test-Path "core\configs\windows.local.yaml")) {
  Copy-Item "core\configs\windows.yaml" "core\configs\windows.local.yaml"
  Write-Host "Created core\configs\windows.local.yaml. Edit its game and OBS paths before capture."
}

Write-Host "Python environment is ready. Activate it with:"
Write-Host ".\.venv\Scripts\activate"
