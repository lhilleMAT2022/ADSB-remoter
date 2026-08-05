"""Typed models for BaseStation/SBS messages and track state."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum, StrEnum
from typing import Self

BASESTATION_FIELD_COUNT = 22
MILLISECONDS = 3
FEET_TO_METERS = 0.3048


class TransmissionType(IntEnum):
    """BaseStation MSG transmission subtypes."""

    ES_IDENT_AND_CATEGORY = 1
    ES_SURFACE_POSITION = 2
    ES_AIRBORNE_POSITION = 3
    ES_AIRBORNE_VELOCITY = 4
    SURVEILLANCE_ALT = 5
    SURVEILLANCE_ID = 6
    AIR_TO_AIR = 7
    ALL_CALL_REPLY = 8


class ObserverRole(StrEnum):
    """Observer role in the ADS-B console."""

    LOCAL = "local"
    REMOTE = "remote"


class ReportMethod(StrEnum):
    """How reports are sent to an observer."""

    NONE = "none"
    UDP = "udp"
    TCP = "tcp"


@dataclass(frozen=True, slots=True)
class PositionReport:
    """Aircraft geodetic position at a report time."""

    latitude_deg: float
    longitude_deg: float
    altitude_ft: float
    reported_at: datetime

    @property
    def altitude_m(self) -> float:
        return self.altitude_ft * FEET_TO_METERS


@dataclass(frozen=True, slots=True)
class VelocityReport:
    """Aircraft velocity-like BaseStation report."""

    ground_speed_kt: float
    track_deg: float
    vertical_rate_fpm: float | None
    reported_at: datetime


@dataclass(frozen=True, slots=True)
class BaseStationMessage:
    """A parsed BaseStation/SBS-1 CSV message.

    Field numbering follows the SBS/BaseStation convention:
    0 message type, 1 transmission type, 4 ICAO hex, 6/7 generated date/time,
    8/9 logged date/time, 10 callsign, 11 altitude, 12 ground speed, 13 track,
    14 latitude, 15 longitude, 16 vertical rate, 17 squawk, 21 on-ground flag.
    """

    fields: tuple[str, ...]

    @classmethod
    def parse(cls, line: str) -> Self:
        raw = line.strip()
        if not raw:
            raise ValueError("BaseStation message is empty")

        fields = tuple(part.strip() for part in next(csv.reader([raw])))
        if len(fields) < 5:
            raise ValueError(f"BaseStation message has too few fields: {len(fields)}")
        if len(fields) < BASESTATION_FIELD_COUNT:
            fields = fields + ("",) * (BASESTATION_FIELD_COUNT - len(fields))
        return cls(fields=fields)

    @property
    def message_type(self) -> str:
        return self.fields[0]

    @property
    def transmission_type(self) -> TransmissionType | None:
        return _parse_transmission_type(self.fields[1])

    @property
    def icao(self) -> str:
        return self.fields[4].upper()

    @property
    def callsign(self) -> str | None:
        value = self.fields[10].strip()
        return value or None

    @property
    def generated_at(self) -> datetime | None:
        return _parse_basestation_datetime(self.fields[6], self.fields[7])

    @property
    def logged_at(self) -> datetime | None:
        return _parse_basestation_datetime(self.fields[8], self.fields[9])

    @property
    def altitude_ft(self) -> float | None:
        return _parse_float(self.fields[11])

    @property
    def ground_speed_kt(self) -> float | None:
        return _parse_float(self.fields[12])

    @property
    def track_deg(self) -> float | None:
        return _parse_float(self.fields[13])

    @property
    def latitude_deg(self) -> float | None:
        return _parse_float(self.fields[14])

    @property
    def longitude_deg(self) -> float | None:
        return _parse_float(self.fields[15])

    @property
    def vertical_rate_fpm(self) -> float | None:
        return _parse_float(self.fields[16])

    @property
    def squawk(self) -> str | None:
        value = self.fields[17].strip()
        return value or None

    @property
    def is_on_ground(self) -> bool | None:
        value = self.fields[21].strip()
        if not value:
            return None
        return value == "1"

    def position_report(self) -> PositionReport | None:
        reported_at = self.generated_at
        latitude = self.latitude_deg
        longitude = self.longitude_deg
        altitude = self.altitude_ft
        if reported_at is None or latitude is None or longitude is None or altitude is None:
            return None
        return PositionReport(
            latitude_deg=latitude,
            longitude_deg=longitude,
            altitude_ft=altitude,
            reported_at=reported_at,
        )

    def velocity_report(self) -> VelocityReport | None:
        reported_at = self.generated_at
        ground_speed = self.ground_speed_kt
        track = self.track_deg
        if reported_at is None or ground_speed is None or track is None:
            return None
        return VelocityReport(
            ground_speed_kt=ground_speed,
            track_deg=track,
            vertical_rate_fpm=self.vertical_rate_fpm,
            reported_at=reported_at,
        )

    def with_rebased_timestamps(self, timestamp: datetime) -> BaseStationMessage:
        fields = list(self.fields)
        date_text = _format_date(timestamp)
        time_text = _format_time(timestamp)
        fields[6] = date_text
        fields[7] = time_text
        fields[8] = date_text
        fields[9] = time_text
        return BaseStationMessage(fields=tuple(fields))

    def to_csv_line(self) -> str:
        return ",".join(self.fields)


@dataclass(frozen=True, slots=True)
class ObserverConfig:
    """Observer/sensor configuration for projecting ADS-B tracks."""

    name: str
    role: ObserverRole
    latitude_deg: float
    longitude_deg: float
    altitude_m: float
    orientation_az_deg: float = 0.0
    orientation_el_deg: float = 0.0
    orientation_roll_deg: float = 0.0
    min_range_m: float = 0.0
    max_range_m: float = 1_000_000.0
    min_azimuth_deg: float = 0.0
    max_azimuth_deg: float = 360.0
    min_elevation_deg: float = -90.0
    max_elevation_deg: float = 90.0
    seek_pattern: str = ".*"
    report_rate_hz: float = 0.5
    report_method: ReportMethod = ReportMethod.NONE
    report_endpoint: str | None = None

    @property
    def is_local(self) -> bool:
        return self.role is ObserverRole.LOCAL


def _new_subtype_counts() -> dict[int, int]:
    return {}


def _new_position_history() -> list[PositionReport]:
    return []


@dataclass(slots=True)
class TrackState:
    """Current state accumulated for one ICAO aircraft."""

    icao: str
    first_seen: datetime
    last_seen: datetime
    message_count: int = 0
    subtype_counts: dict[int, int] = field(default_factory=_new_subtype_counts)
    callsign: str | None = None
    squawk: str | None = None
    last_position: PositionReport | None = None
    last_velocity: VelocityReport | None = None
    position_history: list[PositionReport] = field(default_factory=_new_position_history)
    max_history: int = 100

    def update(self, message: BaseStationMessage) -> None:
        reported_at = message.generated_at or datetime.now()
        self.last_seen = reported_at
        self.message_count += 1

        if message.transmission_type is not None:
            subtype = int(message.transmission_type)
            self.subtype_counts[subtype] = self.subtype_counts.get(subtype, 0) + 1

        if message.callsign:
            self.callsign = message.callsign
        if message.squawk:
            self.squawk = message.squawk

        position = message.position_report()
        if position is not None:
            self.last_position = position
            self.position_history.append(position)
            if len(self.position_history) > self.max_history:
                del self.position_history[0 : len(self.position_history) - self.max_history]

        velocity = message.velocity_report()
        if velocity is not None:
            self.last_velocity = velocity


def _parse_float(value: str) -> float | None:
    value = value.strip()
    if not value:
        return None
    return float(value)


def _parse_transmission_type(value: str) -> TransmissionType | None:
    value = value.strip()
    if not value:
        return None
    try:
        return TransmissionType(int(value))
    except ValueError:
        return None


def _parse_basestation_datetime(date_value: str, time_value: str) -> datetime | None:
    date_value = date_value.strip()
    time_value = time_value.strip()
    if not date_value:
        return None

    candidates: list[str] = []
    if time_value:
        candidates.append(f"{date_value} {time_value}")
    candidates.append(date_value)

    formats = (
        "%Y/%m/%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
    )
    for candidate in candidates:
        for date_format in formats:
            try:
                return datetime.strptime(candidate, date_format)
            except ValueError:
                continue
    return None


def _format_date(timestamp: datetime) -> str:
    return timestamp.strftime("%Y/%m/%d")


def _format_time(timestamp: datetime) -> str:
    text = timestamp.strftime("%H:%M:%S.%f")
    return text[: -(6 - MILLISECONDS)]
