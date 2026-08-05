"""Coordinate transforms for ADS-B track projection."""

from __future__ import annotations

from dataclasses import dataclass
from math import atan2, cos, degrees, hypot, radians, sin, sqrt

from adsb_console.models import ObserverConfig, PositionReport, VelocityReport

WGS84_A_M = 6_378_137.0
WGS84_F = 1.0 / 298.257_223_563
WGS84_E2 = WGS84_F * (2.0 - WGS84_F)
MEAN_EARTH_RADIUS_M = 6_371_000.0
KNOT_TO_METERS_PER_SECOND = 0.514444
FEET_PER_MINUTE_TO_METERS_PER_SECOND = 0.00508
SPEED_OF_LIGHT_MPS = 299_792_458.0


@dataclass(frozen=True, slots=True)
class EcefPoint:
    """Earth-centered, Earth-fixed Cartesian point in meters."""

    x_m: float
    y_m: float
    z_m: float


@dataclass(frozen=True, slots=True)
class EcefVector:
    """Earth-centered, Earth-fixed Cartesian vector."""

    x: float
    y: float
    z: float


@dataclass(frozen=True, slots=True)
class EnuPoint:
    """Local east-north-up point in meters."""

    east_m: float
    north_m: float
    up_m: float


@dataclass(frozen=True, slots=True)
class EnuVector:
    """Local east-north-up velocity vector in meters per second."""

    east_mps: float
    north_mps: float
    up_mps: float


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


def ecef_vector_to_enu(vector: EcefVector, observer: ObserverConfig) -> EnuVector:
    """Project an ECEF vector into an observer-centered ENU frame."""

    lat = radians(observer.latitude_deg)
    lon = radians(observer.longitude_deg)
    sin_lat = sin(lat)
    cos_lat = cos(lat)
    sin_lon = sin(lon)
    cos_lon = cos(lon)

    return EnuVector(
        east_mps=-sin_lon * vector.x + cos_lon * vector.y,
        north_mps=-sin_lat * cos_lon * vector.x - sin_lat * sin_lon * vector.y + cos_lat * vector.z,
        up_mps=cos_lat * cos_lon * vector.x + cos_lat * sin_lon * vector.y + sin_lat * vector.z,
    )


def enu_vector_to_ecef(vector: EnuVector, latitude_deg: float, longitude_deg: float) -> EcefVector:
    """Project a local ENU vector into ECEF coordinates."""

    lat = radians(latitude_deg)
    lon = radians(longitude_deg)
    sin_lat = sin(lat)
    cos_lat = cos(lat)
    sin_lon = sin(lon)
    cos_lon = cos(lon)

    return EcefVector(
        x=-sin_lon * vector.east_mps
        - sin_lat * cos_lon * vector.north_mps
        + cos_lat * cos_lon * vector.up_mps,
        y=cos_lon * vector.east_mps
        - sin_lat * sin_lon * vector.north_mps
        + cos_lat * sin_lon * vector.up_mps,
        z=cos_lat * vector.north_mps + sin_lat * vector.up_mps,
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


def position_to_enu(position: PositionReport, observer: ObserverConfig) -> EnuPoint:
    """Project an aircraft position report into observer-centered ENU coordinates."""

    ecef = lla_to_ecef(position.latitude_deg, position.longitude_deg, position.altitude_m)
    return ecef_to_enu(ecef, observer)


def velocity_to_observer_enu(
    position: PositionReport, velocity: VelocityReport, observer: ObserverConfig
) -> EnuVector:
    """Project an aircraft velocity report into the observer ENU frame."""

    track_rad = radians(velocity.track_deg)
    ground_speed_mps = velocity.ground_speed_kt * KNOT_TO_METERS_PER_SECOND
    target_velocity = EnuVector(
        east_mps=ground_speed_mps * sin(track_rad),
        north_mps=ground_speed_mps * cos(track_rad),
        up_mps=0.0
        if velocity.vertical_rate_fpm is None
        else velocity.vertical_rate_fpm * FEET_PER_MINUTE_TO_METERS_PER_SECOND,
    )
    ecef_velocity = enu_vector_to_ecef(
        target_velocity, position.latitude_deg, position.longitude_deg
    )
    return ecef_vector_to_enu(ecef_velocity, observer)


def position_velocity_to_range_rate_mps(
    position: PositionReport, velocity: VelocityReport, observer: ObserverConfig
) -> float:
    """Return line-of-sight range rate; positive is opening, negative is closing."""

    target_enu = position_to_enu(position, observer)
    velocity_enu = velocity_to_observer_enu(position, velocity, observer)
    range_m = hypot(hypot(target_enu.east_m, target_enu.north_m), target_enu.up_m)
    if range_m == 0.0:
        return 0.0
    return (
        target_enu.east_m * velocity_enu.east_mps
        + target_enu.north_m * velocity_enu.north_mps
        + target_enu.up_m * velocity_enu.up_mps
    ) / range_m


def range_rate_to_doppler_hz(range_rate_mps: float, carrier_frequency_hz: float) -> float:
    """Return one-way Doppler shift; closing targets have positive Doppler."""

    return -range_rate_mps / SPEED_OF_LIGHT_MPS * carrier_frequency_hz


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
