param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingArgs
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = if ($env:PYTHON_BIN) {
    $env:PYTHON_BIN
}
else {
    Join-Path $ProjectRoot ".venv\Scripts\python.exe"
}

if (-not (Test-Path $Python)) {
    throw "Python venv not found: $Python. Run .\scripts\setup_windows.ps1 first."
}

$ConfigPath = $env:RE9_CONFIG
if (-not $ConfigPath) {
    if (Test-Path (Join-Path $ProjectRoot "configs\windows.local.yaml")) {
        $ConfigPath = "configs\windows.local.yaml"
    }
    elseif (Test-Path (Join-Path $ProjectRoot "configs\windows.yaml")) {
        $ConfigPath = "configs\windows.yaml"
    }
    else {
        $ConfigPath = "configs\default.yaml"
    }
}

$ObsPassword = if ($null -ne $env:OBS_PASSWORD) { $env:OBS_PASSWORD } else { "123456" }
$SessionId = if ($env:SESSION_ID) { $env:SESSION_ID } else { "scene_windows" }
$SettleSeconds = if ($env:SETTLE_SECONDS) { $env:SETTLE_SECONDS } else { "0.6" }
$ImageFormat = if ($env:IMAGE_FORMAT) { $env:IMAGE_FORMAT } else { "jpg" }
$ImageWidth = if ($env:IMAGE_WIDTH) { $env:IMAGE_WIDTH } else { "1920" }
$ImageHeight = if ($env:IMAGE_HEIGHT) { $env:IMAGE_HEIGHT } else { "1080" }
$ImageQuality = if ($env:IMAGE_QUALITY) { $env:IMAGE_QUALITY } else { "100" }

$CliArgs = @(
    "-m", "re9_pose_recorder.cli", "scan-stills-gui",
    "--config", $ConfigPath,
    "--obs-password", $ObsPassword,
    "--session-id", $SessionId,
    "--settle-seconds", $SettleSeconds,
    "--image-format", $ImageFormat,
    "--image-width", $ImageWidth,
    "--image-height", $ImageHeight,
    "--image-quality", $ImageQuality
)

if ($env:POSE_PLAN_CONFIG) {
    $CliArgs += @("--pose-plan-config", $env:POSE_PLAN_CONFIG)
}
elseif ($env:LAYERS_CONFIG) {
    $CliArgs += @("--layers-config", $env:LAYERS_CONFIG)
}
else {
    $CliArgs += @("--layers-config", "configs\scene01_scan_layers.yaml")
}

$OptionalSettings = @(
    @("TRAJECTORY_SET", "--trajectory-set"),
    @("TRAJECTORY_JSON", "--trajectory-json"),
    @("TRAJECTORY_OUTPUT_DIR", "--trajectory-output-dir"),
    @("TRAJECTORY_LABEL", "--trajectory-label"),
    @("TRAJECTORY_SESSION_PREFIX", "--trajectory-session-prefix")
)
foreach ($Setting in $OptionalSettings) {
    $Value = [Environment]::GetEnvironmentVariable($Setting[0])
    if ($Value) {
        $CliArgs += @($Setting[1], $Value)
    }
}

$PreviousPythonPath = $env:PYTHONPATH
$SourceRoot = Join-Path $ProjectRoot "src"
$env:PYTHONPATH = if ($PreviousPythonPath) {
    "$SourceRoot;$PreviousPythonPath"
}
else {
    $SourceRoot
}

$ExitCode = 0
Push-Location $ProjectRoot
try {
    & $Python @CliArgs @RemainingArgs
    $ExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
    $env:PYTHONPATH = $PreviousPythonPath
}
if ($ExitCode -ne 0) {
    throw "RE9 capture GUI exited with code $ExitCode."
}
