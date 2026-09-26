from __future__ import annotations

import asyncio
import logging
import re
import socket
from datetime import datetime, timedelta

import pytest
from textual.binding import Binding
from textual.worker import WorkerState

from adsb_console.app import (
    ADSBConsoleApp,
    TrackFocusSnapshot,
    bisnr_breakdown_table,
    bistatic_tower_log_table,
    cue_config_with_overrides,
    cue_heartbeat_status,
    filter_display_tracks,
    main_display_bistatic_by_icao,
    next_refresh_interval_s,
    plain_log_text,
    refresh_interval_s,
    track_focus_row,
    track_focus_status,
)
from adsb_console.bistatic import (
    BistaticMeasurement,
    BistaticSnrBreakdown,
    DtvEmitter,
    top_bistatic_measurements,
)
from adsb_console.cue import UdpOutputConfig
from adsb_console.cue_config import CueRuntimeConfig, load_cue_runtime_config
from adsb_console.models import ObserverConfig, ObserverRole, PositionReport, TrackState
from adsb_console.prediction import PredictionConfig
from adsb_console.tracker import ObservedTrack
from adsb_console.transforms import ClosestPointOfApproach, RangeAzEl


def test_manual_cue_and_mode_bindings_preserve_lowercase_quit() -> None:
    bindings = {
        binding.key: binding.action for binding in Binding.make_bindings(ADSBConsoleApp.BINDINGS)
    }

    assert bindings["q"] == "quit"
    assert bindings["shift+q"] == "publish_selected_cue"
    assert bindings["m"] == "toggle_cue_mode"


def test_cue_heartbeat_status_tracks_startup_health_and_shutdown() -> None:
    now = datetime(2026, 9, 25, 18, 0, 0)
    assert cue_heartbeat_status(
        has_published_heartbeat=False,
        stopping=False,
        last_sbs_input_utc=now,
        now=now,
        maximum_adsb_report_age_s=20.0,
        recent_publish_failure=False,
        recent_oversize=False,
    ) == "starting"
    assert cue_heartbeat_status(
        has_published_heartbeat=True,
        stopping=False,
        last_sbs_input_utc=now,
        now=now,
        maximum_adsb_report_age_s=20.0,
        recent_publish_failure=False,
        recent_oversize=False,
    ) == "running"
    assert cue_heartbeat_status(
        has_published_heartbeat=True,
        stopping=False,
        last_sbs_input_utc=now - timedelta(seconds=21),
        now=now,
        maximum_adsb_report_age_s=20.0,
        recent_publish_failure=False,
        recent_oversize=False,
    ) == "degraded"
    assert cue_heartbeat_status(
        has_published_heartbeat=True,
        stopping=False,
        last_sbs_input_utc=now,
        now=now,
        maximum_adsb_report_age_s=20.0,
        recent_publish_failure=True,
        recent_oversize=False,
    ) == "degraded"
    assert cue_heartbeat_status(
        has_published_heartbeat=True,
        stopping=False,
        last_sbs_input_utc=now,
        now=now,
        maximum_adsb_report_age_s=20.0,
        recent_publish_failure=False,
        recent_oversize=True,
    ) == "degraded"
    assert cue_heartbeat_status(
        has_published_heartbeat=True,
        stopping=True,
        last_sbs_input_utc=now,
        now=now,
        maximum_adsb_report_age_s=20.0,
        recent_publish_failure=False,
        recent_oversize=False,
    ) == "stopping"


def test_app_constructs_without_textual_attribute_collisions() -> None:
    app = ADSBConsoleApp(source=("127.0.0.1", 28887))

    assert app.source == ("127.0.0.1", 28887)
    assert app.event_log is None
    assert app.tracker.message_count == 0
    assert app.selected_observer.is_local
    assert app.refresh_interval_s == 10.0
    assert app.tracker.stale_track_seconds == 20.0 * 60.0
    assert len(app.observers) == 2
    assert app.observers[0].name == "MathWorks Apple Hill Parking Lot"
    assert app.observers[1].name == "CBS Broadcast Tower"


def test_filter_display_tracks_counts_hidden_by_range_regex_and_row_limit() -> None:
    tracks = [
        _observed_track("A00001", 10_000.0),
        _observed_track("B00002", 20_000.0),
        _observed_track("A00003", 300_000.0),
    ]

    result = filter_display_tracks(
        observed_tracks=tracks,
        icao_filter=re.compile("^A"),
        max_range_km=100.0,
        max_rows=1,
    )

    assert [track.icao for track in result.visible_tracks] == ["A00001"]
    assert result.visible_count == 1
    assert result.observer_track_count == 3
    assert result.hidden_count == 2


