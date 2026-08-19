param(
    [string]$GameId = "",
    [string]$TrajectoryFile = "",
    [string]$CameraToolsDir = "",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$profileDir = Join-Path $repositoryRoot "core\runtime\ue-camera-runtime\profiles"
$gamesDir = Join-Path $repositoryRoot "games"
$kcd2Dir = Join-Path $gamesDir "kcd2"
$kcd2Launcher = Join-Path $kcd2Dir "launch_kcd2_capture_studio.ps1"
$kcd2DataRoot = Join-Path $repositoryRoot "capture_data\kcd2"
$sharedStudioDir = Join-Path $gamesDir "black-myth-wukong"
$sharedLauncher = Join-Path $sharedStudioDir "launch_bmw_capture_studio.ps1"
$kcd2ExpectedDllSha256 = "9600C8CE3B32AE78177603695287126B05B3B165AD8820283544E8AD420B5D96"

function Get-Sha256Hex {
    param([Parameter(Mandatory = $true)][string]$LiteralPath)
    $stream = [System.IO.File]::OpenRead($LiteralPath)
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = $algorithm.ComputeHash($stream)
        return ([System.BitConverter]::ToString($bytes)).Replace("-", "")
    }
    finally {
        $algorithm.Dispose()
        $stream.Dispose()
    }
}

function Resolve-Kcd2CameraTools {
    $candidateSpecs = @()
    if (-not [string]::IsNullOrWhiteSpace($CameraToolsDir)) {
        $candidateSpecs += [pscustomobject]@{ Source = "parameter"; Path = $CameraToolsDir }
    }
    $configuredToolsDir = [Environment]::GetEnvironmentVariable("GAME_CAMERA_TOOLS_DIR")
    if (-not [string]::IsNullOrWhiteSpace($configuredToolsDir)) {
        $candidateSpecs += [pscustomobject]@{ Source = "environment"; Path = $configuredToolsDir }
    }
    $candidateSpecs += [pscustomobject]@{
        Source = "repository"
        Path = (Join-Path $kcd2Dir "camera_tools")
    }
    $candidateSpecs += [pscustomobject]@{
        Source = "legacy_sibling_project"
        Path = (Join-Path (Split-Path -Parent $repositoryRoot) "kcd2-camera-capture-studio\camera_tools")
    }

    $seen = @{}
    $fallback = $null
    foreach ($candidateSpec in $candidateSpecs) {
        try {
            $candidate = [System.IO.Path]::GetFullPath([Environment]::ExpandEnvironmentVariables([string]$candidateSpec.Path))
        }
        catch {
            continue
        }
        $candidateKey = $candidate.ToLowerInvariant()
        if ($seen.ContainsKey($candidateKey)) {
            continue
        }
        $seen[$candidateKey] = $true

        $dllPath = Join-Path $candidate "KCD2CameraTools.dll"
        $clientPath = Join-Path $candidate "IGCSClient.exe"
        $dllExists = Test-Path -LiteralPath $dllPath -PathType Leaf
        $clientExists = Test-Path -LiteralPath $clientPath -PathType Leaf
        $dllSha256 = $null
        if ($dllExists) {
            $dllSha256 = Get-Sha256Hex -LiteralPath $dllPath
        }
        $result = [pscustomobject]@{
            Source = [string]$candidateSpec.Source
            Path = $candidate
            DllPath = $dllPath
            ClientPath = $clientPath
            DllExists = $dllExists
            ClientExists = $clientExists
            DllSha256 = $dllSha256
            HashMatches = ($dllSha256 -eq $kcd2ExpectedDllSha256)
        }
        if ($null -eq $fallback) {
            $fallback = $result
        }
        if ($result.Source -eq "parameter" -or $result.Source -eq "environment") {
            return $result
        }
        if ($dllExists -and $clientExists -and $result.HashMatches) {
            return $result
        }
    }
    return $fallback
}

