from __future__ import annotations

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
