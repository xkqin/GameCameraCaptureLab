$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Candidates = @(
    (Join-Path $ProjectRoot ".venv\Scripts\pythonw.exe"),
    (Join-Path $ProjectRoot ".venv\Scripts\python.exe")
)

$Python = $Candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $Python) {
    foreach ($Name in @("pyw.exe", "pythonw.exe", "py.exe", "python.exe")) {
        $Command = Get-Command $Name -ErrorAction SilentlyContinue
        if ($Command) {
            $Python = $Command.Source
            break
        }
    }
}
if (-not $Python) {
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show(
        "Python 3.10+ was not found.",
        "Game Camera Capture Lab"
    ) | Out-Null
    throw "Python 3.10+ was not found"
}

$Arguments = @()
if ([IO.Path]::GetFileName($Python) -in @("py.exe", "pyw.exe")) {
    $Arguments += "-3"
}
$ScriptPath = Join-Path $PSScriptRoot "game_capture_hub.py"
$Arguments += ('"{0}"' -f $ScriptPath)

Start-Process -FilePath $Python -ArgumentList $Arguments -WorkingDirectory $ProjectRoot -WindowStyle Hidden
