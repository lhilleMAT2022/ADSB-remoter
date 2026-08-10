from __future__ import annotations

import re
from datetime import datetime, timedelta

from adsb_console.app import (
    ADSBConsoleApp,
    TrackFocusSnapshot,
    bistatic_tower_log_table,
    filter_display_tracks,
    main_display_bistatic_by_icao,
    refresh_interval_s,
    track_focus_row,
    track_focus_status,
)
from adsb_console.bistatic import BistaticMeasurement, DtvEmitter
from adsb_console.models import ObserverConfig, ObserverRole, PositionReport, TrackState
from adsb_console.tracker import ObservedTrack
from adsb_console.transforms import ClosestPointOfApproach, RangeAzEl


def test_app_constructs_without_textual_attribute_collisions() -> None:
    app = ADSBConsoleApp(source=("127.0.0.1", 28887))

    assert app.source == ("127.0.0.1", 28887)
    assert app.event_log is None
    assert app.tracker.message_count == 0
    assert app.selected_observer.is_local
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
        "WBZ-TV @123°: 12dB 34km -68Hz",
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
        receiver_gain_dbi=30.0,
    )


def _position(latitude_deg: float, longitude_deg: float, reported_at: datetime) -> PositionReport:
    return PositionReport(
        latitude_deg=latitude_deg,
        longitude_deg=longitude_deg,
        altitude_ft=1000.0,
        reported_at=reported_at,
    )
