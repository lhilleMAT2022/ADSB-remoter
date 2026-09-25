from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from adsb_console.bistatic import DtvEmitter
from adsb_console.cue import CueSerializer, UdpCuePublisher, UdpOutputConfig
from adsb_console.models import ObserverConfig, ObserverRole, PositionReport, TrackState, VelocityReport
from adsb_console.prediction import (
    PredictionConfig,
    PredictionRevisionManager,
    PredictionUpdateReason,
    TrackPrediction,
    build_track_prediction,
)


def test_track_cue_validates_against_versioned_schema() -> None:
    epoch = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
    observer = ObserverConfig(
        name="receiver",
        role=ObserverRole.LOCAL,
        latitude_deg=42.0,
        longitude_deg=-71.0,
        altitude_m=100.0,
        receiver_gain_dbi=10.0,
    )
    track = TrackState(
        icao="ABC123",
        first_seen=epoch.replace(tzinfo=None),
        last_seen=epoch.replace(tzinfo=None),
        last_position=PositionReport(
            latitude_deg=42.02,
            longitude_deg=-71.01,
            altitude_ft=10_000.0,
            reported_at=epoch.replace(tzinfo=None),
        ),
        last_velocity=VelocityReport(
            ground_speed_kt=200.0,
            track_deg=90.0,
            vertical_rate_fpm=0.0,
            reported_at=epoch.replace(tzinfo=None),
        ),
    )
    emitter = DtvEmitter(
        facility_id="123",
        call_sign="WTEST",
        site_name="Test Tower",
        asrn="456",
        rf_channel=20,
        center_frequency_mhz=509.0,
        latitude_deg=42.05,
        longitude_deg=-71.0,
        altitude_m=300.0,
        eirp_kw=1_000.0,
    )
    prediction = build_track_prediction(
        track=track,
        reference_origin=observer,
        observers=[observer],
        emitters=[emitter],
        config=PredictionConfig(
            enabled=True,
            prediction_horizon_s=20.0,
            prediction_sample_interval_s=10.0,
            minimum_track_history_s=0.0,
            detection_threshold_db=-100.0,
        ),
        token=PredictionRevisionManager().request("adsb:ABC123"),
        created_utc=epoch.replace(tzinfo=None),
        update_reason=PredictionUpdateReason.INITIAL_TRACK,
    )

    payload = CueSerializer().track_cue(
        prediction,
        source_instance_id="test-source",
        sequence_number=1,
        message_id="123e4567-e89b-12d3-a456-426614174000",
        generated_utc=epoch,
        include_history=False,
    )
    schema = json.loads(
        (Path(__file__).parents[1] / "schemas" / "track-cue-1.0.0.json").read_text()
    )

    errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(payload))

    assert not errors, [error.message for error in errors]


def test_track_cue_omits_unusable_or_unreceivable_opportunities() -> None:
    prediction = _prediction_with_usable_window()
    usable = prediction.opportunities[0]
    unreceivable = replace(usable, emitter_id="disabled-receiver", observer_can_receive=False)
    no_window = replace(usable, emitter_id="no-window", windows=())
    filtered_prediction = replace(
        prediction,
        opportunities=(usable, unreceivable, no_window),
    )

    payload = CueSerializer().track_cue(
        filtered_prediction,
        source_instance_id="test-source",
        sequence_number=1,
        message_id="123e4567-e89b-12d3-a456-426614174000",
        generated_utc=datetime(2025, 1, 1, 12, 0, tzinfo=UTC),
        include_history=False,
    )

    opportunities = payload["opportunities"]
    assert isinstance(opportunities, list)
    assert [item["emitter_id"] for item in opportunities if isinstance(item, dict)] == [
        usable.emitter_id
    ]


def test_track_cue_keeps_track_state_when_no_opportunity_is_usable() -> None:
    prediction = _prediction_with_usable_window()
    payload = CueSerializer().track_cue(
        replace(prediction, opportunities=()),
        source_instance_id="test-source",
        sequence_number=1,
        message_id="123e4567-e89b-12d3-a456-426614174000",
        generated_utc=datetime(2025, 1, 1, 12, 0, tzinfo=UTC),
        include_history=False,
    )

    assert payload["opportunities"] == []
    assert isinstance(payload["track"], dict)


