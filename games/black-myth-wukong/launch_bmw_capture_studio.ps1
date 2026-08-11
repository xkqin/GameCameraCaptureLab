$ErrorActionPreference = "Stop"

$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvDir = Join-Path $projectDir ".venv"
$pythonExe = Join-Path $venvDir "Scripts\python.exe"
$pythonwExe = Join-Path $venvDir "Scripts\pythonw.exe"

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

Start-Process -FilePath $pythonwExe -ArgumentList @("-m", "bmw_capture_studio") -WorkingDirectory $projectDir
