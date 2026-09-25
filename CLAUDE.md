# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Python 3.13+ ADS-B tooling built around the SBS/BaseStation text protocol (dump1090 port 30003): a Textual TUI (`adsb-console`), a file-replay TCP server (`adsb-playback`), and a TCP→UDP relay (`adsb-relay`). The TUI also estimates passive bistatic radar opportunities using FCC DTV towers as illuminators. Managed with Astral `uv`; entry points are in `pyproject.toml` `[project.scripts]`.

The README has PowerShell examples (the original Windows workstation), but the current dev/deployment host is Ubuntu 24. The live feed is a Raspberry Pi at `192.168.10.131:30003`.

## Commands

```bash
uv sync --group dev                                   # install with dev tools
uv run pytest -p no:cacheprovider                     # all tests (asyncio_mode=auto, pythonpath=src)
uv run pytest tests/test_bistatic.py::test_name       # single test
uv run ruff check . --no-cache                        # lint (line length 100, broad rule set incl. PL, B, UP, RUF)
uv run pyright                                        # strict mode over src/ and tests/
```

Before committing, all three must be clean. Pyright runs in **strict** mode, so new code needs full type annotations. The cache flags came from OneDrive permission problems on the original Windows machine; they don't hurt on Linux.

Run locally end to end by starting a replay server, then pointing the TUI at it:

```bash
uv run adsb-playback tests/fixtures/sbs_clean/10_adsb_20220413_060850.csv.gz --bind 127.0.0.1:28887
uv run adsb-console --source 127.0.0.1:28887 [--observerfile tests/fixtures/observers.ini]
```

Playback rewrites message timestamps to the current time by default (`--no-rebase-timestamps` turns this off). Age-out and purge compare each track's `last_seen` against wall-clock `utc_now()`, so replaying the 2022 fixtures without rebasing makes every track show as aged or get purged.

## Architecture

All code is in `src/adsb_console/`. Dependencies point one way, and only `app.py` imports Textual:

`models` → `transforms` → `tracker` / `bistatic` → `app`, plus `config` for observers and endpoints and `playback` / `relay` as standalone CLIs.

- **models.py**: `BaseStationMessage.parse` splits a 22-field SBS CSV line and raises `ValueError` if it is malformed. `TrackState` holds per-ICAO state and a bounded position history. `ObserverConfig` is the frozen description of a sensor site, including RF receiver parameters (`receiver_gain_dbi`, `noise_figure_db`, `bandwidth_mhz`) that the bistatic model uses.
- **transforms.py**: WGS-84 LLA→ECEF→observer ENU conversion, range/az/el, range rate, Doppler, CPA, and `is_observable_by`. That last gate always passes for local observers; remote observers are checked against range, az/el extents, and horizon.
- **tracker.py**: `BaseStationTracker` keeps a dict keyed by ICAO. Each `update()` also purges stale tracks relative to the message timestamp (the default retention is 20 min). `observed_tracks()` projects tracks into observer frames, filtered by each observer's `seek_pattern` regex, and returns `ObservedTrack` records.
- **bistatic.py**: loads DTV emitters from `20_DTV_direct_path_input.csv` (FCC-derived; EIRP in kW) and filters them by band (UHF by default). `bistatic_snr_breakdown` itemizes the bistatic radar equation in dB so that the fields add up to `snr_db`. The receive gain, noise figure, and bandwidth come from the receiver `ObserverConfig`; RCS, polarization loss, and system loss are module defaults. `top_bistatic_measurements` keeps only one emitter per physical tower (`tower_key`: the ASRN, or a geographic key if there is none) so the ranked opportunities are geographically distinct.
- **config.py**: parses the observer INI format (keys such as `locLat_locLon_locAlt`, `minR_maxR`, `seekPattern`, `receiverGainDbi`/`Gr`, and similar). `ensure_local_observer` guarantees that at least one observer is local. The built-in defaults are the MathWorks Apple Hill lot (local) and the CBS tower (remote), and the comments explaining the chosen RF values are intentional.
- **app.py**: `ADSBConsoleApp` runs one worker (`_monitor_source`) that reads the TCP stream line by line and feeds the tracker. It redraws the table only on the refresh interval, not on every message. The app has two screen modes: `OBSERVER_TABLE`, one row per track for the selected observer, with BiSNR columns computed only for the closest N displayed tracks; and `TRACK_FOCUS_TABLE`, one row per observer for the selected ICAO. The filtering, sorting, and row-formatting helpers are module-level pure functions (`filter_display_tracks`, `sort_display_tracks`, `track_focus_row`, `bisnr_breakdown_table`, etc.), and `tests/test_app.py` tests those directly instead of driving the Textual UI. Put new display logic in functions like these too.

Tests use small in-code fixtures plus `tests/fixtures/` (a basestation sample, the observers INI, and gzipped real SBS captures).