def test_refresh_interval_from_rate() -> None:
    assert refresh_interval_s(0.5) == 2.0
    assert refresh_interval_s(0.0) == 0.0
    assert refresh_interval_s(0.1) == 10.0


def test_next_refresh_interval_cycles_choices() -> None:
    assert next_refresh_interval_s(2.0) == 5.0
    assert next_refresh_interval_s(5.0) == 10.0
    assert next_refresh_interval_s(10.0) == 30.0
    assert next_refresh_interval_s(30.0) == 2.0


def test_filter_display_tracks_sorts_by_range_before_row_limit() -> None:
    tracks = [
        _observed_track("FAR", 300_000.0),
        _observed_track("NEAR", 10_000.0),
        _observed_track("MID", 20_000.0),
    ]

    result = filter_display_tracks(
        observed_tracks=tracks,
        icao_filter=None,
        max_range_km=None,
        max_rows=2,
        track_lookup={},
        sort_column="Rng km",
        now=datetime.now(),
        hide_aged_tracks=False,
    )

    assert [track.icao for track in result.visible_tracks] == ["NEAR", "MID"]
    assert result.hidden_count == 1


def test_filter_display_tracks_sorts_by_message_count_before_row_limit() -> None:
    now = datetime.now()
    tracks = [
        _observed_track("LOW", 10_000.0),
        _observed_track("HIGH", 20_000.0),
    ]

    result = filter_display_tracks(
        observed_tracks=tracks,
        icao_filter=None,
        max_range_km=None,
        max_rows=1,
        track_lookup={
            "LOW": _track_state("LOW", now, message_count=1),
            "HIGH": _track_state("HIGH", now, message_count=99),
        },
        sort_column="Msgs",
        now=now,
    )

    assert [track.icao for track in result.visible_tracks] == ["HIGH"]
    assert result.hidden_count == 1


def test_filter_display_tracks_hides_aged_tracks_by_default() -> None:
    now = datetime.now()
    tracks = [
        _observed_track("FRESH", 10_000.0),
        _observed_track("AGED", 20_000.0),
    ]
    track_lookup = {
        "FRESH": _track_state("FRESH", now, message_count=1),
        "AGED": _track_state("AGED", now - timedelta(seconds=30), message_count=1),
    }

    result = filter_display_tracks(
        observed_tracks=tracks,
        icao_filter=None,
        max_range_km=None,
        max_rows=None,
        track_lookup=track_lookup,
        sort_column="Rng km",
        now=now,
        age_out_seconds=20.0,
    )

    assert [track.icao for track in result.visible_tracks] == ["FRESH"]
    assert result.observer_track_count == 2
    assert result.hidden_count == 1


def test_filter_display_tracks_can_show_aged_tracks() -> None:
    now = datetime.now()
    tracks = [
        _observed_track("FRESH", 10_000.0),
        _observed_track("AGED", 20_000.0),
    ]

    result = filter_display_tracks(
        observed_tracks=tracks,
        icao_filter=None,
        max_range_km=None,
        max_rows=None,
        track_lookup={
            "FRESH": _track_state("FRESH", now, message_count=1),
            "AGED": _track_state("AGED", now - timedelta(seconds=30), message_count=1),
        },
        sort_column="Rng km",
        now=now,
        hide_aged_tracks=False,
        age_out_seconds=20.0,
    )

    assert [track.icao for track in result.visible_tracks] == ["FRESH", "AGED"]
    assert result.hidden_count == 0


def test_track_focus_status_reports_aged_and_dropped() -> None:
    now = datetime.now()
    track = _track_state("FOCUS", now - timedelta(seconds=30), message_count=1)

    aged = TrackFocusSnapshot(
        icao="FOCUS",
        track=track,
        observed_tracks=[],
        captured_at=now,
        dropped=False,
    )
    dropped = TrackFocusSnapshot(
        icao="FOCUS",
        track=track,
        observed_tracks=[],
        captured_at=now,
        dropped=True,
    )

    assert track_focus_status(aged, now, age_out_seconds=20.0) == "aged-out"
    assert track_focus_status(dropped, now, age_out_seconds=20.0) == "dropped"


