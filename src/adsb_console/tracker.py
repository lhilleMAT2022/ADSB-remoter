"""Track management for BaseStation/SBS streams."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from adsb_console.models import BaseStationMessage, ObserverConfig, TrackState, utc_now
from adsb_console.transforms import (
    ClosestPointOfApproach,
    RangeAzEl,
    closest_point_of_approach,
    is_observable_by,
    position_to_range_az_el,
    position_velocity_to_range_rate_mps,
    range_rate_to_doppler_hz,
)

DEFAULT_STALE_TRACK_SECONDS = 20.0 * 60.0


@dataclass(frozen=True, slots=True)
class ObservedTrack:
    """Track projected into one observer frame."""

    observer_name: str
    icao: str
    callsign: str | None
    range_az_el: RangeAzEl
    range_rate_mps: float | None
    doppler_hz: float | None
    cpa: ClosestPointOfApproach | None
    reported_at: datetime


class BaseStationTracker:
    """Stateful tracker keyed by ICAO hex address."""

    def __init__(
        self,
        *,
        max_history: int = 100,
        stale_track_seconds: float | None = DEFAULT_STALE_TRACK_SECONDS,
    ) -> None:
        self._tracks: dict[str, TrackState] = {}
        self._max_history = max_history
        self._stale_track_seconds = stale_track_seconds
        self.message_count = 0
        self.invalid_count = 0
        self.purged_track_count = 0

    @property
    def tracks(self) -> dict[str, TrackState]:
        return self._tracks

    @property
    def stale_track_seconds(self) -> float | None:
        return self._stale_track_seconds

    def update_line(self, line: str) -> TrackState | None:
        try:
            message = BaseStationMessage.parse(line)
        except ValueError:
            self.invalid_count += 1
            return None
        return self.update(message)

    def update(self, message: BaseStationMessage) -> TrackState | None:
        if message.message_type != "MSG" or not message.icao:
            self.invalid_count += 1
            return None

        self.message_count += 1
        reported_at = message.generated_at or utc_now()
        track = self._tracks.get(message.icao)
        if track is None:
            track = TrackState(
                icao=message.icao,
                first_seen=reported_at,
                last_seen=reported_at,
                max_history=self._max_history,
            )
            self._tracks[message.icao] = track

        track.update(message)
        self.purge_stale(reported_at)
        return track

    def purge_stale(self, reference_time: datetime | None = None) -> int:
        """Remove tracks with no reports inside the configured retention window."""

        if self._stale_track_seconds is None or self._stale_track_seconds <= 0.0:
            return 0
        now = reference_time or utc_now()
        cutoff = now - timedelta(seconds=self._stale_track_seconds)
        stale_icaos = [icao for icao, track in self._tracks.items() if track.last_seen < cutoff]
        for icao in stale_icaos:
            del self._tracks[icao]
        purged_count = len(stale_icaos)
        self.purged_track_count += purged_count
        return purged_count

    def active_tracks(self) -> list[TrackState]:
        return sorted(self._tracks.values(), key=lambda item: item.last_seen, reverse=True)

    def observed_tracks(
        self, observers: list[ObserverConfig], *, carrier_frequency_hz: float
    ) -> list[ObservedTrack]:
        observed: list[ObservedTrack] = []
        for observer in observers:
            seeker = re.compile(observer.seek_pattern)
            for track in self._tracks.values():
                if track.last_position is None:
                    continue
                observed_track = self.project_track(
                    track, observer, carrier_frequency_hz=carrier_frequency_hz
                )
                if observed_track is None:
                    continue
                regex_match = seeker.fullmatch(track.icao) is not None
                specific_regex = observer.seek_pattern.strip() not in {"", ".*", ".+"}
                if (
                    not observer.is_local
                    and not (specific_regex and regex_match)
                    and not is_observable_by(
                        track.last_position, observed_track.range_az_el, observer
                    )
                ):
                    continue
                observed.append(observed_track)
        return observed

    def project_track(
        self,
        track: TrackState,
        observer: ObserverConfig,
        *,
        carrier_frequency_hz: float,
    ) -> ObservedTrack | None:
        """Project one track into one observer frame without applying remote gates."""

        if track.last_position is None:
            return None
        range_az_el = position_to_range_az_el(track.last_position, observer)
        range_rate_mps = (
            None
            if track.last_velocity is None
            else position_velocity_to_range_rate_mps(
                track.last_position, track.last_velocity, observer
            )
        )
        cpa = (
            None
            if track.last_velocity is None
            else closest_point_of_approach(track.last_position, track.last_velocity, observer)
        )
        return ObservedTrack(
            observer_name=observer.name,
            icao=track.icao,
            callsign=track.callsign,
            range_az_el=range_az_el,
            range_rate_mps=range_rate_mps,
            doppler_hz=None
            if range_rate_mps is None
            else range_rate_to_doppler_hz(range_rate_mps, carrier_frequency_hz),
            cpa=cpa,
            reported_at=track.last_position.reported_at,
        )
