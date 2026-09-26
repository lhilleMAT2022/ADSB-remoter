"""CT cue messages (ICD_Messages.md section 2, schema 2.0.0): serialization, framing, UDP.

The contract is flightTest docs/system/ICD_Messages.md: epoch-millisecond times and
per-unit resolutions (section 1.2), plain or deflate-with-dictionary framing (section 1.3),
and the CT message schemas in schemas/*-2.0.0.json (section 2).
"""

from __future__ import annotations

import hashlib
import json
import logging
import socket
import zlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from pathlib import Path
from threading import Lock
from typing import Literal
from uuid import uuid4

from adsb_console.bistatic import DEFAULT_RCS_DBSM
from adsb_console.prediction import (
    ObservationOpportunity,
    ObservationSample,
    ObservationWindow,
    TrackPrediction,
)

SCHEMA_VERSION = "2.0.0"
SOURCE_NAME = "ADSBConsoleApp"
LOGGER = logging.getLogger(__name__)
CueHeartbeatStatus = Literal["starting", "running", "degraded", "stopping"]
CueEncoding = Literal["json", "deflate_dictionary"]

PLAIN_FRAME_FIRST_BYTE = ord("{")
COMPRESSED_FRAME_TAG = 0xDC
MAXIMUM_DICTIONARY_BYTES = 32_768
DICTIONARY_DIR = Path(__file__).resolve().parents[2] / "schemas" / "dictionaries"
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_ONE_MS = timedelta(milliseconds=1)

# ICD section 1.2 resolutions, as decimal places (0 = integer).
_LAT_LON = 6
_DEG = 1
_M = 0
_MPS = 1
_HZ = 1
_HZPS = 3
_DB = 1
_S = 1


@dataclass(frozen=True, slots=True)
class UdpOutputConfig:
    enabled: bool = False
    destination_address: str = "127.0.0.1"
    destination_port: int = 31_001
    source_address: str | None = None
    maximum_datagram_bytes: int = 1_472          # one Ethernet frame, no IP fragmentation
    heartbeat_interval_s: float = 10.0
    snapshot_interval_s: float = 60.0
    maximum_opportunities_per_cue: int = 8
    encoding: CueEncoding = "json"
    dictionary_id: int = 1


@dataclass(frozen=True, slots=True)
class PublicationResult:
    message_id: str
    sequence_number: int
    bytes_sent: int
    opportunities_sent: int | None = None        # track_cue only
    opportunities_shed: int = 0                  # dropped to fit the datagram limit


# ------------------------------------------------------------------ dictionaries and framing

def dictionary_path(dictionary_id: int, directory: Path = DICTIONARY_DIR) -> Path:
    return directory / f"cue-dictionary-{dictionary_id}.bin"


def load_dictionary(dictionary_id: int, directory: Path = DICTIONARY_DIR) -> bytes:
    """Read a released compression dictionary and check it against its .sha256 file."""

    path = dictionary_path(dictionary_id, directory)
    data = path.read_bytes()
    expected = path.with_suffix(".sha256").read_text(encoding="utf-8").split()[0]
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise ValueError(f"{path.name}: SHA-256 {actual} does not match the released {expected}")
    if len(data) > MAXIMUM_DICTIONARY_BYTES:
        raise ValueError(f"{path.name}: {len(data)} bytes exceeds {MAXIMUM_DICTIONARY_BYTES}")
    return data


def load_dictionaries(directory: Path = DICTIONARY_DIR) -> dict[int, bytes]:
    """Every released dictionary in a directory, by id (for receivers)."""

    result: dict[int, bytes] = {}
    for path in sorted(directory.glob("cue-dictionary-*.bin")):
        dictionary_id = int(path.stem.rsplit("-", 1)[1])
        result[dictionary_id] = load_dictionary(dictionary_id, directory)
    return result