def test_track_focus_row_formats_cpa_values() -> None:
    emitter = DtvEmitter(
        facility_id="1",
        call_sign="WBZ-TV",
        site_name="CBS Tower",
        asrn="100",
        rf_channel=20,
        center_frequency_mhz=509.0,
        latitude_deg=42.0,
        longitude_deg=-71.0,
        altitude_m=400.0,
        eirp_kw=1000.0,
    )
    observed_track = ObservedTrack(
        observer_name="LongObserverName",
        icao="FOCUS",
        callsign=None,
        range_az_el=RangeAzEl(range_m=12_345.0, azimuth_deg=123.4, elevation_deg=5.0),
        range_rate_mps=-10.5,
        doppler_hz=21.0,
        cpa=ClosestPointOfApproach(range_m=2_000.0, bearing_deg=90.0, time_s=-5.0),
        reported_at=datetime.now(),
    )
    bistatic = BistaticMeasurement(
        emitter=emitter,
        snr_db=12.4,
        snr_breakdown=_breakdown(12.4),
        bistatic_range_km=34.5,
        bistatic_doppler_hz=-67.8,
        bearing_to_emitter_deg=123.4,
    )

    assert track_focus_row(observed_track, [bistatic]) == (
        "LongObserv",
        "12.3",
        "-10.5",
        "123.4",
        "21.0",
        "2.0",
        "90.0",
        "-5.0",
        "WBZ-TV: 12dB 34km -68Hz",
        "",
        "",
        "",
        "",
    )


def test_bistatic_tower_log_uses_pretty_table_format() -> None:
    receiver = _observer("MathWorks Apple Hill Parking Lot")
    emitter = DtvEmitter(
        facility_id="1",
        call_sign="WBZ-TV",
        site_name="CBS Tower",
        asrn="100",
        rf_channel=20,
        center_frequency_mhz=509.0,
        latitude_deg=42.0,
        longitude_deg=-71.0,
        altitude_m=400.0,
        eirp_kw=1000.0,
    )
    measurement = BistaticMeasurement(
        emitter=emitter,
        snr_db=12.4,
        snr_breakdown=_breakdown(12.4),
        bistatic_range_km=34.5,
        bistatic_doppler_hz=-67.8,
        bearing_to_emitter_deg=123.4,
    )

    table = bistatic_tower_log_table(receiver, [measurement])

    assert "DTV towers" in table
    assert "+------------+--------+-----------+-----+-----+" in table
    assert "| Observer   | Call   | Site      | MHz | Brg |" in table
    assert "| MathWorks  | WBZ-TV | CBS Tower | 509 | 123 |" in table
    assert "Call:" not in table


def test_bisnr_breakdown_table_has_one_row_per_observer() -> None:
    now = datetime.now()
    emitter = DtvEmitter(
        facility_id="1",
        call_sign="WBZ-TV",
        site_name="CBS Tower",
        asrn="100",
        rf_channel=20,
        center_frequency_mhz=509.0,
        latitude_deg=0.0,
        longitude_deg=0.02,
        altitude_m=100.0,
        eirp_kw=1000.0,
    )
    observer_a = _observer("Alpha")
    observer_b = ObserverConfig(
        name="Bravo",
        role=ObserverRole.REMOTE,
        latitude_deg=0.0,
        longitude_deg=0.0,
        altitude_m=0.0,
        receiver_gain_dbi=6.0,
    )
    position = _position(0.0, 0.01, now)

    table_text = bisnr_breakdown_table([observer_a, observer_b], [emitter], position, None)

    assert "Alpha" in table_text
    assert "Bravo" in table_text
    assert "SNR" in table_text

    # Cross-check against the same computation the main table uses, so the
    # breakdown table can't silently drift from the numbers it explains.
    expected = top_bistatic_measurements(
        emitters=[emitter],
        receiver=observer_b,
        position=position,
        velocity=None,
        count=1,
    )[0]
    assert f"{expected.snr_db:.1f}" in table_text


def test_bisnr_breakdown_table_handles_observer_with_no_emitters() -> None:
    position = _position(0.0, 0.0, datetime.now())

    table_text = bisnr_breakdown_table([_observer("Alpha")], [], position, None)

    assert "Alpha" in table_text


def test_main_display_bistatic_only_computes_closest_track_limit() -> None:
    now = datetime.now()
    receiver = _observer("receiver")
    emitter = DtvEmitter(
        facility_id="1",
        call_sign="WBZ-TV",
        site_name="CBS Tower",
        asrn="100",
        rf_channel=20,
        center_frequency_mhz=509.0,
        latitude_deg=0.02,
        longitude_deg=0.0,
        altitude_m=100.0,
        eirp_kw=1000.0,
    )
    tracks = [
        _observed_track("FAR", 300_000.0),
        _observed_track("NEAR", 10_000.0),
    ]
    track_lookup = {
        "FAR": _track_state("FAR", now, message_count=1),
        "NEAR": _track_state("NEAR", now, message_count=1),
    }
    track_lookup["FAR"].last_position = _position(0.3, 0.0, now)
    track_lookup["NEAR"].last_position = _position(0.01, 0.0, now)

    results = main_display_bistatic_by_icao(
        tracks,
        track_lookup,
        receiver,
        [emitter],
        track_limit=1,
    )

    assert set(results) == {"NEAR"}
    assert len(results["NEAR"]) == 1


