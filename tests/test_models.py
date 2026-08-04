from __future__ import annotations

from datetime import datetime

from adsb_console.models import BaseStationMessage, TransmissionType


def test_parse_standard_position_message() -> None:
    message = BaseStationMessage.parse(
        "MSG,3,1,1,A5CDE9,1,2025/08/12,12:20:30.797,2025/08/12,12:20:30.827,,"
        "5200,,,42.33270,-71.35499,,,0,,0,0"
    )

    assert message.message_type == "MSG"
    assert message.transmission_type == TransmissionType.ES_AIRBORNE_POSITION
    assert message.icao == "A5CDE9"
    assert message.generated_at == datetime(2025, 8, 12, 12, 20, 30, 797000)
    assert message.altitude_ft == 5200.0
    assert message.latitude_deg == 42.33270
    assert message.longitude_deg == -71.35499

    position = message.position_report()
    assert position is not None
    assert position.altitude_m == 1584.96


def test_parse_combined_date_time_variant() -> None:
    message = BaseStationMessage.parse(
        "MSG,8,1,1,A15717,1,2025/05/01 07:15:23.797,07:15:23.797,"
        "2025/05/01 07:15:23.850,07:15:23.850,,,,,,,,,,,,0"
    )

    assert message.generated_at == datetime(2025, 5, 1, 7, 15, 23, 797000)
    assert message.logged_at == datetime(2025, 5, 1, 7, 15, 23, 850000)


def test_rebase_timestamps() -> None:
    message = BaseStationMessage.parse(
        "MSG,3,1,1,A5CDE9,1,2025/08/12,12:20:30.797,2025/08/12,12:20:30.827,,"
        "5200,,,42.33270,-71.35499,,,0,,0,0"
    )

    rebased = message.with_rebased_timestamps(datetime(2026, 8, 4, 17, 1, 2, 345678))

    assert rebased.fields[6] == "2026/08/04"
    assert rebased.fields[7] == "17:01:02.345"
    assert rebased.fields[8] == "2026/08/04"
    assert rebased.fields[9] == "17:01:02.345"
