$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    throw "Python venv not found: $Python"
}

Push-Location $ProjectRoot
try {
    & $Python -m re9_pose_recorder.cli scan-stills-gui `
        --obs-password "123456" `
        --layers-config "configs\still_scan_layers.yaml" `
        --points-x 5 `
        --points-z 6 `
        --settle-seconds 0.6 `
        --image-format jpg `
        --image-width 1920 `
        --image-height 1080 `
        --image-quality 100
}
finally {
    Pop-Location
}