def _prediction_with_usable_window() -> TrackPrediction:
    epoch = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
    observer = ObserverConfig(
        name="receiver",
        role=ObserverRole.LOCAL,
        latitude_deg=42.0,
        longitude_deg=-71.0,
        altitude_m=100.0,
        receiver_gain_dbi=10.0,
    )
    track = TrackState(
        icao="ABC123",
        first_seen=epoch.replace(tzinfo=None),
        last_seen=epoch.replace(tzinfo=None),
        last_position=PositionReport(
            latitude_deg=42.02,
            longitude_deg=-71.01,
            altitude_ft=10_000.0,
            reported_at=epoch.replace(tzinfo=None),
        ),
        last_velocity=VelocityReport(
            ground_speed_kt=200.0,
            track_deg=90.0,
            vertical_rate_fpm=0.0,
            reported_at=epoch.replace(tzinfo=None),
        ),
    )
    emitter = DtvEmitter(
        facility_id="123",
        call_sign="WTEST",
        site_name="Test Tower",
        asrn="456",
        rf_channel=20,
        center_frequency_mhz=509.0,
        latitude_deg=42.05,
        longitude_deg=-71.0,
        altitude_m=300.0,
        eirp_kw=1_000.0,
    )
    prediction = build_track_prediction(
        track=track,
        reference_origin=observer,
        observers=[observer],
        emitters=[emitter],
        config=PredictionConfig(
            enabled=True,
            prediction_horizon_s=20.0,
            prediction_sample_interval_s=10.0,
            minimum_track_history_s=0.0,
            detection_threshold_db=-100.0,
        ),
        token=PredictionRevisionManager().request("adsb:ABC123"),
        created_utc=epoch.replace(tzinfo=None),
        update_reason=PredictionUpdateReason.INITIAL_TRACK,
    )

    assert prediction.opportunities[0].windows
    return prediction
class _CaptureSocket:
    def __init__(self) -> None:
        self.payloads: list[bytes] = []

    def bind(self, _address: tuple[str, int]) -> None:
        pass

    def close(self) -> None:
        pass

    def sendto(self, data: bytes, _address: tuple[str, int]) -> int:
        self.payloads.append(data)
        return len(data)


def test_udp_publisher_serializes_sequences_and_snapshot_messages() -> None:
    capture = _CaptureSocket()
    publisher = UdpCuePublisher(
        UdpOutputConfig(enabled=True),
        source_instance_id="test-source",
        clock=lambda: datetime(2025, 1, 1, tzinfo=UTC),
        id_provider=iter(
            [
                "123e4567-e89b-12d3-a456-426614174000",
                "123e4567-e89b-12d3-a456-426614174001",
                "123e4567-e89b-12d3-a456-426614174002",
            ]
        ).__next__,
        udp_socket=capture,  # type: ignore[arg-type]
    )

    assert publisher.publish_snapshot_boundary(
        begin=True, snapshot_id="snapshot:test", expected_track_count=0
    )
    assert publisher.publish_snapshot_boundary(
        begin=False, snapshot_id="snapshot:test", published_track_count=0, failed_track_count=0
    )
    assert publisher.publish_heartbeat(
        active_tracks=0,
        cue_eligible_tracks=0,
        active_observers=1,
        enabled_emitters=1,
        last_full_snapshot_utc=datetime(2025, 1, 1, tzinfo=UTC),
    )

    payloads = [json.loads(item) for item in capture.payloads]
    assert [item["sequence_number"] for item in payloads] == [1, 2, 3]
    schema_dir = Path(__file__).parents[1] / "schemas"
    schemas = {
        "cue_snapshot_begin": "cue-snapshot-boundary-1.0.0.json",
        "cue_snapshot_end": "cue-snapshot-boundary-1.0.0.json",
        "cue_heartbeat": "cue-heartbeat-1.0.0.json",
    }
    for payload in payloads:
        schema = json.loads((schema_dir / schemas[payload["message_type"]]).read_text())
        assert not list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(payload))
