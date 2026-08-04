from __future__ import annotations

from math import isclose

from adsb_console.models import ObserverConfig
from adsb_console.transforms import WGS84_A_M, ecef_to_enu, enu_to_range_az_el, lla_to_ecef


def test_lla_to_ecef_equator_prime_meridian() -> None:
    point = lla_to_ecef(0.0, 0.0, 0.0)

    assert isclose(point.x_m, WGS84_A_M, abs_tol=1e-6)
    assert isclose(point.y_m, 0.0, abs_tol=1e-6)
    assert isclose(point.z_m, 0.0, abs_tol=1e-6)


def test_nearby_east_target_has_east_azimuth() -> None:
    observer = ObserverConfig(name="origin", latitude_deg=0.0, longitude_deg=0.0, altitude_m=0.0)
    target = lla_to_ecef(0.0, 0.01, 0.0)
    enu = ecef_to_enu(target, observer)
    range_az_el = enu_to_range_az_el(enu)

    assert enu.east_m > 0.0
    assert isclose(range_az_el.azimuth_deg, 90.0, abs_tol=0.01)
    assert range_az_el.range_m > 1000.0
