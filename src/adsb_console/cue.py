"""Versioned planner-facing cue DTOs, JSON serialization, and UDP publishing."""

from __future__ import annotations

import json
import logging
import socket
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from ipaddress import ip_address
from threading import Lock
from typing import Literal, cast
from uuid import uuid4

from adsb_console.prediction import (
    ObservationOpportunity,
    ObservationSample,
    ObservationWindow,
    TrackPrediction,
)

SCHEMA_VERSION = "1.1.0"
SOURCE_NAME = "ADSBConsoleApp"
LOGGER = logging.getLogger(__name__)
CueHeartbeatStatus = Literal["starting", "running", "degraded", "stopping"]


@dataclass(frozen=True, slots=True)
class UdpOutputConfig:
    enabled: bool = False
    destination_address: str = "127.0.0.1"
    destination_port: int = 31_001
    source_address: str | None = None
    maximum_datagram_bytes: int = 16_384
    oversize_policy: str = "omit_history"
    heartbeat_interval_s: float = 10.0
    snapshot_interval_s: float = 60.0
    maximum_opportunities_per_cue: int = 3


@dataclass(frozen=True, slots=True)
class PublicationResult:
    message_id: str
    sequence_number: int
    bytes_sent: int
    omitted_history: bool


class CueSerializer:
    """Map prediction-domain objects to finite JSON-safe schema DTOs."""

    def __init__(self, *, maximum_opportunities: int | None = None) -> None:
        # None publishes every usable opportunity; a limit keeps the N with the
        # highest peak window SNR so a cue fits in one datagram.
        self.maximum_opportunities = maximum_opportunities

    def track_cue(
        self,
        prediction: TrackPrediction,
        *,
        source_instance_id: str,
        sequence_number: int,
        message_id: str,
        generated_utc: datetime,
        include_history: bool,
        snapshot_id: str | None = None,
    ) -> dict[str, object]:
        state = prediction.state
        if state is None:
            raise ValueError("Cannot serialize an invalid prediction as a track cue")
        return {
            "schema_version": SCHEMA_VERSION,
            "message_type": "track_cue",
            "message_id": message_id,
            "source": SOURCE_NAME,
            "source_instance_id": source_instance_id,
            "sequence_number": sequence_number,
            "generated_utc": _iso_utc(generated_utc),
            "snapshot_id": snapshot_id,
            "track": {
                "track_id": prediction.track_id,
                "icao": prediction.icao,
                "callsign": prediction.callsign,
                "status": prediction.status.value,
                "last_report_utc": _iso_utc(state.epoch_utc),
                "report_age_s": max(_seconds_between(generated_utc, state.epoch_utc), 0.0),
                "state": {
                    "epoch_utc": _iso_utc(state.epoch_utc),
                    "reference_frame": "ENU",
                    "reference_origin_id": state.reference_origin_id,
                    "position_enu_m": [
                        state.position_enu_m.east_m,
                        state.position_enu_m.north_m,
                        state.position_enu_m.up_m,
                    ],
                    "velocity_enu_mps": [
                        state.velocity_enu_mps.east_mps,
                        state.velocity_enu_mps.north_mps,
                        state.velocity_enu_mps.up_mps,
                    ],
                    "latitude_deg": state.latitude_deg,
                    "longitude_deg": state.longitude_deg,
                    "altitude_m_msl": state.altitude_m,
                    "ground_speed_mps": state.ground_speed_mps,
                    "track_angle_deg": state.track_angle_deg,
                    "vertical_rate_mps": state.vertical_rate_mps,
                    "quality": {
                        "vertical_rate_assumed": state.vertical_rate_assumed,
                        "horizontal_velocity_estimated": False,
                        "position_valid": True,
                        "velocity_valid": True,
                    },
                },
            },
            "prediction": {
                "prediction_id": prediction.prediction_id,
                "revision": prediction.revision,
                "created_utc": _iso_utc(prediction.created_utc),
                "valid_until_utc": _iso_utc(prediction.valid_until_utc),
                "horizon_s": prediction.horizon_s,
                "sample_interval_s": prediction.sample_interval_s,
                "motion_model": "constant_velocity_enu",
                "maturity": prediction.maturity.value,
                "update_reason": prediction.update_reason.value,
                "validation": {
                    "position_error_m": prediction.validation.position_error_m,
                    "position_error_threshold_m": prediction.validation.position_error_threshold_m,
                    "velocity_error_mps": prediction.validation.velocity_error_mps,
                    "velocity_error_threshold_mps": (
                        prediction.validation.velocity_error_threshold_mps
                    ),
                    "heading_change_deg": prediction.validation.heading_change_deg,
                    "heading_change_threshold_deg": (
                        prediction.validation.heading_change_threshold_deg
                    ),
                },
            },
            "opportunities": [
                self._opportunity(item, include_history=include_history)
                for item in self.ranked_opportunities(prediction)
            ],
        }

    def withdrawal(
        self,
        *,
        track_id: str,
        icao: str,
        revision: int,
        reason: str,
        source_instance_id: str,
        sequence_number: int,
        message_id: str,
        generated_utc: datetime,
    ) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "message_type": "track_cue_withdrawal",
            "message_id": message_id,
            "source": SOURCE_NAME,
            "source_instance_id": source_instance_id,
            "sequence_number": sequence_number,
            "generated_utc": _iso_utc(generated_utc),
            "track_id": track_id,
            "icao": icao,
            "withdrawn_prediction_revision": revision,
            "reason": reason,
        }

    def heartbeat(
        self,
        *,
        source_instance_id: str,
        sequence_number: int,
        message_id: str,
        generated_utc: datetime,
        active_tracks: int,
        cue_eligible_tracks: int,
        active_observers: int,
        enabled_emitters: int,
        udp_destination: str | None,
        last_full_snapshot_utc: datetime | None,
        status: CueHeartbeatStatus,
    ) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "message_type": "cue_heartbeat",
            "message_id": message_id,
            "source": SOURCE_NAME,
            "source_instance_id": source_instance_id,
            "sequence_number": sequence_number,
            "generated_utc": _iso_utc(generated_utc),
            "status": status,
            "active_tracks": active_tracks,
            "cue_eligible_tracks": cue_eligible_tracks,
            "active_observers": active_observers,
            "enabled_emitters": enabled_emitters,
            "udp_destination": udp_destination,
            "last_full_snapshot_utc": (
                None if last_full_snapshot_utc is None else _iso_utc(last_full_snapshot_utc)
            ),
        }

    def snapshot_boundary(
        self,
        *,
        begin: bool,
        snapshot_id: str,
        source_instance_id: str,
        sequence_number: int,
        message_id: str,
        generated_utc: datetime,
        expected_track_count: int | None = None,
        published_track_count: int | None = None,
        failed_track_count: int | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "message_type": "cue_snapshot_begin" if begin else "cue_snapshot_end",
            "message_id": message_id,
            "source": SOURCE_NAME,
            "source_instance_id": source_instance_id,
            "sequence_number": sequence_number,
            "generated_utc": _iso_utc(generated_utc),
            "snapshot_id": snapshot_id,
        }
        if begin:
            payload["expected_track_count"] = expected_track_count or 0
        else:
            payload["published_track_count"] = published_track_count or 0
            payload["failed_track_count"] = failed_track_count or 0
        return payload

    def ranked_opportunities(
        self, prediction: TrackPrediction
    ) -> list[ObservationOpportunity]:
        """Usable opportunities, strongest peak window SNR first, capped at the limit."""

        usable = [
            item
            for item in prediction.opportunities
            if item.observer_can_receive and item.emitter_enabled and item.windows
        ]
        usable.sort(key=_peak_window_snr_db, reverse=True)
        if self.maximum_opportunities is None:
            return usable
        return usable[: self.maximum_opportunities]

    def to_json_bytes(self, payload: dict[str, object]) -> bytes:
        return json.dumps(
            payload, allow_nan=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")

    def _opportunity(
        self, opportunity: ObservationOpportunity, *, include_history: bool
    ) -> dict[str, object]:
        samples = opportunity.samples
        current = opportunity.current
        windows = [self._window(item) for item in opportunity.windows]
        max_snr_sample = (
            max(samples, key=lambda item: item.predicted_bistatic_snr_db) if samples else None
        )
        return {
            "opportunity_id": (
                f"{opportunity.observer_id}:{opportunity.emitter_id}"
            ),
            "observer_id": opportunity.observer_id,
            "observer_name": opportunity.observer_name,
            "emitter_id": opportunity.emitter_id,
            "transmitter_site_id": opportunity.transmitter_site_id,
            "carrier_frequency_hz": opportunity.emitter.center_frequency_mhz * 1_000_000.0,
            "rf_channel": _rf_channel_or_none(opportunity.emitter.rf_channel),
            "emitter_enabled": opportunity.emitter_enabled,
            "observer_can_receive": opportunity.observer_can_receive,
            "models": {
                "bistatic_range_definition": "tx_target_plus_target_rx_minus_tx_rx",
                "doppler_source": "analytic_derivative_of_bistatic_range",
                "doppler_sign_convention": "positive_for_decreasing_bistatic_path",
                "snr_model_id": "console_bisnr_v1",
                "assumed_rcs_dbsm": opportunity.assumed_rcs_dbsm,
                "detection_threshold_db": opportunity.detection_threshold_db,
            },
            "current": None if current is None else self._sample(current),
            "summary": {
                "has_usable_window": bool(opportunity.windows),
                "next_window_start_utc": (
                    None if not opportunity.windows else _iso_utc(opportunity.windows[0].start_utc)
                ),
                "next_window_end_utc": (
                    None if not opportunity.windows else _iso_utc(opportunity.windows[0].end_utc)
                ),
                "total_usable_duration_s": sum(item.duration_s for item in opportunity.windows),
                "maximum_snr_db": (
                    None if max_snr_sample is None else max_snr_sample.predicted_bistatic_snr_db
                ),
                "maximum_snr_utc": (
                    None if max_snr_sample is None else _iso_utc(max_snr_sample.sample_utc)
                ),
                "minimum_bistatic_range_m": (
                    None if not samples else min(item.bistatic_range_m for item in samples)
                ),
                "maximum_bistatic_range_m": (
                    None if not samples else max(item.bistatic_range_m for item in samples)
                ),
                "minimum_bistatic_doppler_hz": (
                    None if not samples else min(item.bistatic_doppler_hz for item in samples)
                ),
                "maximum_bistatic_doppler_hz": (
                    None if not samples else max(item.bistatic_doppler_hz for item in samples)
                ),
            },
            "windows": windows,
            "history": [self._sample(item) for item in samples] if include_history else None,
        }

    def _sample(self, sample: ObservationSample) -> dict[str, object]:
        return {
            "time_offset_s": sample.time_offset_s,
            "sample_utc": _iso_utc(sample.sample_utc),
            "bistatic_range_m": sample.bistatic_range_m,
            "bistatic_range_rate_mps": sample.bistatic_range_rate_mps,
            "bistatic_doppler_hz": sample.bistatic_doppler_hz,
            "predicted_bistatic_snr_db": sample.predicted_bistatic_snr_db,
            "geometrically_visible": sample.geometrically_visible,
            "rf_available": sample.rf_available,
            "within_range_limits": sample.within_range_limits,
            "within_doppler_limits": sample.within_doppler_limits,
            "above_snr_threshold": sample.above_snr_threshold,
            "usable": sample.usable,
        }

    def _window(self, window: ObservationWindow) -> dict[str, object]:
        return {
            "window_id": f"w:{_iso_utc(window.start_utc)}",
            "start_utc": _iso_utc(window.start_utc),
            "end_utc": _iso_utc(window.end_utc),
            "duration_s": window.duration_s,
            "entry_reason": window.entry_reason,
            "exit_reason": window.exit_reason,
            "min_bistatic_range_m": window.min_bistatic_range_m,
            "max_bistatic_range_m": window.max_bistatic_range_m,
            "min_bistatic_range_rate_mps": window.min_bistatic_range_rate_mps,
            "max_bistatic_range_rate_mps": window.max_bistatic_range_rate_mps,
            "min_bistatic_doppler_hz": window.min_bistatic_doppler_hz,
            "max_bistatic_doppler_hz": window.max_bistatic_doppler_hz,
            "maximum_abs_doppler_rate_hzps": window.maximum_abs_doppler_rate_hzps,
            "min_snr_db": window.min_snr_db,
            "mean_snr_db": window.mean_snr_db,
            "max_snr_db": window.max_snr_db,
            "peak_snr_utc": _iso_utc(window.peak_snr_utc),
        }


class UdpCuePublisher:
    """A synchronous UDP publisher with serialized sequence allocation."""

    def __init__(
        self,
        config: UdpOutputConfig,
        *,
        source_instance_id: str,
        serializer: CueSerializer | None = None,
        clock: Callable[[], datetime] | None = None,
        id_provider: Callable[[], str] | None = None,
        udp_socket: socket.socket | None = None,
    ) -> None:
        self.config = config
        self.source_instance_id = source_instance_id
        self.serializer = serializer or CueSerializer(
            maximum_opportunities=config.maximum_opportunities_per_cue
        )
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_provider = id_provider or (lambda: str(uuid4()))
        self._sequence = 0
        self._lock = Lock()
        self._socket = udp_socket or socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if ip_address(config.destination_address).is_multicast:
            self._socket.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
            self._socket.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        if config.source_address is not None:
            self._socket.bind((config.source_address, 0))
        self.sent_count = 0
        self.failure_count = 0
        self.oversize_count = 0

    def close(self) -> None:
        self._socket.close()

    def publish_prediction(
        self,
        prediction: TrackPrediction,
        *,
        include_history: bool = False,
        snapshot_id: str | None = None,
    ) -> PublicationResult | None:
        return self._publish(
            lambda sequence, message_id, now: self.serializer.track_cue(
                prediction,
                source_instance_id=self.source_instance_id,
                sequence_number=sequence,
                message_id=message_id,
                generated_utc=now,
                include_history=include_history,
                snapshot_id=snapshot_id,
            ),
            retry_without_history=include_history,
        )

    def publish_withdrawal(
        self, *, track_id: str, icao: str, revision: int, reason: str
    ) -> PublicationResult | None:
        return self._publish(
            lambda sequence, message_id, now: self.serializer.withdrawal(
                track_id=track_id,
                icao=icao,
                revision=revision,
                reason=reason,
                source_instance_id=self.source_instance_id,
                sequence_number=sequence,
                message_id=message_id,
                generated_utc=now,
            )
        )

    def publish_heartbeat(
        self,
        *,
        active_tracks: int,
        cue_eligible_tracks: int,
        active_observers: int,
        enabled_emitters: int,
        last_full_snapshot_utc: datetime | None,
        status: CueHeartbeatStatus,
    ) -> PublicationResult | None:
        destination = f"{self.config.destination_address}:{self.config.destination_port}"
        return self._publish(
            lambda sequence, message_id, now: self.serializer.heartbeat(
                source_instance_id=self.source_instance_id,
                sequence_number=sequence,
                message_id=message_id,
                generated_utc=now,
                active_tracks=active_tracks,
                cue_eligible_tracks=cue_eligible_tracks,
                active_observers=active_observers,
                enabled_emitters=enabled_emitters,
                udp_destination=destination,
                last_full_snapshot_utc=last_full_snapshot_utc,
                status=status,
            )
        )

    def publish_snapshot_boundary(
        self,
        *,
        begin: bool,
        snapshot_id: str,
        expected_track_count: int | None = None,
        published_track_count: int | None = None,
        failed_track_count: int | None = None,
    ) -> PublicationResult | None:
        return self._publish(
            lambda sequence, message_id, now: self.serializer.snapshot_boundary(
                begin=begin,
                snapshot_id=snapshot_id,
                source_instance_id=self.source_instance_id,
                sequence_number=sequence,
                message_id=message_id,
                generated_utc=now,
                expected_track_count=expected_track_count,
                published_track_count=published_track_count,
                failed_track_count=failed_track_count,
            )
        )

    def _publish(
        self,
        make_payload: Callable[[int, str, datetime], dict[str, object]],
        *,
        retry_without_history: bool = False,
    ) -> PublicationResult | None:
        if not self.config.enabled:
            return None
        with self._lock:
            self._sequence += 1
            sequence = self._sequence
            message_id = self._id_provider()
            payload = make_payload(sequence, message_id, self._clock())
            encoded = self.serializer.to_json_bytes(payload)
            omitted_history = False
            if len(encoded) > self.config.maximum_datagram_bytes:
                self.oversize_count += 1
                if retry_without_history and self.config.oversize_policy == "omit_history":
                    payload = make_payload(sequence, message_id, self._clock())
                    opportunities = payload.get("opportunities")
                    if isinstance(opportunities, list):
                        for opportunity in cast(list[object], opportunities):
                            if isinstance(opportunity, dict):
                                opportunity["history"] = None
                    encoded = self.serializer.to_json_bytes(payload)
                    omitted_history = True
                if len(encoded) > self.config.maximum_datagram_bytes:
                    self.failure_count += 1
                    return None
            try:
                self._socket.sendto(
                    encoded,
                    (self.config.destination_address, self.config.destination_port),
                )
            except OSError:
                self.failure_count += 1
                return None
            self.sent_count += 1
            return PublicationResult(
                message_id=message_id,
                sequence_number=sequence,
                bytes_sent=len(encoded),
                omitted_history=omitted_history,
            )


def _peak_window_snr_db(opportunity: ObservationOpportunity) -> float:
    return max(window.max_snr_db for window in opportunity.windows)


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _seconds_between(later: datetime, earlier: datetime) -> float:
    if later.tzinfo is None:
        later = later.replace(tzinfo=UTC)
    if earlier.tzinfo is None:
        earlier = earlier.replace(tzinfo=UTC)
    return (later.astimezone(UTC) - earlier.astimezone(UTC)).total_seconds()


def _rf_channel_or_none(value: object) -> int | None:
    """Normalize source CSV channel values to the frozen wire contract."""

    if isinstance(value, bool):
        channel = None
    elif isinstance(value, int):
        channel = value
    elif isinstance(value, str):
        try:
            channel = int(value.strip())
        except ValueError:
            channel = None
    else:
        channel = None
    if channel is not None and 2 <= channel <= 69:
        return channel
    LOGGER.warning("Cue emitter has invalid RF channel %r; publishing null", value)
    return None