class CueFramer:
    """Frames one JSON message per datagram, plain or compressed (ICD section 1.3).

    The framing is fixed when CT starts; receivers detect it per datagram.
    """

    def __init__(
        self,
        encoding: CueEncoding = "json",
        dictionary_id: int = 1,
        *,
        dictionary: bytes | None = None,
        dictionary_dir: Path = DICTIONARY_DIR,
    ) -> None:
        if encoding not in ("json", "deflate_dictionary"):
            raise ValueError(f"Unknown cue encoding {encoding!r}")
        if not 1 <= dictionary_id <= 255:
            raise ValueError("dictionary_id must be between 1 and 255")
        self.encoding: CueEncoding = encoding
        self.dictionary_id = dictionary_id
        self._dictionary = b""
        if encoding == "deflate_dictionary":
            self._dictionary = (
                dictionary
                if dictionary is not None
                else load_dictionary(dictionary_id, dictionary_dir)
            )

    def encode(self, message: bytes) -> bytes:
        if self.encoding == "json":
            return message
        compressor = zlib.compressobj(
            9, zlib.DEFLATED, -15, 9, zlib.Z_DEFAULT_STRATEGY, self._dictionary
        )
        body = compressor.compress(message) + compressor.flush()
        return bytes((COMPRESSED_FRAME_TAG, self.dictionary_id)) + body


def decode_datagram(datagram: bytes, dictionaries: Mapping[int, bytes]) -> bytes:
    """Return the UTF-8 JSON message carried by one plain or compressed datagram."""

    if not datagram:
        raise ValueError("Empty datagram")
    tag = datagram[0]
    if tag == PLAIN_FRAME_FIRST_BYTE:
        return datagram
    if tag != COMPRESSED_FRAME_TAG:
        raise ValueError(f"Unknown frame tag 0x{tag:02X}")
    if len(datagram) < 3:
        raise ValueError("Truncated compressed datagram")
    dictionary = dictionaries.get(datagram[1])
    if dictionary is None:
        raise ValueError(f"Unknown dictionary id {datagram[1]}")
    decompressor = zlib.decompressobj(-15, zdict=dictionary)
    message = decompressor.decompress(datagram[2:]) + decompressor.flush()
    if not decompressor.eof:
        raise ValueError("Truncated deflate stream")
    return message


# ------------------------------------------------------------------ serialization