function Invoke-Kcd2CaptureStudio {
    if (-not (Test-Path -LiteralPath $kcd2Launcher)) {
        throw "KCD2 capture-studio launcher was not found: $kcd2Launcher"
    }

    $kcd2SettingsPath = Join-Path $kcd2DataRoot "settings.json"
    $kcd2PoseConfig = Join-Path $kcd2Dir "pose_offsets.json"
    $cameraTools = Resolve-Kcd2CameraTools
    $kcd2ToolsDir = $cameraTools.Path
    $configuration = [ordered]@{
        game_id = "kcd2"
        game_name = "Kingdom Come: Deliverance II"
        backend = "kcd2_igcs_camera_tools"
        process_names = @("KingdomCome.exe")
        profile = $null
        adapter_root = $kcd2Dir
        camera_tools_root = $kcd2ToolsDir
        camera_tools_source = $cameraTools.Source
        camera_tools_dll_exists = $cameraTools.DllExists
        camera_tools_client_exists = $cameraTools.ClientExists
        camera_tools_dll_sha256 = $cameraTools.DllSha256
        camera_tools_hash_matches = $cameraTools.HashMatches
        data_root = $kcd2DataRoot
        settings_path = $kcd2SettingsPath
        pose_config = $kcd2PoseConfig
        trajectory_file = $TrajectoryFile
        absolute_pose_status = "unverified_visual_result"
    }

    if ($DryRun) {
        $configuration | ConvertTo-Json -Depth 4
        exit 0
    }

    if (-not $cameraTools.DllExists -or -not $cameraTools.ClientExists) {
        throw (
            "KCD2 Camera Tools is incomplete. Expected KCD2CameraTools.dll and " +
            "IGCSClient.exe under: $kcd2ToolsDir. Pass -CameraToolsDir or set " +
            "GAME_CAMERA_TOOLS_DIR to the existing private installation."
        )
    }
    if (-not $cameraTools.HashMatches) {
        throw (
            "KCD2CameraTools.dll SHA256 mismatch. This adapter supports v1.0.5 " +
            "($kcd2ExpectedDllSha256), found $($cameraTools.DllSha256)."
        )
    }

    $environmentNames = @(
        "GAME_CAMERA_GAME_ID",
        "GAME_CAMERA_GAME_NAME",
        "GAME_CAMERA_GAME_SHORT_NAME",
        "GAME_CAMERA_PROCESS_NAMES",
        "GAME_CAMERA_WINDOW_PATTERNS",
        "GAME_CAMERA_HUD_REQUIRED",
        "GAME_CAMERA_ADAPTER_ROOT",
        "GAME_CAMERA_DATA_ROOT",
        "GAME_CAMERA_SETTINGS_PATH",
        "GAME_CAMERA_POSE_CONFIG_PATH",
        "GAME_CAMERA_TOOLS_DIR"
    )
    $previousEnvironment = @{}
    foreach ($name in $environmentNames) {
        $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name)
    }

    $env:GAME_CAMERA_GAME_ID = "kcd2"
    $env:GAME_CAMERA_GAME_NAME = "Kingdom Come: Deliverance II"
    $env:GAME_CAMERA_GAME_SHORT_NAME = "KCD2"
    $env:GAME_CAMERA_PROCESS_NAMES = "KingdomCome.exe"
    $env:GAME_CAMERA_WINDOW_PATTERNS = "Kingdom Come: Deliverance II,KCD2,KingdomCome"
    $env:GAME_CAMERA_HUD_REQUIRED = "0"
    $env:GAME_CAMERA_ADAPTER_ROOT = $kcd2Dir
    $env:GAME_CAMERA_DATA_ROOT = $kcd2DataRoot
    $env:GAME_CAMERA_SETTINGS_PATH = $kcd2SettingsPath
    $env:GAME_CAMERA_POSE_CONFIG_PATH = $kcd2PoseConfig
    $env:GAME_CAMERA_TOOLS_DIR = $kcd2ToolsDir

    $childExitCode = 0
    try {
        Write-Host "Launching KCD2 bottom camera adapter: Camera Tools / IGCS"
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $kcd2Launcher -TrajectoryFile $TrajectoryFile
        $childExitCode = $LASTEXITCODE
    }
    finally {
        foreach ($name in $environmentNames) {
            $value = $previousEnvironment[$name]
            if ($null -eq $value) {
                Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
            }
            else {
                Set-Item -LiteralPath "Env:$name" -Value $value
            }
        }
    }
    exit $childExitCode
}

$kcd2Candidate = [pscustomobject]@{
    Id = "kcd2"
    Name = "Kingdom Come: Deliverance II"
    ProcessNames = @("KingdomCome.exe")
    HudRequired = $false
    ProfilePath = $null
}

if ($GameId -eq "kcd2") {
    Invoke-Kcd2CaptureStudio
}

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
    $kcd2Detected = @(
        $kcd2Candidate.ProcessNames | Where-Object {
            $processBase = [System.IO.Path]::GetFileNameWithoutExtension($_).ToLowerInvariant()
            $runningNames -contains $processBase
        }
    ).Count -gt 0
    if ($kcd2Detected) {
        $detected += $kcd2Candidate
    }

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

if ($selected.Id -eq "kcd2") {
    Invoke-Kcd2CaptureStudio
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
