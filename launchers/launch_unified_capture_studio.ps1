param(
    [string]$GameId = "",
    [string]$TrajectoryFile = "",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$profileDir = Join-Path $repositoryRoot "core\runtime\ue-camera-runtime\profiles"
$gamesDir = Join-Path $repositoryRoot "games"
$sharedStudioDir = Join-Path $gamesDir "black-myth-wukong"
$sharedLauncher = Join-Path $sharedStudioDir "launch_bmw_capture_studio.ps1"

if (-not (Test-Path -LiteralPath $sharedLauncher)) {
    throw "Unified capture-studio host was not found: $sharedLauncher"
}

$profiles = @(
    Get-ChildItem -LiteralPath $profileDir -Filter "*.json" -File |
        ForEach-Object {
            $profile = Get-Content -LiteralPath $_.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
            [pscustomobject]@{
                Id = [string]$profile.id
                Name = [string]$profile.name
                ProcessNames = @($profile.process_names | ForEach-Object { [string]$_ })
                HudRequired = $null -ne $profile.hud_hook
                ProfilePath = $_.FullName
            }
        }
)

if ($profiles.Count -eq 0) {
    throw "No UE camera profiles were found in $profileDir"
}

function Select-InteractiveProfile {
    param(
        [object[]]$Candidates,
        [string]$Reason
    )

    Write-Host $Reason
    for ($index = 0; $index -lt $Candidates.Count; $index++) {
        $candidate = $Candidates[$index]
        Write-Host ("[{0}] {1} ({2})" -f ($index + 1), $candidate.Name, $candidate.Id)
    }
    $answer = Read-Host "Select adapter number"
    $number = 0
    if (-not [int]::TryParse($answer, [ref]$number) -or $number -lt 1 -or $number -gt $Candidates.Count) {
        throw "A valid adapter number is required. You can also pass -GameId explicitly."
    }
    return $Candidates[$number - 1]
}

$selected = $null
if ($GameId) {
    $selected = $profiles | Where-Object { $_.Id -eq $GameId } | Select-Object -First 1
    if ($null -eq $selected) {
        $available = ($profiles.Id | Sort-Object) -join ", "
        throw "Unknown GameId '$GameId'. Available profiles: $available"
    }
}
else {
    $runningNames = @(
        Get-Process -ErrorAction SilentlyContinue |
            ForEach-Object { $_.ProcessName.ToLowerInvariant() } |
            Sort-Object -Unique
    )
    $detected = @(
        $profiles | Where-Object {
            $profile = $_
            @($profile.ProcessNames | Where-Object {
                $processBase = [System.IO.Path]::GetFileNameWithoutExtension($_).ToLowerInvariant()
                $runningNames -contains $processBase
            }).Count -gt 0
        }
    )

    if ($detected.Count -eq 1) {
        $selected = $detected[0]
        Write-Host "Auto-detected: $($selected.Name)"
    }
    elseif ($detected.Count -gt 1) {
        $selected = Select-InteractiveProfile -Candidates $detected -Reason (
            "Multiple supported games are running; choose one."
        )
    }
    else {
        $selected = [pscustomobject]@{
            Id = "unified-auto"
            Name = "Auto-detect Supported Game"
            ProcessNames = @($profiles.ProcessNames | ForEach-Object { $_ } | Sort-Object -Unique)
            HudRequired = $false
            ProfilePath = $null
        }
        Write-Host "No game is running. Starting in auto-detect mode."
    }
}

$adapterDir = if ($selected.Id -eq "unified-auto") {
    $repositoryRoot
} else {
    Join-Path $gamesDir $selected.Id
}
if (-not (Test-Path -LiteralPath $adapterDir)) {
    throw "Adapter directory was not found: $adapterDir"
}

$gameManifestPath = Join-Path $adapterDir "game.json"
$manifest = $null
if (Test-Path -LiteralPath $gameManifestPath) {
    $manifest = Get-Content -LiteralPath $gameManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
}

$displayName = if ($selected.Id -eq "unified-auto") {
    "Auto-detect Supported Game"
} elseif ($null -ne $manifest -and $manifest.name) {
    [string]$manifest.name
} else {
    $selected.Name
}
$shortName = if ($selected.Id -eq "unified-auto") {
    "Auto-detect"
} elseif ($null -ne $manifest -and $manifest.short_name) {
    [string]$manifest.short_name
} else {
    $displayName
}
$windowPatterns = @($displayName, $shortName)
$windowPatterns += @(
    $selected.ProcessNames | ForEach-Object { [System.IO.Path]::GetFileNameWithoutExtension($_) }
)

$launchConfiguration = [ordered]@{
    game_id = $selected.Id
    game_name = $displayName
    process_names = @($selected.ProcessNames)
    profile = $selected.ProfilePath
    adapter_root = $adapterDir
    hud_required = [bool]$selected.HudRequired
    trajectory_file = $TrajectoryFile
}

if ($DryRun) {
    $launchConfiguration | ConvertTo-Json -Depth 4
    exit 0
}

$env:GAME_CAMERA_GAME_ID = $selected.Id
$env:GAME_CAMERA_GAME_NAME = $displayName
$env:GAME_CAMERA_GAME_SHORT_NAME = $shortName
$env:GAME_CAMERA_PROCESS_NAMES = $selected.ProcessNames -join ","
$env:GAME_CAMERA_WINDOW_PATTERNS = ($windowPatterns | Sort-Object -Unique) -join ","
$env:GAME_CAMERA_HUD_REQUIRED = if ($selected.HudRequired) { "1" } else { "0" }
$env:GAME_CAMERA_ADAPTER_ROOT = $adapterDir
$env:UE_CAMERA_NATIVE_DIR = Join-Path $sharedStudioDir "native"
if ($selected.Id -eq "unified-auto") {
    $unifiedDataRoot = Join-Path $repositoryRoot "capture_data\unified-camera"
    $env:GAME_CAMERA_DATA_ROOT = $unifiedDataRoot
    $env:GAME_CAMERA_SETTINGS_PATH = Join-Path $unifiedDataRoot "settings.json"
}

Write-Host "Launching: $displayName ($($selected.Id))"
& $sharedLauncher -TrajectoryFile $TrajectoryFile