class CueSerializer:
    """Map prediction-domain objects to CT 2.0.0 messages (finite, JSON-safe dicts)."""

    def __init__(
        self,
        *,
        maximum_opportunities: int | None = None,
        include_summary: bool = False,
        assumed_rcs_dbsm: float = DEFAULT_RCS_DBSM,
        detection_threshold_db: float = -10.0,
    ) -> None:
        # None publishes every usable opportunity; a limit keeps the N with the highest
        # peak window SNR. The publisher may shed more to fit one datagram.
        self.maximum_opportunities = maximum_opportunities
        # summary is a debugging aid, off by default and fixed for the whole run (ICD 2.3).
        self.include_summary = include_summary
        self.assumed_rcs_dbsm = assumed_rcs_dbsm
        self.detection_threshold_db = detection_threshold_db

    def track_cue(
        self,
        prediction: TrackPrediction,
        *,
        source_instance_id: str,
        sequence_number: int,
        message_id: str,
        generated_utc: datetime,
        snapshot_id: str | None = None,
        opportunity_limit: int | None = None,
    ) -> dict[str, object]:
        state = prediction.state
        if state is None:
            raise ValueError("Cannot serialize an invalid prediction as a track cue")
        opportunities = self.ranked_opportunities(prediction)
        if opportunity_limit is not None:
            opportunities = opportunities[: max(opportunity_limit, 0)]
        validation = prediction.validation
        return {
            **_envelope(
                "track_cue", source_instance_id, sequence_number, message_id, generated_utc
            ),
            "snapshot_id": snapshot_id,
            "track": {
                "track_id": prediction.track_id,
                "icao": prediction.icao,
                "callsign": prediction.callsign,
                "status": prediction.status.value,
                "last_report_utc_ms": _utc_ms(state.epoch_utc),
                "report_age_s": _round(
                    max(_seconds_between(generated_utc, state.epoch_utc), 0.0), _S
                ),
                "state": {
                    "epoch_utc_ms": _utc_ms(state.epoch_utc),
                    "reference_frame": "ENU",
                    "reference_origin_id": state.reference_origin_id,
                    "position_enu_m": [
                        _round(state.position_enu_m.east_m, _M),
                        _round(state.position_enu_m.north_m, _M),
                        _round(state.position_enu_m.up_m, _M),
                    ],
                    "velocity_enu_mps": [
                        _round(state.velocity_enu_mps.east_mps, _MPS),
                        _round(state.velocity_enu_mps.north_mps, _MPS),
                        _round(state.velocity_enu_mps.up_mps, _MPS),
                    ],
                    "latitude_deg": _round(state.latitude_deg, _LAT_LON),
                    "longitude_deg": _round(state.longitude_deg, _LAT_LON),
                    "altitude_m_msl": _round(state.altitude_m, _M),
                    "ground_speed_mps": _round_or_none(state.ground_speed_mps, _MPS),
                    "track_angle_deg": _round_or_none(state.track_angle_deg, _DEG),
                    "vertical_rate_mps": _round_or_none(state.vertical_rate_mps, _MPS),
                    "quality": {
                        "vertical_rate_assumed": state.vertical_rate_assumed,
                        "horizontal_velocity_estimated": False,
                        "position_valid": True,
                        "velocity_valid": True,
                    },
                },
            },
            "prediction": {
                "revision": prediction.revision,
                "created_utc_ms": _utc_ms(prediction.created_utc),
                "valid_until_utc_ms": _utc_ms(prediction.valid_until_utc),
                "horizon_s": _round(prediction.horizon_s, _S),
                "sample_interval_s": _round(prediction.sample_interval_s, _S),
                "motion_model": "constant_velocity_enu",
                "maturity": prediction.maturity.value,
                "update_reason": prediction.update_reason.value,
                "validation": {
                    "position_error_m": _round_or_none(validation.position_error_m, _M),
                    "position_error_threshold_m": _round_or_none(
                        validation.position_error_threshold_m, _M
                    ),
                    "velocity_error_mps": _round_or_none(validation.velocity_error_mps, _MPS),
                    "velocity_error_threshold_mps": _round_or_none(
                        validation.velocity_error_threshold_mps, _MPS
                    ),
                    "heading_change_deg": _round_or_none(validation.heading_change_deg, _DEG),
                    "heading_change_threshold_deg": _round_or_none(
                        validation.heading_change_threshold_deg, _DEG
                    ),
                },
            },
            "models": self._models(prediction),
            "opportunities": [self._opportunity(item) for item in opportunities],
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
            **_envelope(
                "track_cue_withdrawal", source_instance_id, sequence_number, message_id,
                generated_utc,
            ),
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
            **_envelope(
                "cue_heartbeat", source_instance_id, sequence_number, message_id, generated_utc
            ),
            "status": status,
            "active_tracks": active_tracks,
            "cue_eligible_tracks": cue_eligible_tracks,
            "active_observers": active_observers,
            "enabled_emitters": enabled_emitters,
            "udp_destination": udp_destination,
            "last_full_snapshot_utc_ms": (
                None if last_full_snapshot_utc is None else _utc_ms(last_full_snapshot_utc)
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
        message_type = "cue_snapshot_begin" if begin else "cue_snapshot_end"
        payload: dict[str, object] = {
            **_envelope(
                message_type, source_instance_id, sequence_number, message_id, generated_utc
            ),
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

    def _models(self, prediction: TrackPrediction) -> dict[str, object]:
        # One models block per cue: the values are the same for every opportunity.
        rcs = self.assumed_rcs_dbsm
        threshold = self.detection_threshold_db
        if prediction.opportunities:
            rcs = prediction.opportunities[0].assumed_rcs_dbsm
            threshold = prediction.opportunities[0].detection_threshold_db
        return {
            "bistatic_range_definition": "tx_target_plus_target_rx_minus_tx_rx",
            "doppler_source": "analytic_derivative_of_bistatic_range",
            "doppler_sign_convention": "positive_for_decreasing_bistatic_path",
            "snr_model_id": "console_bisnr_v1",
            "assumed_rcs_dbsm": _round(rcs, _DB),
            "detection_threshold_db": _round(threshold, _DB),
        }

    def _opportunity(self, opportunity: ObservationOpportunity) -> dict[str, object]:
        current = opportunity.current
        if current is None:  # cannot happen for a usable opportunity: windows imply samples
            raise ValueError(f"Opportunity {opportunity.emitter_id} has windows but no samples")
        payload: dict[str, object] = {
            "observer_id": opportunity.observer_id,
            "emitter_id": opportunity.emitter_id,
            "carrier_frequency_hz": _round(opportunity.emitter.center_frequency_mhz * 1e6, _HZ),
            "rf_channel": _rf_channel_or_none(opportunity.emitter.rf_channel),
            "current": self._sample(current),
            "windows": [self._window(item) for item in opportunity.windows],
        }
        if self.include_summary:
            payload["summary"] = self._summary(opportunity)
        return payload

    def _summary(self, opportunity: ObservationOpportunity) -> dict[str, object]:
        samples = opportunity.samples
        windows = opportunity.windows
        peak = max(samples, key=lambda item: item.predicted_bistatic_snr_db) if samples else None
        ranges = [item.bistatic_range_m for item in samples]
        dopplers = [item.bistatic_doppler_hz for item in samples]
        return {
            "has_usable_window": bool(windows),
            "next_window_start_utc_ms": _utc_ms(windows[0].start_utc) if windows else None,
            "next_window_end_utc_ms": _utc_ms(windows[0].end_utc) if windows else None,
            "total_usable_duration_s": _round(sum(item.duration_s for item in windows), _S),
            "maximum_snr_db": (
                None if peak is None else _round(peak.predicted_bistatic_snr_db, _DB)
            ),
            "maximum_snr_utc_ms": None if peak is None else _utc_ms(peak.sample_utc),
            "minimum_bistatic_range_m": _round(min(ranges), _M) if ranges else None,
            "maximum_bistatic_range_m": _round(max(ranges), _M) if ranges else None,
            "minimum_bistatic_doppler_hz": _round(min(dopplers), _HZ) if dopplers else None,
            "maximum_bistatic_doppler_hz": _round(max(dopplers), _HZ) if dopplers else None,
        }

    def _sample(self, sample: ObservationSample) -> dict[str, object]:
        return {
            "time_offset_s": _round(sample.time_offset_s, _S),
            "sample_utc_ms": _utc_ms(sample.sample_utc),
            "bistatic_range_m": _round(sample.bistatic_range_m, _M),
            "bistatic_range_rate_mps": _round(sample.bistatic_range_rate_mps, _MPS),
            "bistatic_doppler_hz": _round(sample.bistatic_doppler_hz, _HZ),
            "predicted_bistatic_snr_db": _round(sample.predicted_bistatic_snr_db, _DB),
            "geometrically_visible": sample.geometrically_visible,
            "rf_available": sample.rf_available,
            "within_range_limits": sample.within_range_limits,
            "within_doppler_limits": sample.within_doppler_limits,
            "above_snr_threshold": sample.above_snr_threshold,
            "usable": sample.usable,
        }

    def _window(self, window: ObservationWindow) -> dict[str, object]:
        return {
            "start_utc_ms": _utc_ms(window.start_utc),
            "end_utc_ms": _utc_ms(window.end_utc),
            "duration_s": _round(window.duration_s, _S),
            "entry_reason": window.entry_reason,
            "exit_reason": window.exit_reason,
            "min_bistatic_range_m": _round(window.min_bistatic_range_m, _M),
            "max_bistatic_range_m": _round(window.max_bistatic_range_m, _M),
            "min_bistatic_range_rate_mps": _round(window.min_bistatic_range_rate_mps, _MPS),
            "max_bistatic_range_rate_mps": _round(window.max_bistatic_range_rate_mps, _MPS),
            "min_bistatic_doppler_hz": _round(window.min_bistatic_doppler_hz, _HZ),
            "max_bistatic_doppler_hz": _round(window.max_bistatic_doppler_hz, _HZ),
            "maximum_abs_doppler_rate_hzps": _round_or_none(
                window.maximum_abs_doppler_rate_hzps, _HZPS
            ),
            "min_snr_db": _round(window.min_snr_db, _DB),
            "mean_snr_db": _round(window.mean_snr_db, _DB),
            "max_snr_db": _round(window.max_snr_db, _DB),
            "peak_snr_utc_ms": _utc_ms(window.peak_snr_utc),
        }


# ------------------------------------------------------------------ publishing

class UdpCuePublisher:
    """A synchronous UDP publisher with serialized sequence allocation.

    Every datagram is framed per the run's encoding and must fit maximum_datagram_bytes.
    A track_cue that doesn't fit sheds its lowest-ranked opportunities until it does.
    """

    def __init__(
        self,
        config: UdpOutputConfig,
        *,
        source_instance_id: str,
        serializer: CueSerializer | None = None,
        framer: CueFramer | None = None,
        clock: Callable[[], datetime] | None = None,
        id_provider: Callable[[], str] | None = None,
        udp_socket: socket.socket | None = None,
    ) -> None:
        self.config = config
        self.source_instance_id = source_instance_id
        self.serializer = serializer or CueSerializer(
            maximum_opportunities=config.maximum_opportunities_per_cue
        )
        self.framer = framer or CueFramer(config.encoding, config.dictionary_id)
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
        self.shed_opportunity_count = 0

    def close(self) -> None:
        self._socket.close()

    def publish_prediction(
        self,
        prediction: TrackPrediction,
        *,
        snapshot_id: str | None = None,
    ) -> PublicationResult | None:
        if not self.config.enabled:
            return None
        with self._lock:
            sequence, message_id, now = self._next_envelope()
            available = len(self.serializer.ranked_opportunities(prediction))
            limit = available
            while True:
                payload = self.serializer.track_cue(
                    prediction,
                    source_instance_id=self.source_instance_id,
                    sequence_number=sequence,
                    message_id=message_id,
                    generated_utc=now,
                    snapshot_id=snapshot_id,
                    opportunity_limit=limit,
                )
                datagram = self.framer.encode(self.serializer.to_json_bytes(payload))
                if len(datagram) <= self.config.maximum_datagram_bytes:
                    break
                if limit == 0:
                    self.oversize_count += 1
                    self.failure_count += 1
                    return None
                limit -= 1
            shed = available - limit
            self.shed_opportunity_count += shed
            if not self._send(datagram):
                return None
            return PublicationResult(
                message_id=message_id,
                sequence_number=sequence,
                bytes_sent=len(datagram),
                opportunities_sent=limit,
                opportunities_shed=shed,
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
        self, make_payload: Callable[[int, str, datetime], dict[str, object]]
    ) -> PublicationResult | None:
        if not self.config.enabled:
            return None
        with self._lock:
            sequence, message_id, now = self._next_envelope()
            payload = make_payload(sequence, message_id, now)
            datagram = self.framer.encode(self.serializer.to_json_bytes(payload))
            if len(datagram) > self.config.maximum_datagram_bytes:
                self.oversize_count += 1
                self.failure_count += 1
                return None
            if not self._send(datagram):
                return None
            return PublicationResult(
                message_id=message_id, sequence_number=sequence, bytes_sent=len(datagram)
            )

    def _next_envelope(self) -> tuple[int, str, datetime]:
        self._sequence += 1
        return self._sequence, self._id_provider(), self._clock()

    def _send(self, datagram: bytes) -> bool:
        try:
            self._socket.sendto(
                datagram, (self.config.destination_address, self.config.destination_port)
            )
        except OSError:
            self.failure_count += 1
            return False
        self.sent_count += 1
        return True


# ------------------------------------------------------------------ helpers

def _envelope(
    message_type: str,
    source_instance_id: str,
    sequence_number: int,
    message_id: str,
    generated_utc: datetime,
) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "message_type": message_type,
        "message_id": message_id,
        "source": SOURCE_NAME,
        "source_instance_id": source_instance_id,
        "sequence_number": sequence_number,
        "generated_utc_ms": _utc_ms(generated_utc),
    }


def _round(value: float, decimals: int) -> float | int:
    """Round to the ICD resolution; whole-unit resolutions are sent as integers."""
    if decimals == 0:
        return round(value)
    return round(value, decimals)


def _round_or_none(value: float | None, decimals: int) -> float | int | None:
    return None if value is None else _round(value, decimals)


def _peak_window_snr_db(opportunity: ObservationOpportunity) -> float:
    return max(window.max_snr_db for window in opportunity.windows)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _utc_ms(value: datetime) -> int:
    """Milliseconds since 1970-01-01T00:00:00Z (ICD section 1.2), exact integer arithmetic."""
    return (_as_utc(value) - _EPOCH) // _ONE_MS


def _seconds_between(later: datetime, earlier: datetime) -> float:
    return (_as_utc(later) - _as_utc(earlier)).total_seconds()


def _rf_channel_or_none(value: object) -> int | None:
    """Normalize source CSV channel values to the wire contract (integer 2-69 or null)."""

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
