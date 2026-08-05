from __future__ import annotations

from adsb_console.models import ObserverConfig, ObserverRole
from adsb_console.tracker import BaseStationTracker


def test_tracker_accumulates_position_and_velocity() -> None:
    tracker = BaseStationTracker()

    position_track = tracker.update_line(
        "MSG,3,1,1,A5CDE9,1,2025/08/12,12:20:30.797,2025/08/12,12:20:30.827,,"
        "5200,,,42.33270,-71.35499,,,0,,0,0"
    )
    velocity_track = tracker.update_line(
        "MSG,4,1,1,A5CDE9,1,2025/08/12,12:20:30.797,2025/08/12,12:20:30.827,,,92,206,,,-384,,,,,0"
    )

    assert position_track is velocity_track
    assert position_track is not None
    assert position_track.message_count == 2
    assert position_track.subtype_counts == {3: 1, 4: 1}
    assert position_track.last_position is not None
    assert position_track.last_velocity is not None
    assert position_track.last_velocity.ground_speed_kt == 92.0


def test_tracker_rejects_non_msg_lines() -> None:
    tracker = BaseStationTracker()

    assert tracker.update_line("STA,,,,") is None
    assert tracker.message_count == 0
    assert tracker.invalid_count == 1


def test_local_observer_projects_all_positioned_tracks_without_remote_gates() -> None:
    tracker = BaseStationTracker()
    tracker.update_line(
        "MSG,3,1,1,A5CDE9,1,2025/08/12,12:20:30.797,2025/08/12,12:20:30.827,,"
        "5200,,,42.33270,-71.35499,,,0,,0,0"
    )
    observer = ObserverConfig(
        name="local",
        role=ObserverRole.LOCAL,
        latitude_deg=42.0,
        longitude_deg=-71.0,
        altitude_m=0.0,
        seek_pattern="NO_MATCH",
        min_range_m=1_000_000.0,
        max_range_m=1_000_001.0,
    )

    observed = tracker.observed_tracks([observer], carrier_frequency_hz=600e6)

    assert len(observed) == 1
    assert observed[0].icao == "A5CDE9"


def test_remote_observer_specific_icao_regex_can_include_track_outside_geometry() -> None:
    tracker = BaseStationTracker()
    tracker.update_line(
        "MSG,3,1,1,A5CDE9,1,2025/08/12,12:20:30.797,2025/08/12,12:20:30.827,,"
        "5200,,,42.33270,-71.35499,,,0,,0,0"
    )
    observer = ObserverConfig(
        name="remote",
        role=ObserverRole.REMOTE,
        latitude_deg=42.0,
        longitude_deg=-71.0,
        altitude_m=0.0,
        seek_pattern="A5CDE9",
        min_range_m=1_000_000.0,
        max_range_m=1_000_001.0,
    )

    observed = tracker.observed_tracks([observer], carrier_frequency_hz=600e6)

    assert len(observed) == 1
    assert observed[0].icao == "A5CDE9"


def test_observed_track_includes_range_rate_and_doppler() -> None:
    tracker = BaseStationTracker()
    tracker.update_line(
        "MSG,3,1,1,A5CDE9,1,2025/08/12,12:20:30.797,2025/08/12,12:20:30.827,,"
        "5200,,,42.33270,-71.35499,,,0,,0,0"
    )
    tracker.update_line(
        "MSG,4,1,1,A5CDE9,1,2025/08/12,12:20:30.797,2025/08/12,12:20:30.827,,,92,206,,,-384,,,,,0"
    )
    observer = ObserverConfig(
        name="local",
        role=ObserverRole.LOCAL,
        latitude_deg=42.0,
        longitude_deg=-71.0,
        altitude_m=0.0,
    )

    observed = tracker.observed_tracks([observer], carrier_frequency_hz=600e6)

    assert len(observed) == 1
    assert observed[0].range_rate_mps is not None
    assert observed[0].doppler_hz is not None
