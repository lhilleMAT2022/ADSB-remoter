param(
    [string]$Source = "127.0.0.1:28887",
    [string]$ObserverFile = "",
    [string]$Observer = "",
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ConsoleArgs
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$env:PYTHONPATH = @(
    (Join-Path $RepoRoot "src")
    (Join-Path $RepoRoot ".venv\Lib\site-packages")
) -join [System.IO.Path]::PathSeparator
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"

$ArgsList = @("-m", "adsb_console.app", "--source", $Source)
if ($ObserverFile) {
    $ArgsList += @("--observerfile", $ObserverFile)
}
if ($Observer) {
    $ArgsList += @("--observer", $Observer)
}
if ($ConsoleArgs) {
    $ArgsList += $ConsoleArgs
}

& $Python @ArgsList
