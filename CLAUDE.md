# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Python 3.13+ ADS-B tooling built around the SBS/BaseStation text protocol (dump1090 port 30003): a Textual TUI (`adsb-console`), a file-replay TCP server (`adsb-playback`), and a TCP→UDP relay (`adsb-relay`). The TUI also estimates passive bistatic radar opportunities using FCC DTV towers as illuminators. Managed with Astral `uv`; entry points are in `pyproject.toml` `[project.scripts]`.

### Purpose: cueing tracks of opportunity for the passive radar

This tool listens to the dump1090 receiver and acts as a **bell ringer**: it flags aircraft entering the airspace that are good passive-radar opportunities and says which DTV illuminators suit them best. The passive bistatic radar collection system in `~/Documents/flightTest-pluto` (MATLAB, USRP N320 SURV/REF receiver at Apple Hill, git remote `pwilliamMAT/flightTest`) handles resource management: it decides when to start a passive track and keeps updating it, using the best illuminator available. Keep that split. This repo produces cues and ranks illuminators; it does not schedule collections or control the radio.

The cue interface is specified in `ADSBConsole_PassiveRadar_Cueing_Full_Engineering_Spec.md`, which covers the functional requirements FR-001–FR-014, the data model, the UDP transport, and the message schemas. That spec is the contract with the collection system, so read the relevant FR section before changing cue behavior.
- **Messages:** versioned UDP JSON messages validated by `schemas/*-1.1.0.json`: `track_cue`, `track_cue_withdrawal`, heartbeat, and snapshot begin/end. Older versions live in `schemas/archive/`.
- **Changing the wire format:** add a new schema version and bump `SCHEMA_VERSION` in `cue.py`; don't edit a released schema in place. Every schema property is emitted on every message, with `null` for unknown values, so MATLAB `jsondecode` produces stable structs. Keep it that way.
- **Enabling it:** cueing is off unless `--cue-config <json>` is given. `cue_config.py` validates that file against `schemas/cue-config-1.0.0.json`, and `examples/passive-radar-cueing.json` is a template. That example sends to multicast `239.192.10.1:31986` with TTL 1, so the collection system must be on the same subnet.
- **Unused INI settings:** the observer INI `reportMethod`/`reportEndpoint`/`reportRateHz` keys are parsed but not used by cueing.

The two systems must agree on these:
- **Bistatic convention:** `R_excess = R_tx + R_rx − L_baseline` and `f_D = −(fc/c)·dR_excess/dt`, both matching `bistatic_measurement` here. flightTest-pluto enforces the same convention with `bistaticTruthConventionTest.m`, so don't let them drift apart.
- **DTV emitter table:** `20_DTV_direct_path_input.csv` is also kept at `flightTest-pluto/TestSetupTesting/siteData/`.
- **Receive site:** the geometry and antenna pointing are documented in `flightTest-pluto/TestSetupTesting/SiteGeometry.md`.
- **Pi ADS-B feed:** the Pi at `192.168.10.131` also runs the collection system's truth logger (`ADSB_GPS/gatherTCPcompress.py`).

The README has PowerShell examples (the original Windows workstation), but the current dev/deployment host is Ubuntu 24. The live feed is a Raspberry Pi at `192.168.10.131:30003`.

## Commands

```bash
uv sync --group dev                                   # install with dev tools
uv run pytest -p no:cacheprovider                     # all tests (asyncio_mode=auto, pythonpath=src)
uv run pytest tests/test_bistatic.py::test_name       # single test
uv run ruff check . --no-cache                        # lint (line length 100, broad rule set incl. PL, B, UP, RUF)
uv run pyright                                        # strict mode over src/ and tests/
```

`tests/test_replay_cueing.py` looks for the external replay corpus at `$ADSB_REPLAY_CORPUS_DIR` (by default `000_sbs_for_ELAD_cleanup/sbs_clean` in the folder that contains this repo) and **silently passes if it isn't there**, which is the case on this machine. To actually run it against the in-repo copy of the capture, use `ADSB_REPLAY_CORPUS_DIR=tests/fixtures/sbs_clean uv run pytest tests/test_replay_cueing.py`. Two tools support the cue work: `tools/cue_capture.py --bind HOST:PORT --duration-s N` captures cue traffic and validates it against the schemas, and `tools/generate_replay_manifest.py` rebuilds `tests/replay_corpus_manifest.json` from the corpus.

Before committing, all three must be clean. Pyright runs in **strict** mode, so new code needs full type annotations. The cache flags came from OneDrive permission problems on the original Windows machine; they don't hurt on Linux.

Run locally end to end by starting a replay server, then pointing the TUI at it:

```bash
uv run adsb-playback tests/fixtures/sbs_clean/10_adsb_20220413_060850.csv.gz --bind 127.0.0.1:28887
uv run adsb-console --source 127.0.0.1:28887 [--observerfile tests/fixtures/observers.ini]
```

`--headless` runs the same app with no terminal UI, for use as a service. Status-log lines and a summary once a minute go to stderr through `logging`. SIGTERM or SIGINT does a clean exit that sends a `stopping` heartbeat, and losing the SBS source makes it exit 1 so a supervisor restarts it. `deploy/adsb-cue.service` and `deploy/pi-cue-config.json` run it as the cue tasker on the ADS-B Raspberry Pi (`pi2@192.168.10.131`, repo at `~/flightTest/ADSB-remoter`, local dump1090 on `127.0.0.1:30003`); the install and remote-control commands are in the unit file's header.

