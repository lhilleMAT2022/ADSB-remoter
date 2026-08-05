"""Coordinate transforms for ADS-B track projection."""

from __future__ import annotations

from dataclasses import dataclass
from math import atan2, cos, degrees, hypot, radians, sin, sqrt

from adsb_console.models import ObserverConfig, PositionReport

WGS84_A_M = 6_378_137.0
WGS84_F = 1.0 / 298.257_223_563
WGS84_E2 = WGS84_F * (2.0 - WGS84_F)
MEAN_EARTH_RADIUS_M = 6_371_000.0


@dataclass(frozen=True, slots=True)
class EcefPoint:
    """Earth-centered, Earth-fixed Cartesian point in meters."""

    x_m: float
    y_m: float
    z_m: float


@dataclass(frozen=True, slots=True)
class EnuPoint:
    """Local east-north-up point in meters."""

    east_m: float
    north_m: float
    up_m: float


@dataclass(frozen=True, slots=True)
class RangeAzEl:
    """Range, azimuth, and elevation from an observer to a target."""

    range_m: float
    azimuth_deg: float
    elevation_deg: float


def lla_to_ecef(latitude_deg: float, longitude_deg: float, altitude_m: float) -> EcefPoint:
    """Convert geodetic latitude, longitude, altitude to WGS-84 ECEF."""

    lat = radians(latitude_deg)
    lon = radians(longitude_deg)
    sin_lat = sin(lat)
    cos_lat = cos(lat)
    radius = WGS84_A_M / sqrt(1.0 - WGS84_E2 * sin_lat * sin_lat)

    return EcefPoint(
        x_m=(radius + altitude_m) * cos_lat * cos(lon),
        y_m=(radius + altitude_m) * cos_lat * sin(lon),
        z_m=(radius * (1.0 - WGS84_E2) + altitude_m) * sin_lat,
    )


def ecef_to_enu(target: EcefPoint, observer: ObserverConfig) -> EnuPoint:
    """Project an ECEF target into an observer-centered ENU frame."""

    origin = lla_to_ecef(observer.latitude_deg, observer.longitude_deg, observer.altitude_m)
    dx = target.x_m - origin.x_m
    dy = target.y_m - origin.y_m
    dz = target.z_m - origin.z_m

    lat = radians(observer.latitude_deg)
    lon = radians(observer.longitude_deg)
    sin_lat = sin(lat)
    cos_lat = cos(lat)
    sin_lon = sin(lon)
    cos_lon = cos(lon)

    return EnuPoint(
        east_m=-sin_lon * dx + cos_lon * dy,
        north_m=-sin_lat * cos_lon * dx - sin_lat * sin_lon * dy + cos_lat * dz,
        up_m=cos_lat * cos_lon * dx + cos_lat * sin_lon * dy + sin_lat * dz,
    )


def enu_to_range_az_el(point: EnuPoint) -> RangeAzEl:
    """Convert ENU coordinates to range, azimuth clockwise from north, and elevation."""

    horizontal_m = hypot(point.east_m, point.north_m)
    range_m = hypot(horizontal_m, point.up_m)
    azimuth = degrees(atan2(point.east_m, point.north_m))
    if azimuth < 0.0:
        azimuth += 360.0
    elevation = degrees(atan2(point.up_m, horizontal_m))
    return RangeAzEl(range_m=range_m, azimuth_deg=azimuth, elevation_deg=elevation)


def position_to_range_az_el(position: PositionReport, observer: ObserverConfig) -> RangeAzEl:
    """Project an aircraft position report into observer range/azimuth/elevation."""

    ecef = lla_to_ecef(position.latitude_deg, position.longitude_deg, position.altitude_m)
    enu = ecef_to_enu(ecef, observer)
    return enu_to_range_az_el(enu)


def is_observable_by(
    position: PositionReport, range_az_el: RangeAzEl, observer: ObserverConfig
) -> bool:
    """Return true when a target satisfies observer range, angular, and horizon gates."""

    if observer.is_local:
        return True
    in_range = observer.min_range_m <= range_az_el.range_m <= observer.max_range_m
    in_azimuth = _angle_in_extent(
        range_az_el.azimuth_deg, observer.min_azimuth_deg, observer.max_azimuth_deg
    )
    in_elevation = (
        observer.min_elevation_deg <= range_az_el.elevation_deg <= observer.max_elevation_deg
    )
    horizon_visible = (
        range_az_el.elevation_deg < 0.0
        and range_az_el.elevation_deg < observer.min_elevation_deg
        and range_az_el.range_m <= horizon_distance_m(position.altitude_m, observer.altitude_m)
    )
    return in_range and in_azimuth and (in_elevation or horizon_visible)


def horizon_distance_m(target_altitude_m: float, observer_altitude_m: float = 0.0) -> float:
    """Approximate geometric line-of-sight horizon distance."""

    target_altitude_m = max(target_altitude_m, 0.0)
    observer_altitude_m = max(observer_altitude_m, 0.0)
    return sqrt(2.0 * MEAN_EARTH_RADIUS_M * target_altitude_m) + sqrt(
        2.0 * MEAN_EARTH_RADIUS_M * observer_altitude_m
    )


def _angle_in_extent(angle_deg: float, min_deg: float, max_deg: float) -> bool:
    angle = angle_deg % 360.0
    lower = min_deg % 360.0
    upper = max_deg % 360.0
    if abs(max_deg - min_deg) >= 360.0:
        return True
    if lower <= upper:
        return lower <= angle <= upper
    return angle >= lower or angle <= upper
