# ADS-B Console Respin

Clean Python respin of the BaseStation ADS-B stream parser, playback tool, tracker, retransmitter, and Textual TUI.

## Development Environment

This project targets Python 3.13 or newer and uses Astral uv.

```powershell
uv python install 3.13
uv sync --group dev
```

The runtime dependency set is intentionally small. Textual provides the TUI; BaseStation parsing, playback, TCP/UDP I/O, tracking, and coordinate transforms should remain independently testable without the TUI layer.

## Ubuntu 24 Deployment

The field deployment target is an Ubuntu 24 desktop that can reach the Raspberry Pi running dump1090 at `192.168.2.131`. dump1090 normally exposes SBS/BaseStation messages on TCP port `30003`, so the live source is `192.168.2.131:30003`.

Install baseline tools:

```bash
sudo apt update
sudo apt install -y git curl netcat-openbsd
curl -LsSf https://astral.sh/uv/install.sh | sh
source "$HOME/.local/bin/env"
```

Clone and install the console:

```bash
git clone git@github.com:lhilleMAT2022/ADSB-remoter.git
cd ADSB-remoter
uv python install 3.13
uv sync
```

Before launching the TUI, verify that the Ubuntu desktop can reach the Pi's dump1090 SBS feed:

```bash
nc -vz 192.168.2.131 30003
```

Start the live TUI:

```bash
uv run adsb-console --source 192.168.2.131:30003
```

Useful live-display options:

```bash
uv run adsb-console --source 192.168.2.131:30003 --refresh-rate 0.5
uv run adsb-console --source 192.168.2.131:30003 --max-display-range-km 150
uv run adsb-console --source 192.168.2.131:30003 --show-aged-tracks
```

If `nc` cannot connect, check that the desktop and Pi are on the same reachable network, that the Pi address is still `192.168.2.131`, and that dump1090 is configured to expose the SBS/BaseStation TCP output on port `30003`.

## MVP Commands

Replay one or more recorded BaseStation files over TCP:

```powershell
adsb-playback .\tests\fixtures\sbs_clean\10_adsb_20220413_060850.csv.gz --bind 127.0.0.1:28887
```

The playback server logs client connections, clean disconnects, and periodic sent-message summaries. Use `--status-interval 5` to change the status cadence.

Monitor a live or playback BaseStation TCP stream in the Textual TUI:

```powershell
adsb-console --source 127.0.0.1:28887
```

Observer configuration is loaded from an INI file. The TUI always has a local observer and displays range, azimuth, and elevation from the selected observer. Press `o` to cycle observers.

```powershell
adsb-console --source 127.0.0.1:28887 --observerfile .\tests\fixtures\observers.ini
```

Without an observer file, the default local observer is Goat Island Lighthouse in Newport, RI, and the default remote observer is MathWorks Apple Hill.

The TUI redraws the table at a bounded screen refresh rate instead of rebuilding it for every incoming SBS message. Use `--refresh-rate 0.5` or similar to tune the display cadence. Display controls:

- `o`: cycle observers
- `a`: toggle all tracks versus filtered display
- `+` / `-`: increase or decrease the maximum display range
- `f`: focus the ICAO regex filter; press Enter to apply
- `h`: toggle aged-out tracks hidden or shown
- `s`: cycle the active sort column

Tracks with no reports for more than 20 seconds are hidden by default. The summary line reports visible and hidden track counts.

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

## Local Launchers

If the editable package has not been installed into `.venv`, use the checked-in PowerShell launchers. They set `PYTHONPATH=src` before running the module.

```powershell
.\scripts\adsb-playback.ps1 .\tests\fixtures\sbs_clean\10_adsb_20220413_060850.csv.gz --bind 127.0.0.1:28887
.\scripts\adsb-console.ps1 -Source 127.0.0.1:28887
```
