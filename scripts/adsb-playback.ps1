param(
    [Parameter(Mandatory = $true, ValueFromRemainingArguments = $true)]
    [string[]]$PlaybackArgs
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$env:PYTHONPATH = @(
    (Join-Path $RepoRoot "src")
    (Join-Path $RepoRoot ".venv\Lib\site-packages")
) -join [System.IO.Path]::PathSeparator
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"

& $Python -m adsb_console.playback @PlaybackArgs
