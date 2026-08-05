"""Track management for BaseStation/SBS streams."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from adsb_console.models import BaseStationMessage, ObserverConfig, TrackState
from adsb_console.transforms import RangeAzEl, is_observable_by, position_to_range_az_el


@dataclass(frozen=True, slots=True)
class ObservedTrack:
    """Track projected into one observer frame."""

    observer_name: str
    icao: str
    callsign: str | None
    range_az_el: RangeAzEl
    reported_at: datetime


class BaseStationTracker:
    """Stateful tracker keyed by ICAO hex address."""

    def __init__(self, *, max_history: int = 100) -> None:
        self._tracks: dict[str, TrackState] = {}
        self._max_history = max_history
        self.message_count = 0
        self.invalid_count = 0

    @property
    def tracks(self) -> dict[str, TrackState]:
        return self._tracks

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
        reported_at = message.generated_at or datetime.now()
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
        return track

    def active_tracks(self) -> list[TrackState]:
        return sorted(self._tracks.values(), key=lambda item: item.last_seen, reverse=True)

    def observed_tracks(self, observers: list[ObserverConfig]) -> list[ObservedTrack]:
        observed: list[ObservedTrack] = []
        for observer in observers:
            seeker = re.compile(observer.seek_pattern)
            for track in self._tracks.values():
                if track.last_position is None:
                    continue
                range_az_el = position_to_range_az_el(track.last_position, observer)
                regex_match = seeker.fullmatch(track.icao) is not None
                specific_regex = observer.seek_pattern.strip() not in {"", ".*", ".+"}
                if (
                    not observer.is_local
                    and not (specific_regex and regex_match)
                    and not is_observable_by(track.last_position, range_az_el, observer)
                ):
                    continue
                observed.append(
                    ObservedTrack(
                        observer_name=observer.name,
                        icao=track.icao,
                        callsign=track.callsign,
                        range_az_el=range_az_el,
                        reported_at=track.last_position.reported_at,
                    )
                )
        return observed
