param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PlaybackArgs
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$DefaultCapture = Join-Path $RepoRoot "..\000_sbs_for_ELAD_cleanup\sbs_clean\10_adsb_20220413_060850.csv.gz"
$env:PYTHONPATH = @(
    (Join-Path $RepoRoot "src")
    (Join-Path $RepoRoot ".venv\Lib\site-packages")
) -join [System.IO.Path]::PathSeparator
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"

if (-not $PlaybackArgs) {
    if (-not (Test-Path -LiteralPath $DefaultCapture)) {
        throw "Default capture file was not found: $DefaultCapture"
    }
    $PlaybackArgs = @($DefaultCapture)
}

& $Python -m adsb_console.playback @PlaybackArgs
