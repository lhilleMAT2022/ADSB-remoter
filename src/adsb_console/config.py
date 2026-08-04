"""Configuration helpers for CLI-compatible endpoint and observer files."""

from __future__ import annotations

import configparser
from pathlib import Path

from adsb_console.models import ObserverConfig

Endpoint = tuple[str, int]


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
        observers.append(
            ObserverConfig(
                name=values.get("name") or section,
                latitude_deg=loc_lat,
                longitude_deg=loc_lon,
                altitude_m=loc_alt,
                orientation_az_deg=azimuth,
                orientation_el_deg=elevation,
                orientation_roll_deg=roll,
                min_range_m=min_range,
                max_range_m=max_range,
                seek_pattern=values.get("seekPattern", ".*"),
            )
        )
    return observers


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
