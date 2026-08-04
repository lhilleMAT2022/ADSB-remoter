# ADS-B Console Respin

Clean Python respin of the BaseStation ADS-B stream parser, playback tool, tracker, retransmitter, and Textual TUI.

## Development Environment

This project targets Python 3.13 or newer and uses Astral uv.

```powershell
uv python install 3.13
uv sync --group dev
```

The runtime dependency set is intentionally small. Textual provides the TUI; BaseStation parsing, playback, TCP/UDP I/O, tracking, and coordinate transforms should remain independently testable without the TUI layer.

## MVP Commands

Replay one or more recorded BaseStation files over TCP:

```powershell
adsb-playback ..\000_sbs_for_ELAD_cleanup\sbs_clean\10_adsb_20220413_060850.csv --bind 127.0.0.1:28887
```

Monitor a live or playback BaseStation TCP stream in the Textual TUI:

```powershell
adsb-console --source 127.0.0.1:28887
```

Relay a BaseStation TCP stream to one or more UDP destinations:

```powershell
adsb-relay --source 127.0.0.1:28887 --dest 127.0.0.1:63542,127.0.0.1:11111
```

The playback tool accepts plain text/CSV BaseStation files and `.gz` compressed files.

## Verification

```powershell
python -m pytest -p no:cacheprovider
python -m ruff check . --no-cache
$env:PYRIGHT_PYTHON_CACHE_DIR=(Resolve-Path .\.pyright-cache).Path; pyright
```

The cache flags and environment variable avoid local OneDrive/cache permission issues seen on this workstation.