def _observed_track(icao: str, range_m: float) -> ObservedTrack:
    return ObservedTrack(
        observer_name="observer",
        icao=icao,
        callsign=None,
        range_az_el=RangeAzEl(range_m=range_m, azimuth_deg=0.0, elevation_deg=0.0),
        range_rate_mps=None,
        doppler_hz=None,
        cpa=None,
        reported_at=datetime.now(),
    )


def _track_state(icao: str, last_seen: datetime, message_count: int) -> TrackState:
    return TrackState(
        icao=icao,
        first_seen=last_seen,
        last_seen=last_seen,
        message_count=message_count,
    )


def _observer(name: str) -> ObserverConfig:
    return ObserverConfig(
        name=name,
        role=ObserverRole.LOCAL,
        latitude_deg=0.0,
        longitude_deg=0.0,
        altitude_m=0.0,
        receiver_gain_dbi=10.0,
    )


def _position(latitude_deg: float, longitude_deg: float, reported_at: datetime) -> PositionReport:
    return PositionReport(
        latitude_deg=latitude_deg,
        longitude_deg=longitude_deg,
        altitude_ft=1000.0,
        reported_at=reported_at,
    )


def _breakdown(snr_db: float) -> BistaticSnrBreakdown:
    """Stand-in breakdown for tests that only care about the total SNR."""
    return BistaticSnrBreakdown(
        eirp_dbw=0.0,
        receiver_gain_dbi=0.0,
        wavelength_gain_db=0.0,
        rcs_dbsm=0.0,
        spreading_loss_db=0.0,
        tx_range_loss_db=0.0,
        rx_range_loss_db=0.0,
        noise_floor_db=0.0,
        polarization_loss_db=0.0,
        system_loss_db=0.0,
        snr_db=snr_db,
    )


def test_plain_log_text_strips_markup_and_tolerates_bad_markup() -> None:
    assert plain_log_text("[red]Connection failed:[/] refused") == "Connection failed: refused"
    assert plain_log_text("[/red] unbalanced") == "[/red] unbalanced"


async def test_headless_app_exits_nonzero_when_source_is_unreachable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        unused_port = probe.getsockname()[1]
    app = ADSBConsoleApp(source=("127.0.0.1", unused_port), headless=True)

    with caplog.at_level(logging.INFO, logger="adsb_console.app"):
        async with app.run_test() as pilot:
            await pilot.pause(0.5)

    assert app.return_code == 1
    assert any("Connection failed" in record.getMessage() for record in caplog.records)
    assert all("[red]" not in record.getMessage() for record in caplog.records)


async def test_cue_snapshot_worker_does_not_cancel_source_monitor() -> None:
    connections: list[asyncio.StreamWriter] = []

    async def hold_open(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        connections.append(writer)

    server = await asyncio.start_server(hold_open, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    config = CueRuntimeConfig(
        prediction=PredictionConfig(enabled=True),
        udp_output=UdpOutputConfig(enabled=True, destination_port=port),
    )
    app = ADSBConsoleApp(source=("127.0.0.1", port), cue_runtime_config=config)
    try:
        async with app.run_test() as pilot:
            await pilot.pause(0.2)
            app._schedule_snapshot()  # pyright: ignore[reportPrivateUsage]
            await pilot.pause(0.2)
            states = {worker.name: worker.state for worker in app.workers}
            assert states["source-monitor"] is WorkerState.RUNNING
    finally:
        for writer in connections:
            writer.close()
        server.close()
        await server.wait_closed()


def test_cue_startup_overrides_set_framing_and_summary_for_the_run() -> None:
    base = load_cue_runtime_config("deploy/pi-cue-config.json")

    plain = cue_config_with_overrides(base, encoding="json", include_summary=True)
    unchanged = cue_config_with_overrides(base, encoding=None, include_summary=False)

    assert base.udp_output.encoding == "deflate_dictionary"
    assert plain.udp_output.encoding == "json"
    assert plain.include_summary is True
    assert unchanged == base
    with pytest.raises(ValueError, match="Unknown cue encoding"):
        cue_config_with_overrides(base, encoding="gzip", include_summary=False)
