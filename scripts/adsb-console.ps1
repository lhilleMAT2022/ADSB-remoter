param(
    [string]$Source = "127.0.0.1:28887",
    [string]$ObserverFile = "",
    [string]$Observer = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$env:PYTHONPATH = Join-Path $RepoRoot "src"
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"

$ArgsList = @("-m", "adsb_console.app", "--source", $Source)
if ($ObserverFile) {
    $ArgsList += @("--observerfile", $ObserverFile)
}
if ($Observer) {
    $ArgsList += @("--observer", $Observer)
}

& $Python @ArgsList
