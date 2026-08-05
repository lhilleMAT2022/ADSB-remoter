from __future__ import annotations

import re
from datetime import datetime, timedelta

from adsb_console.app import ADSBConsoleApp, filter_display_tracks, refresh_interval_s
from adsb_console.models import TrackState
from adsb_console.tracker import ObservedTrack
from adsb_console.transforms import RangeAzEl


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


def _observed_track(icao: str, range_m: float) -> ObservedTrack:
    return ObservedTrack(
        observer_name="observer",
        icao=icao,
        callsign=None,
        range_az_el=RangeAzEl(range_m=range_m, azimuth_deg=0.0, elevation_deg=0.0),
        range_rate_mps=None,
        doppler_hz=None,
        reported_at=datetime.now(),
    )


def _track_state(icao: str, last_seen: datetime, message_count: int) -> TrackState:
    return TrackState(
        icao=icao,
        first_seen=last_seen,
        last_seen=last_seen,
        message_count=message_count,
    )
