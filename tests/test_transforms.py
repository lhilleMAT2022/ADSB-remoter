from __future__ import annotations

from datetime import datetime
from math import isclose

from adsb_console.models import ObserverConfig, ObserverRole, PositionReport, VelocityReport
from adsb_console.transforms import (
    KNOT_TO_METERS_PER_SECOND,
    WGS84_A_M,
    ecef_to_enu,
    enu_to_range_az_el,
    horizon_distance_m,
    is_observable_by,
    lla_to_ecef,
    position_velocity_to_range_rate_mps,
    range_rate_to_doppler_hz,
)


def test_lla_to_ecef_equator_prime_meridian() -> None:
    point = lla_to_ecef(0.0, 0.0, 0.0)

    assert isclose(point.x_m, WGS84_A_M, abs_tol=1e-6)
    assert isclose(point.y_m, 0.0, abs_tol=1e-6)
    assert isclose(point.z_m, 0.0, abs_tol=1e-6)


def test_nearby_east_target_has_east_azimuth() -> None:
    observer = ObserverConfig(
        name="origin",
        role=ObserverRole.LOCAL,
        latitude_deg=0.0,
        longitude_deg=0.0,
        altitude_m=0.0,
    )
    target = lla_to_ecef(0.0, 0.01, 0.0)
    enu = ecef_to_enu(target, observer)
    range_az_el = enu_to_range_az_el(enu)

    assert enu.east_m > 0.0
    assert isclose(range_az_el.azimuth_deg, 90.0, abs_tol=0.01)
    assert range_az_el.range_m > 1000.0


def test_observer_visibility_applies_azimuth_extent() -> None:
    observer = ObserverConfig(
        name="north_only",
        role=ObserverRole.REMOTE,
        latitude_deg=0.0,
        longitude_deg=0.0,
        altitude_m=0.0,
        min_azimuth_deg=315.0,
        max_azimuth_deg=45.0,
    )
    position = PositionReport(
        latitude_deg=0.0,
        longitude_deg=0.01,
        altitude_ft=1000.0,
        reported_at=datetime.now(),
    )
    range_az_el = enu_to_range_az_el(ecef_to_enu(lla_to_ecef(0.0, 0.01, 304.8), observer))

    assert not is_observable_by(position, range_az_el, observer)
    assert horizon_distance_m(position.altitude_m) > 0.0


def test_range_rate_is_radial_component_only() -> None:
    observer = ObserverConfig(
        name="origin",
        role=ObserverRole.LOCAL,
        latitude_deg=0.0,
        longitude_deg=0.0,
        altitude_m=0.0,
    )
    target_due_east = PositionReport(
        latitude_deg=0.0,
        longitude_deg=0.01,
        altitude_ft=0.0,
        reported_at=datetime.now(),
    )
    moving_east = VelocityReport(
        ground_speed_kt=100.0,
        track_deg=90.0,
        vertical_rate_fpm=0.0,
        reported_at=datetime.now(),
    )
    moving_north = VelocityReport(
        ground_speed_kt=100.0,
        track_deg=0.0,
        vertical_rate_fpm=0.0,
        reported_at=datetime.now(),
    )

    opening_rate = position_velocity_to_range_rate_mps(target_due_east, moving_east, observer)
    cross_range_rate = position_velocity_to_range_rate_mps(target_due_east, moving_north, observer)

    assert isclose(opening_rate, 100.0 * KNOT_TO_METERS_PER_SECOND, rel_tol=0.001)
    assert isclose(cross_range_rate, 0.0, abs_tol=0.01)


def test_doppler_is_positive_for_closing_range_rate() -> None:
    doppler_hz = range_rate_to_doppler_hz(range_rate_mps=-100.0, carrier_frequency_hz=600e6)

    assert isclose(doppler_hz, 200.138457, rel_tol=0.001)
