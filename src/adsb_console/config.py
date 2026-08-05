"""Configuration helpers for CLI-compatible endpoint and observer files."""

from __future__ import annotations

import configparser
from pathlib import Path

from adsb_console.models import ObserverConfig, ObserverRole, ReportMethod

Endpoint = tuple[str, int]
DEFAULT_LOCAL_OBSERVER = ObserverConfig(
    name="Local",
    role=ObserverRole.LOCAL,
    latitude_deg=41.576006,
    longitude_deg=-71.281711,
    altitude_m=50.0,
)


def parse_endpoint(value: str) -> Endpoint:
    host, separator, port = value.rpartition(":")
    if not separator or not host or not port:
        raise ValueError(f"Endpoint must be HOST:PORT, got {value!r}")
    return host, int(port)


def parse_endpoint_list(value: str) -> list[Endpoint]:
    return [parse_endpoint(part.strip()) for part in value.split(",") if part.strip()]


def load_observers(path: str | Path) -> list[ObserverConfig]:
    parser = configparser.ConfigParser()
    parser.read(path)
    observers: list[ObserverConfig] = []

    for section in parser.sections():
        values = parser[section]
        loc_lat, loc_lon, loc_alt = _triple(values.get("locLat_locLon_locAlt", "0,0,0"))
        azimuth, elevation, roll = _triple(values.get("orientAz_orientEl_orientRot", "0,0,0"))
        min_range, max_range = _pair(values.get("minR_maxR", "0,1000000"))
        min_azimuth, max_azimuth = _pair(
            values.get("minAz_maxAz") or values.get("spanAz_minMax") or "0,360"
        )
        min_elevation, max_elevation = _pair(
            values.get("minEl_maxEl") or values.get("spanEl_minMax") or "-90,90"
        )
        observers.append(
            ObserverConfig(
                name=values.get("name") or section,
                role=_observer_role(values.get("role") or values.get("type")),
                latitude_deg=loc_lat,
                longitude_deg=loc_lon,
                altitude_m=loc_alt,
                orientation_az_deg=azimuth,
                orientation_el_deg=elevation,
                orientation_roll_deg=roll,
                min_range_m=min_range,
                max_range_m=max_range,
                min_azimuth_deg=min_azimuth,
                max_azimuth_deg=max_azimuth,
                min_elevation_deg=min_elevation,
                max_elevation_deg=max_elevation,
                seek_pattern=values.get("seekPattern", ".*"),
                report_rate_hz=float(values.get("reportRateHz", "0.5")),
                report_method=_report_method(values.get("reportMethod")),
                report_endpoint=values.get("reportEndpoint"),
            )
        )
    return observers


def ensure_local_observer(observers: list[ObserverConfig]) -> list[ObserverConfig]:
    """Return observers with exactly one local observer available for display."""

    if any(observer.is_local for observer in observers):
        return observers
    if observers:
        first = observers[0]
        return [
            ObserverConfig(
                name=f"{first.name} Local",
                role=ObserverRole.LOCAL,
                latitude_deg=first.latitude_deg,
                longitude_deg=first.longitude_deg,
                altitude_m=first.altitude_m,
                orientation_az_deg=first.orientation_az_deg,
                orientation_el_deg=first.orientation_el_deg,
                orientation_roll_deg=first.orientation_roll_deg,
                min_range_m=0.0,
                max_range_m=1_000_000.0,
            ),
            *observers,
        ]
    return [DEFAULT_LOCAL_OBSERVER]


def load_observers_or_default(path: str | Path | None) -> list[ObserverConfig]:
    if path is None:
        return [DEFAULT_LOCAL_OBSERVER]
    return ensure_local_observer(load_observers(path))


def _observer_role(value: str | None) -> ObserverRole:
    if value is None:
        return ObserverRole.REMOTE
    normalized = value.strip().lower()
    if normalized in {"local", "lo"}:
        return ObserverRole.LOCAL
    return ObserverRole.REMOTE


def _report_method(value: str | None) -> ReportMethod:
    if value is None:
        return ReportMethod.NONE
    normalized = value.strip().lower()
    try:
        return ReportMethod(normalized)
    except ValueError:
        return ReportMethod.NONE


def _pair(value: str) -> tuple[float, float]:
    parts = [float(part.strip()) for part in value.split(",")]
    if len(parts) != 2:
        raise ValueError(f"Expected two comma-separated values, got {value!r}")
    return parts[0], parts[1]


def _triple(value: str) -> tuple[float, float, float]:
    parts = [float(part.strip()) for part in value.split(",")]
    if len(parts) != 3:
        raise ValueError(f"Expected three comma-separated values, got {value!r}")
    return parts[0], parts[1], parts[2]
