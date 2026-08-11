$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepositoryRoot = (Resolve-Path (Join-Path $ProjectRoot "..\..")).Path
$PythonCandidates = @(
    (Join-Path $ProjectRoot ".venv\Scripts\python.exe"),
    (Join-Path $RepositoryRoot ".venv\Scripts\python.exe")
)

$SelectedPython = $null
foreach ($Candidate in $PythonCandidates) {
    if (Test-Path -LiteralPath $Candidate) {
        $SelectedPython = $Candidate
        break
    }
}

if ($null -eq $SelectedPython) {
    $PythonCommand = Get-Command py -ErrorAction SilentlyContinue
    if ($null -ne $PythonCommand) {
        $SelectedPython = $PythonCommand.Source
    } else {
        $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if ($null -ne $PythonCommand) {
            $SelectedPython = $PythonCommand.Source
        }
    }
}

if ($null -eq $SelectedPython) {
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show(
        "Python 3.10+ was not found. Install Python or create .venv in this project.",
        "KCD2 Capture Studio"
    )
    throw "Python 3.10+ was not found"
}

Write-Host "KCD2 Capture Studio"
Write-Host "Project: $ProjectRoot"
Write-Host "Python:  $SelectedPython"

Push-Location (Join-Path $ProjectRoot "src")
try {
    & $SelectedPython -m kcd2_capture_studio
    if ($LASTEXITCODE -ne 0) {
        throw "KCD2 Capture Studio exited with code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