Playback rewrites message timestamps to the current time by default (`--no-rebase-timestamps` turns this off). Age-out and purge compare each track's `last_seen` against wall-clock `utc_now()`, so replaying the 2022 fixtures without rebasing makes every track show as aged or get purged.

## Architecture

All code is in `src/adsb_console/`. Dependencies point one way, and only `app.py` imports Textual:

`models` → `transforms` → `tracker` / `bistatic` → `prediction` → `cue` → `cue_config` → `app`, plus `config` for observers and endpoints and `playback` / `relay` as standalone CLIs.

- **models.py**: `BaseStationMessage.parse` splits a 22-field SBS CSV line and raises `ValueError` if it is malformed. `TrackState` holds per-ICAO state and a bounded position history. `ObserverConfig` is the frozen description of a sensor site, including RF receiver parameters (`receiver_gain_dbi`, `noise_figure_db`, `bandwidth_mhz`) that the bistatic model uses.
- **transforms.py**: WGS-84 LLA→ECEF→observer ENU conversion, range/az/el, range rate, Doppler, CPA, and `is_observable_by`. That last gate always passes for local observers; remote observers are checked against range, az/el extents, and horizon.
- **tracker.py**: `BaseStationTracker` keeps a dict keyed by ICAO. Each `update()` also purges stale tracks relative to the message timestamp (the default retention is 20 min). `observed_tracks()` projects tracks into observer frames, filtered by each observer's `seek_pattern` regex, and returns `ObservedTrack` records.
- **bistatic.py**: loads DTV emitters from `20_DTV_direct_path_input.csv` (FCC-derived; EIRP in kW) and filters them by band (UHF by default). `bistatic_snr_breakdown` itemizes the bistatic radar equation in dB so that the fields add up to `snr_db`. The receive gain, noise figure, and bandwidth come from the receiver `ObserverConfig`; RCS, polarization loss, and system loss are module defaults. `top_bistatic_measurements` keeps only one emitter per physical tower (`tower_key`: the ASRN, or a geographic key if there is none) so the ranked opportunities are geographically distinct.
- **prediction.py**: the cueing logic, with no UI dependencies. `build_track_prediction` propagates a track at constant velocity over `prediction_horizon_s`, sampling every `prediction_sample_interval_s`. For every observer × DTV emitter pair it computes bistatic range, range rate, Doppler, and SNR for each sample, then extracts `ObservationWindow`s where SNR clears `detection_threshold_db` (default −10 dB, pre-integration) and the geometry gates pass. Window boundaries are refined between samples, and every window records an entry and exit reason. `PredictionTriggerEvaluator` decides when to regenerate a prediction: on a new track, a maneuver, a prediction-error threshold, or when the prediction ages out. `PredictionRevisionManager` hands out revision tokens so that a stale prediction computed in the background is discarded. All thresholds are in `PredictionConfig`.
- **cue.py**: `CueSerializer` turns predictions into schema DTOs. `UdpCuePublisher` sends them as unicast or multicast UDP with sequence numbers and a UUID for each message. It enforces `maximum_datagram_bytes`, and when a message is too large the `omit_history` policy drops the per-sample history. It also counts failures and oversize messages, which feed into the heartbeat.
- **cue_config.py**: loads and validates the `--cue-config` JSON into `CueRuntimeConfig`, which holds the publication mode, `PredictionConfig`, and `UdpOutputConfig`.
- **config.py**: parses the observer INI format (keys such as `locLat_locLon_locAlt`, `minR_maxR`, `seekPattern`, `receiverGainDbi`/`Gr`, and similar). `ensure_local_observer` guarantees that at least one observer is local. The built-in defaults are the MathWorks Apple Hill lot (local) and the CBS tower (remote), and the comments explaining the chosen RF values are intentional.
- **app.py**: `ADSBConsoleApp` runs one worker (`_monitor_source`) that reads the TCP stream line by line and feeds the tracker. It redraws the table only on the refresh interval, not on every message. The app has two screen modes: `OBSERVER_TABLE`, one row per track for the selected observer, with BiSNR columns computed only for the closest N displayed tracks; and `TRACK_FOCUS_TABLE`, one row per observer for the selected ICAO. The filtering, sorting, and row-formatting helpers are module-level pure functions (`filter_display_tracks`, `sort_display_tracks`, `track_focus_row`, `bisnr_breakdown_table`, etc.), and `tests/test_app.py` tests those directly instead of driving the Textual UI. Put new display logic in functions like these too.
  **Worker groups:** Textual's `run_worker(..., exclusive=True)` cancels *every* worker in the same group, not just ones with the same name. Any exclusive worker therefore needs its own `group=`. Otherwise it silently cancels the `source-monitor` SBS reader, and the app keeps running but stops receiving ADS-B. `test_cue_snapshot_worker_does_not_cancel_source_monitor` guards this.
  Cueing in the app works like this. Each tracker update may schedule a prediction, and a 1 s timer also schedules periodic ones. `build_track_prediction` and every publisher call run in `asyncio.to_thread`, so the UI isn't blocked. In `automatic` mode each committed prediction is published. In `manual` mode (toggle with `m`), only the selected track is sent, and only when the user presses `Q` (uppercase; lowercase `q` quits). Timers send the heartbeat and periodic full snapshots, and a withdrawal (`reason: track_purged`) is published for any previously published track that gets purged. Withdrawals happen only at purge time (`--track-retention-minutes`, default 20 min), not when a track ages out after 20 s, so the collection system has to rely on each cue's `valid_until_utc` for tracks that go stale.

Tests use small in-code fixtures plus `tests/fixtures/` (a basestation sample, the observers INI, and gzipped real SBS captures).
