from __future__ import annotations

from adsb_console.config import (
    default_observers,
    ensure_local_observer,
    load_observers,
    load_observers_or_default,
    parse_endpoint,
    parse_endpoint_list,
)
from adsb_console.models import ObserverConfig, ObserverRole


def test_parse_endpoint() -> None:
    assert parse_endpoint("127.0.0.1:28887") == ("127.0.0.1", 28887)
    assert parse_endpoint_list("127.0.0.1:1,localhost:2") == [
        ("127.0.0.1", 1),
        ("localhost", 2),
    ]


def test_load_observers() -> None:
    observers = load_observers("tests/fixtures/observers.ini")

    assert len(observers) == 2
    assert observers[0].name == "FLEXDAR"
    assert observers[0].role is ObserverRole.LOCAL
    assert observers[0].latitude_deg == 41.576006
    assert observers[0].max_range_m == 40000.0
    assert observers[0].seek_pattern == "A.*"
    assert observers[1].name == "REMOTE_NORTH"
    assert observers[1].role is ObserverRole.REMOTE
    assert observers[1].min_azimuth_deg == 0.0
    assert observers[1].max_azimuth_deg == 180.0
    assert observers[1].report_endpoint == "127.0.0.1:63542"


def test_ensure_local_observer_adds_local_from_first_remote() -> None:
    remote = ObserverConfig(
        name="Remote",
        role=ObserverRole.REMOTE,
        latitude_deg=1.0,
        longitude_deg=2.0,
        altitude_m=3.0,
    )

    observers = ensure_local_observer([remote])

    assert len(observers) == 2
    assert observers[0].is_local
    assert observers[0].latitude_deg == 1.0
    assert observers[1] is remote


def test_default_observers_are_deployment_observers() -> None:
    observers = default_observers()

    assert len(observers) == 2
    assert observers[0].name == "MathWorks Apple Hill Parking Lot"
    assert observers[0].role is ObserverRole.LOCAL
    assert observers[0].latitude_deg == 42.299350798761694
    assert observers[0].longitude_deg == -71.34948267330608
    assert observers[0].altitude_m == 75.0
    assert observers[0].receiver_gain_dbi == 10.0
    assert observers[0].noise_figure_db == 3.0
    assert observers[0].bandwidth_mhz == 8.0
    assert observers[1].name == "CBS Broadcast Tower"
    assert observers[1].role is ObserverRole.REMOTE
    assert observers[1].latitude_deg == 42.308259720042415
    assert observers[1].longitude_deg == -71.21565924108836
    assert observers[1].altitude_m == 350.0
    assert load_observers_or_default(None) == observers
