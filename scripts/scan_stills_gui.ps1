param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingArgs
)

# Backward-compatible still-scan entry point. The generic Windows launcher now
# mirrors scripts/scan_gui.sh and also exposes trajectory profiles and recovery.
$PreviousLayersConfig = $env:LAYERS_CONFIG
if (-not $env:LAYERS_CONFIG -and -not $env:POSE_PLAN_CONFIG) {
    $env:LAYERS_CONFIG = "core\configs\still_scan_layers.yaml"
}

try {
    & (Join-Path $PSScriptRoot "scan_gui.ps1") @RemainingArgs
    $ExitCode = $LASTEXITCODE
}
finally {
    $env:LAYERS_CONFIG = $PreviousLayersConfig
}
if ($ExitCode -ne 0) {
    throw "RE9 capture GUI exited with code $ExitCode."
}
