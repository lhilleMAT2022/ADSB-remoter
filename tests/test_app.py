from __future__ import annotations

import re
from datetime import datetime

from adsb_console.app import ADSBConsoleApp, filter_display_tracks, refresh_interval_s
from adsb_console.tracker import ObservedTrack
from adsb_console.transforms import RangeAzEl


def test_app_constructs_without_textual_attribute_collisions() -> None:
    app = ADSBConsoleApp(source=("127.0.0.1", 28887))

    assert app.source == ("127.0.0.1", 28887)
    assert app.event_log is None
    assert app.tracker.message_count == 0
    assert app.selected_observer.is_local
    assert len(app.observers) == 2
    assert app.observers[0].name == "Goat Island Lighthouse"
    assert app.observers[1].name == "MathWorks Apple Hill"


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


def _observed_track(icao: str, range_m: float) -> ObservedTrack:
    return ObservedTrack(
        observer_name="observer",
        icao=icao,
        callsign=None,
        range_az_el=RangeAzEl(range_m=range_m, azimuth_deg=0.0, elevation_deg=0.0),
        reported_at=datetime.now(),
    )
