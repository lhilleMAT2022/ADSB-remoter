# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Python 3.13+ ADS-B tooling built around the SBS/BaseStation text protocol (dump1090 port 30003): a Textual TUI (`adsb-console`), a file-replay TCP server (`adsb-playback`), and a TCP→UDP relay (`adsb-relay`). The TUI also estimates passive bistatic radar opportunities using FCC DTV towers as illuminators. Managed with Astral `uv`; entry points are in `pyproject.toml` `[project.scripts]`.

### Purpose: cueing tracks of opportunity for the passive radar

This tool listens to the dump1090 receiver and acts as a **bell ringer**: it flags aircraft entering the airspace that are good passive-radar opportunities and says which DTV illuminators suit them best. The passive bistatic radar collection system is the flightTest repo (MATLAB, USRP N320 SURV/REF receiver at Apple Hill; git remote `pwilliamMAT/flightTest`; local worktrees under `~/Documents/flightTest*`). It handles resource management: it decides when to start a passive track and keeps updating it, using the best illuminator available. Keep that split. This repo produces cues and ranks illuminators; it does not schedule collections or control the radio.

**The system documents live in flightTest `docs/system/` on `main`, the master copy** ([online](https://github.com/pwilliamMAT/flightTest/tree/main/docs/system)). In `System_Architecture.md` this program is item **CT**, the ADSB Cue Tasker.
- **The contract:** the cue interface is governed by [`ICD_Messages.md`](https://github.com/pwilliamMAT/flightTest/blob/main/docs/system/ICD_Messages.md), §1 for conventions and transport and §2 for the CT messages. Read the relevant section before changing message content, schemas, rates or transport.
- **The engineering spec:** `ADSBConsole_PassiveRadar_Cueing_Full_Engineering_Spec.md` in this repo is the original implementation spec (FR-001–FR-014, the prediction design). Where it and the ICD differ, the ICD wins.
- **Change process:** changes to messages or to the config schema start as a change request in flightTest `docs/system/Change_Requests.md`, with evidence, before or alongside the code. The current deviation is the top-N opportunity cap (CR-1).
- **Record keeping:**
  - Record deployment changes in `docs/system/As_Built.md`.
  - Record verification captures from `tools/cue_capture.py` or socat in `docs/system/Verification_Log.md`, with the raw file under `docs/system/evidence/`.
- **Messages:** CT 2.0.0 (ICD §2), validated by `schemas/*-2.0.0.json`: `track_cue`, `track_cue_withdrawal`, heartbeat, and snapshot begin/end. The schemas come from `tools/write_cue_schemas.py`; edit the generator, not the JSON. Older versions live in `schemas/archive/`.
  - **Times:** integers named `*_utc_ms` (epoch milliseconds).
  - **Rounding:** values are rounded to the ICD §1.2 resolutions.
  - **`models`** appears once per cue.
  - **Removed as derivable:** `prediction_id`, `opportunity_id`, `window_id`, `observer_name` and the always-true flags.
  - **`summary`** is a debug-only startup option (`--cue-include-summary` or `cue_prediction.include_summary`).
- **Framing (ICD §1.3):** one message per datagram.
  - **Two forms:** plain JSON (first byte `{`), or `0xDC`, a dictionary id, then raw deflate compressed with that dictionary as the preset dictionary.
  - **Chosen once at startup:** `udp_output.encoding` / `dictionary_id`, or `--cue-encoding`. `CueFramer` encodes; `decode_datagram` and `load_dictionaries` are the receiver side.
  - **Fit to frame:** `maximum_datagram_bytes` defaults to 1472, one Ethernet frame. `UdpCuePublisher.publish_prediction` sheds the weakest opportunities until a cue fits, up to `maximum_opportunities_per_cue` = 8.
- **Dictionaries:** `schemas/dictionaries/cue-dictionary-<id>.bin` plus `.sha256` and `.build.json`. `load_dictionary` checks the SHA-256.
  - **Built by** `tools/build_cue_dictionary.py --id N`, reproducibly (about 5 min), from the SBS fixtures replayed through the real prediction and serializer code. Every corpus message is validated against the schemas first.
  - **Released dictionaries are immutable:** the tool refuses to overwrite one, and `--check` confirms a rebuild matches.
  - **Changing messages or tuning compression means a new dictionary id**, registered in the ICD via a CR.
- **Changing the wire format:** start with a CR in flightTest (above). Then add a new schema version and bump `SCHEMA_VERSION` in `cue.py`; never edit a released schema or dictionary in place.
  - Keep the ICD §1.2 shape rules, so MATLAB `jsondecode` produces stable structs: every property present in every message of a run, no field that is sometimes null and sometimes an object, identical keys across array elements.
- **Enabling it:** cueing is off unless `--cue-config <json>` is given. `cue_config.py` validates that file against `schemas/cue-config-2.0.0.json`.
  - **Templates:** `examples/passive-radar-cueing.json` (plain JSON) and `deploy/pi-cue-config.json` (compressed).
  - **Multicast:** both send to `239.192.10.1:31986` with TTL 1, so the collection system must be on the same subnet.
- **Unused INI settings:** the observer INI `reportMethod`/`reportEndpoint`/`reportRateHz` keys are parsed but not used by cueing.

The two systems must agree on these:
- **Bistatic convention:** `R_excess = R_tx + R_rx − L_baseline` and `f_D = −(fc/c)·dR_excess/dt`, both matching `bistatic_measurement` here. flightTest-pluto enforces the same convention with `bistaticTruthConventionTest.m`, so don't let them drift apart.
- **DTV emitter table:** the master is flightTest `docs/system/20_DTV_direct_path_input.csv`. The copy at this repo's root must match it (same rows; only line endings differ today).
- **Receive site:** the master for geometry and antenna pointing is flightTest `docs/system/SiteGeometry.md`. `deploy/pi-observers.ini` takes the receive-site position from it. See CR-7 for the altitude inconsistency.
- **Pi ADS-B feed:** the Pi at `192.168.10.131` also runs the collection system's truth logger (`ADSB_GPS/gatherTCPcompress.py`).

The README has PowerShell examples (the original Windows workstation), but the current development host is the Ubuntu 26.04 RF collection desktop. The live feed is a Raspberry Pi at `192.168.10.131:30003`. See **Lab deployment** below.

## Commands

```bash
uv sync --group dev                                   # install with dev tools
uv run pytest -p no:cacheprovider                     # all tests (asyncio_mode=auto, pythonpath=src)
uv run pytest tests/test_bistatic.py::test_name       # single test
uv run ruff check . --no-cache                        # lint (line length 100, broad rule set incl. PL, B, UP, RUF)
uv run pyright                                        # strict mode over src/ and tests/
```

`tests/test_replay_cueing.py` looks for the external replay corpus at `$ADSB_REPLAY_CORPUS_DIR` (by default `000_sbs_for_ELAD_cleanup/sbs_clean` in the folder that contains this repo) and **silently passes if it isn't there**, which is the case on this machine. To actually run it against the in-repo copy of the capture, use `ADSB_REPLAY_CORPUS_DIR=tests/fixtures/sbs_clean uv run pytest tests/test_replay_cueing.py`. Two tools support the cue work: `tools/cue_capture.py --bind HOST:PORT --duration-s N` captures cue traffic and validates it against the schemas. It uses an 8 MiB receive buffer because snapshot bursts overflow the kernel default and show up as false sequence gaps, and `tools/generate_replay_manifest.py` rebuilds `tests/replay_corpus_manifest.json` from the corpus.

Before committing, all three must be clean. Pyright runs in **strict** mode, so new code needs full type annotations. The cache flags came from OneDrive permission problems on the original Windows machine; they don't hurt on Linux.

Run locally end to end by starting a replay server, then pointing the TUI at it:

```bash
uv run adsb-playback tests/fixtures/sbs_clean/10_adsb_20220413_060850.csv.gz --bind 127.0.0.1:28887
uv run adsb-console --source 127.0.0.1:28887 [--observerfile tests/fixtures/observers.ini]
```

`--headless` runs the same app with no terminal UI, for use as a service. Status-log lines and a summary once a minute go to stderr through `logging`. SIGTERM or SIGINT does a clean exit that sends a `stopping` heartbeat, and losing the SBS source makes it exit 1 so a supervisor restarts it. This is how the cue tasker runs on the ADS-B Pi (see **Lab deployment**).

Playback rewrites message timestamps to the current time by default (`--no-rebase-timestamps` turns this off). Age-out and purge compare each track's `last_seen` against wall-clock `utc_now()`, so replaying the 2022 fixtures without rebasing makes every track show as aged or get purged.

## Architecture

All code is in `src/adsb_console/`. Dependencies point one way, and only `app.py` imports Textual:

`models` → `transforms` → `tracker` / `bistatic` → `prediction` → `cue` → `cue_config` → `app`, plus `config` for observers and endpoints and `playback` / `relay` as standalone CLIs.

- **models.py**: `BaseStationMessage.parse` splits a 22-field SBS CSV line and raises `ValueError` if it is malformed. `TrackState` holds per-ICAO state and a bounded position history. `ObserverConfig` is the frozen description of a sensor site, including RF receiver parameters (`receiver_gain_dbi`, `noise_figure_db`, `bandwidth_mhz`) that the bistatic model uses.
- **transforms.py**: WGS-84 LLA→ECEF→observer ENU conversion, range/az/el, range rate, Doppler, CPA, and `is_observable_by`. That last gate always passes for local observers; remote observers are checked against range, az/el extents, and horizon.
- **tracker.py**: `BaseStationTracker` keeps a dict keyed by ICAO. Each `update()` also purges stale tracks relative to the message timestamp (the default retention is 20 min). `observed_tracks()` projects tracks into observer frames, filtered by each observer's `seek_pattern` regex, and returns `ObservedTrack` records.
- **bistatic.py**: loads DTV emitters from `20_DTV_direct_path_input.csv` (FCC-derived; EIRP in kW) and filters them by band (UHF by default). `bistatic_snr_breakdown` itemizes the bistatic radar equation in dB so that the fields add up to `snr_db`. The receive gain, noise figure, and bandwidth come from the receiver `ObserverConfig`; RCS, polarization loss, and system loss are module defaults. `top_bistatic_measurements` keeps only one emitter per physical tower (`tower_key`: the ASRN, or a geographic key if there is none) so the ranked opportunities are geographically distinct.
- **prediction.py**: the cueing logic, with no UI dependencies. `build_track_prediction` propagates a track at constant velocity over `prediction_horizon_s`, sampling every `prediction_sample_interval_s`. For every observer × DTV emitter pair it computes bistatic range, range rate, Doppler, and SNR for each sample, then extracts `ObservationWindow`s where SNR clears `detection_threshold_db` (default −10 dB, pre-integration) and the geometry gates pass. Window boundaries are refined between samples, and every window records an entry and exit reason. `PredictionTriggerEvaluator` decides when to regenerate a prediction: on a new track, a maneuver, a prediction-error threshold, or when the prediction ages out. `PredictionRevisionManager` hands out revision tokens so that a stale prediction computed in the background is discarded. All thresholds are in `PredictionConfig`.
- **cue.py**: `CueSerializer` turns predictions into CT 2.0.0 messages (ms times, rounding); `CueFramer` and `decode_datagram` handle the plain and compressed framing. `UdpCuePublisher` sends them as unicast or multicast UDP with sequence numbers and a UUID for each message. Every datagram must fit `maximum_datagram_bytes`. A track cue that doesn't fit sheds its weakest opportunities (`shed_opportunity_count`); any other oversize message is dropped. Failures and oversize messages feed into the heartbeat's `degraded` status.
- **cue_config.py**: loads and validates the `--cue-config` JSON into `CueRuntimeConfig`, which holds the publication mode, `PredictionConfig`, and `UdpOutputConfig`.
- **config.py**: parses the observer INI format (keys such as `locLat_locLon_locAlt`, `minR_maxR`, `seekPattern`, `receiverGainDbi`/`Gr`, and similar). `ensure_local_observer` guarantees that at least one observer is local. The built-in defaults are the MathWorks Apple Hill lot (local) and the CBS tower (remote), and the comments explaining the chosen RF values are intentional.
- **app.py**: `ADSBConsoleApp` runs one worker (`_monitor_source`) that reads the TCP stream line by line and feeds the tracker. It redraws the table only on the refresh interval, not on every message. The app has two screen modes: `OBSERVER_TABLE`, one row per track for the selected observer, with BiSNR columns computed only for the closest N displayed tracks; and `TRACK_FOCUS_TABLE`, one row per observer for the selected ICAO. The filtering, sorting, and row-formatting helpers are module-level pure functions (`filter_display_tracks`, `sort_display_tracks`, `track_focus_row`, `bisnr_breakdown_table`, etc.), and `tests/test_app.py` tests those directly instead of driving the Textual UI. Put new display logic in functions like these too.
  **Worker groups:** Textual's `run_worker(..., exclusive=True)` cancels *every* worker in the same group, not just ones with the same name. Any exclusive worker therefore needs its own `group=`. Otherwise it silently cancels the `source-monitor` SBS reader, and the app keeps running but stops receiving ADS-B. `test_cue_snapshot_worker_does_not_cancel_source_monitor` guards this.
  Cueing in the app works like this. Each tracker update may schedule a prediction, and a 1 s timer also schedules periodic ones. `build_track_prediction` and every publisher call run in `asyncio.to_thread`, so the UI isn't blocked. In `automatic` mode each committed prediction is published. In `manual` mode (toggle with `m`), only the selected track is sent, and only when the user presses `Q` (uppercase; lowercase `q` quits). Timers send the heartbeat and periodic full snapshots, and a withdrawal (`reason: track_purged`) is published for any previously published track that gets purged. Withdrawals happen only at purge time (`--track-retention-minutes`, default 20 min), not when a track ages out after 20 s, so the collection system has to rely on each cue's `valid_until_utc` for tracks that go stale.

Tests use small in-code fixtures plus `tests/fixtures/` (a basestation sample, the observers INI, and gzipped real SBS captures).

## Lab deployment

The cue tasker runs on the ADS-B Raspberry Pi and publishes to the RF collection desktop. Everything below was set up and checked through a Pi reboot on 2026-09-25. The system-level record is flightTest `docs/system/As_Built.md`; keep the two in step.

**Hosts** (both on the data collection network, 192.168.10.0/24; the N320 is 192.168.10.2):
- **RF collection desktop** `rf-lenovo-mw` (Ubuntu 26.04): `eno1` 192.168.10.41 is the data network, the internet comes over Wi-Fi `wlp2s0`, and ZeroTier is 172.25.20.164. It consumes the cues. sudo needs a password, so the user runs desktop sudo commands.
- **ADS-B Pi** `pi2@192.168.10.131` (Pi 4, Debian 11): dump1090 serves SBS on `:30003`, and ZeroTier is 172.25.127.167 (network `12ac4a1e71f93ac3`). `pi2` has passwordless sudo. SSH from the desktop uses `~/.ssh/id_ed25519_flighttest`, set in `~/.ssh/config`.

**Network:** the Pi reaches the internet only through the desktop.
- **Desktop side:** `/etc/sysctl.d/90-pi-gateway.conf` turns on forwarding, and `pi-gateway-nat.service`, a oneshot unit, adds `MASQUERADE -s 192.168.10.131 -o wlp2s0` if it's missing. If the desktop's internet moves off Wi-Fi, edit `-o wlp2s0` in that unit. ufw is installed but disabled.
- **Pi side:** `/etc/dhcpcd.conf` sets `static routers=192.168.10.41` and DNS `8.8.8.8 144.212.95.8`. The original is saved as `/etc/dhcpcd.conf.bak-20260926`. The old gateway, 192.168.10.1, does not exist.
- **Why the Pi needs internet:** chrony keeps time only from internet NTP, because the GPS/PPS refclocks aren't locked. Without NTP the Pi drifted about 15 s behind, which shifts every cue timestamp and all ADS-B truth. ZeroTier and `uv` also need internet.

**Service:**
- **Install:** the repo is at `~/flightTest/ADSB-remoter` on branch `feature/passive-radar-cueing`, and uv is `~/.local/bin/uv`.
- **Update:** `git pull --ff-only && ~/.local/bin/uv sync --no-dev && sudo systemctl restart adsb-cue`. If `deploy/adsb-cue.service` changed, first copy it to `/etc/systemd/system/` and run `sudo systemctl daemon-reload`.
- **What the unit runs:** `.venv/bin/adsb-console --headless` with `deploy/pi-observers.ini` and `deploy/pi-cue-config.json` (multicast `239.192.10.1:31986`, `source_address` pinned to 192.168.10.131).
  - The observer file holds only the surveyed receive site. Its RF values are explicit because the INI loader defaults `receiverGainDbi` to 0.
- **Boot order:** the unit uses `Restart=on-failure`. At boot it starts before dump1090 listens, exits 1, and connects on the restart 10 s later. That's expected.
- **Logs:** use `sudo journalctl -u adsb-cue`, because `pi2` can't read the journal without sudo. The Pi's journald keeps only notice and above, so the unit sets `SyslogLevel=notice`.
- **CPU:** a prediction took about 407 ms on the Pi with 2 observers × 16 UHF emitters, about 5× slower than the desktop. Predictions are pure Python and limited to about one core, so keep observers and emitters lean.

**Monitoring on the desktop:** join the multicast group on `192.168.10.41`, because the desktop's default route is Wi-Fi. netcat can't join multicast groups.
- **Watch or check the stream:** `uv run python tools/cue_capture.py --bind 0.0.0.0:31986 --multicast-group 239.192.10.1 --multicast-interface 192.168.10.41 --duration-s 60`.
  - It decodes both framings, validates against the schemas, and summarises gaps, snapshots, sizes and one-frame fit.
  - Add `--print` to stream the decoded messages as JSON lines (`| jq -c .`).
- **socat, one message per line:** use `UDP4-RECVFROM` with `fork`, so each datagram gets its own child process and becomes one base64 line. `tools/cue_decode.py` then decodes either framing:
  ```
  socat -u UDP4-RECVFROM:31986,reuseaddr,ip-add-membership=239.192.10.1:192.168.10.41,fork SYSTEM:'base64 -w0; echo' | .venv/bin/python tools/cue_decode.py | jq -c .
  ```
  Don't put the decoder inside `SYSTEM:`, because socat parses commas and colons in the command. Plain `UDP4-RECV ... STDOUT` concatenates datagrams, which only works for plain-JSON runs.
- **`degraded` heartbeats:** this status means the SBS feed has been silent for more than 20 s. That's common when few aircraft are in view, and ADS-B reception at the Pi has been thin.
