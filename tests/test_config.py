from __future__ import annotations

from adsb_console.config import load_observers, parse_endpoint, parse_endpoint_list


def test_parse_endpoint() -> None:
    assert parse_endpoint("127.0.0.1:28887") == ("127.0.0.1", 28887)
    assert parse_endpoint_list("127.0.0.1:1,localhost:2") == [
        ("127.0.0.1", 1),
        ("localhost", 2),
    ]


def test_load_observers() -> None:
    observers = load_observers("tests/fixtures/observers.ini")

    assert len(observers) == 1
    assert observers[0].name == "FLEXDAR"
    assert observers[0].latitude_deg == 41.576006
    assert observers[0].max_range_m == 40000.0
    assert observers[0].seek_pattern == "A.*"
