param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingArgs
)

$PreviousPosePlan = $env:POSE_PLAN_CONFIG
$PreviousLayersConfig = $env:LAYERS_CONFIG
$PreviousSession = $env:SESSION_ID
$env:POSE_PLAN_CONFIG = "data\reconstruction_capture_plans\scene_3.2\scene_3.2_reconstruction_manifest.json"
$env:LAYERS_CONFIG = $null
$env:SESSION_ID = "scene_3.2_reconstruction"

try {
    & (Join-Path $PSScriptRoot "scan_gui.ps1") @RemainingArgs
    $ExitCode = $LASTEXITCODE
}
finally {
    $env:POSE_PLAN_CONFIG = $PreviousPosePlan
    $env:LAYERS_CONFIG = $PreviousLayersConfig
    $env:SESSION_ID = $PreviousSession
}
if ($ExitCode -ne 0) {
    throw "Scene 3.2 reconstruction capture GUI exited with code $ExitCode."
}
