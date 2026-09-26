from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from adsb_console.bistatic import DtvEmitter
from adsb_console.cue import (
    COMPRESSED_FRAME_TAG,
    SCHEMA_VERSION,
    CueFramer,
    CueSerializer,
    UdpCuePublisher,
    UdpOutputConfig,
    decode_datagram,
    load_dictionaries,
    load_dictionary,
)
from adsb_console.models import (
    ObserverConfig,
    ObserverRole,
    PositionReport,
    TrackState,
    VelocityReport,
)
from adsb_console.prediction import (
    PredictionConfig,
    PredictionRevisionManager,
    PredictionUpdateReason,
    TrackPrediction,
    build_track_prediction,
)

SCHEMAS = Path(__file__).parents[1] / "schemas"
MESSAGE_ID = "123e4567-e89b-12d3-a456-426614174000"
GENERATED = datetime(2025, 1, 1, 12, 0, 1, 250_000, tzinfo=UTC)
ONE_FRAME = 1472


def _validator(message_type: str) -> Any:
    names = {
        "track_cue": "track-cue",
        "track_cue_withdrawal": "track-cue-withdrawal",
        "cue_heartbeat": "cue-heartbeat",
        "cue_snapshot_begin": "cue-snapshot-begin",
        "cue_snapshot_end": "cue-snapshot-end",
    }
    schema = json.loads((SCHEMAS / f"{names[message_type]}-{SCHEMA_VERSION}.json").read_text())
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _errors(payload: dict[str, object]) -> list[str]:
    return [e.message for e in _validator(str(payload["message_type"])).iter_errors(payload)]


def _cue(serializer: CueSerializer, prediction: TrackPrediction, **kwargs: Any) -> dict[str, Any]:
    return cast(dict[str, Any], serializer.track_cue(
        prediction,
        source_instance_id="test-source",
        sequence_number=1,
        message_id=MESSAGE_ID,
        generated_utc=GENERATED,
        **kwargs,
    ))


def test_track_cue_validates_against_2_0_0_schema() -> None:
    payload = _cue(CueSerializer(), _prediction_with_usable_window())

    assert payload["schema_version"] == "2.0.0"
    assert not _errors(payload)


def test_track_cue_with_debug_summary_validates_and_has_it_on_every_opportunity() -> None:
    prediction = _prediction_with_ranked_opportunities([3.0, 1.0])
    payload = _cue(CueSerializer(include_summary=True), prediction)

    assert not _errors(payload)
    assert all("summary" in item for item in payload["opportunities"])
    assert "summary" not in _cue(CueSerializer(), prediction)["opportunities"][0]


def test_track_cue_drops_derivable_fields_and_sends_models_once() -> None:
    payload = _cue(CueSerializer(), _prediction_with_ranked_opportunities([3.0, 1.0]))
    opportunity = payload["opportunities"][0]

    assert "prediction_id" not in payload["prediction"]
    assert set(opportunity) == {
        "observer_id", "emitter_id", "carrier_frequency_hz", "rf_channel", "current", "windows"
    }
    assert "window_id" not in opportunity["windows"][0]
    assert payload["models"]["snr_model_id"] == "console_bisnr_v1"


def test_track_cue_uses_epoch_milliseconds_and_icd_resolutions() -> None:
    payload = _cue(CueSerializer(), _prediction_with_usable_window())
    state = payload["track"]["state"]
    window = payload["opportunities"][0]["windows"][0]

    assert payload["generated_utc_ms"] == 1_735_732_801_250
    assert payload["track"]["last_report_utc_ms"] == 1_735_732_800_000
    assert isinstance(window["start_utc_ms"], int)
    assert isinstance(window["min_bistatic_range_m"], int)
    assert all(isinstance(value, int) for value in state["position_enu_m"])
    assert state["latitude_deg"] == round(state["latitude_deg"], 6)
    assert window["max_snr_db"] == round(window["max_snr_db"], 1)
    assert payload["track"]["report_age_s"] == 1.2


def test_track_cue_opportunities_all_have_the_same_shape() -> None:
    """ICD 1.2: identical keys so MATLAB jsondecode returns a struct array."""
    payload = _cue(CueSerializer(), _prediction_with_ranked_opportunities([5.0, 2.0, 1.0]))
    shapes = {tuple(sorted(item)) for item in payload["opportunities"]}
    current_shapes = {tuple(sorted(item["current"])) for item in payload["opportunities"]}

    assert len(shapes) == 1
    assert len(current_shapes) == 1


def test_track_cue_omits_unusable_or_unreceivable_opportunities() -> None:
    prediction = _prediction_with_usable_window()
    usable = prediction.opportunities[0]
    unreceivable = replace(usable, emitter_id="disabled-receiver", observer_can_receive=False)
    no_window = replace(usable, emitter_id="no-window", windows=())

    payload = _cue(
        CueSerializer(), replace(prediction, opportunities=(usable, unreceivable, no_window))
    )

    assert [item["emitter_id"] for item in payload["opportunities"]] == [usable.emitter_id]


def test_track_cue_keeps_track_state_and_models_when_no_opportunity_is_usable() -> None:
    payload = _cue(CueSerializer(), replace(_prediction_with_usable_window(), opportunities=()))

    assert payload["opportunities"] == []
    assert isinstance(payload["track"], dict)
    assert payload["models"]["detection_threshold_db"] == -10.0
    assert not _errors(payload)


def test_track_cue_normalizes_rf_channel_to_frozen_wire_type() -> None:
    prediction = _prediction_with_usable_window()
    opportunity = replace(
        prediction.opportunities[0],
        emitter=replace(prediction.opportunities[0].emitter, rf_channel="20"),  # type: ignore[arg-type]
    )
    payload = _cue(CueSerializer(), replace(prediction, opportunities=(opportunity,)))

    assert payload["opportunities"][0]["rf_channel"] == 20


def test_track_cue_keeps_top_opportunities_by_peak_window_snr() -> None:
    prediction = _prediction_with_ranked_opportunities([-9.0, 4.0, -2.0, 12.0, 0.5])

    capped = _cue(CueSerializer(maximum_opportunities=3), prediction)
    uncapped = _cue(CueSerializer(), prediction)

    assert _emitter_ids(capped) == ["emitter-3", "emitter-1", "emitter-4"]
    assert len(_emitter_ids(uncapped)) == 5


def test_other_messages_validate_against_2_0_0_schemas() -> None:
    serializer = CueSerializer()
    common: dict[str, Any] = {
        "source_instance_id": "test-source",
        "sequence_number": 1,
        "message_id": MESSAGE_ID,
        "generated_utc": GENERATED,
    }
    payloads = [
        serializer.withdrawal(track_id="adsb:ABC123", icao="ABC123", revision=1,
                              reason="track_purged", **common),
        serializer.heartbeat(active_tracks=2, cue_eligible_tracks=1, active_observers=1,
                             enabled_emitters=16, udp_destination="239.192.10.1:31986",
                             last_full_snapshot_utc=None, status="starting", **common),
        serializer.snapshot_boundary(begin=True, snapshot_id="snapshot:x",
                                     expected_track_count=0, **common),
        serializer.snapshot_boundary(begin=False, snapshot_id="snapshot:x",
                                     published_track_count=0, failed_track_count=0, **common),
    ]
    for payload in payloads:
        assert not _errors(payload), payload["message_type"]


# ------------------------------------------------------------------ framing and publishing

def test_plain_framing_is_the_json_itself_and_decodes_unchanged() -> None:
    message = b'{"a":1}'
    datagram = CueFramer("json").encode(message)

    assert datagram == message
    assert decode_datagram(datagram, {}) == message


def test_compressed_framing_round_trips_with_the_dictionary() -> None:
    dictionary = b'{"message_type":"track_cue","opportunities":[' * 20
    message = b'{"message_type":"track_cue","opportunities":[]}'
    datagram = CueFramer("deflate_dictionary", 7, dictionary=dictionary).encode(message)

    assert datagram[0] == COMPRESSED_FRAME_TAG
    assert datagram[1] == 7
    assert decode_datagram(datagram, {7: dictionary}) == message


def test_decode_rejects_unknown_tags_and_dictionaries() -> None:
    datagram = CueFramer("deflate_dictionary", 9, dictionary=b"abc").encode(b'{"a":1}')

    with pytest.raises(ValueError, match="Unknown dictionary id 9"):
        decode_datagram(datagram, {1: b"abc"})
    with pytest.raises(ValueError, match="Unknown frame tag"):
        decode_datagram(b"\x00junk", {})


def test_released_dictionary_one_matches_its_checksum_and_fits_a_cue_in_one_frame() -> None:
    dictionary = load_dictionary(1)
    assert len(dictionary) <= 32_768
    assert load_dictionaries()[1] == dictionary

    prediction = _prediction_with_ranked_opportunities([6.0, 5.0, 4.0, 3.0, 2.0, 1.0, 0.5, 0.2])
    message = json.dumps(_cue(CueSerializer(), prediction), separators=(",", ":"),
                         sort_keys=True).encode()
    datagram = CueFramer("deflate_dictionary", 1).encode(message)

    assert len(message) > ONE_FRAME
    assert len(datagram) <= ONE_FRAME
    assert decode_datagram(datagram, {1: dictionary}) == message


def test_load_dictionary_rejects_a_checksum_mismatch(tmp_path: Path) -> None:
    (tmp_path / "cue-dictionary-3.bin").write_bytes(b"dictionary")
    (tmp_path / "cue-dictionary-3.sha256").write_text(
        hashlib.sha256(b"other").hexdigest() + "  cue-dictionary-3.bin\n"
    )

    with pytest.raises(ValueError, match="does not match"):
        load_dictionary(3, tmp_path)


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


def _publisher(config: UdpOutputConfig, capture: _CaptureSocket) -> UdpCuePublisher:
    return UdpCuePublisher(
        config,
        source_instance_id="test-source",
        clock=lambda: GENERATED,
        udp_socket=capture,  # type: ignore[arg-type]
    )


def test_udp_publisher_numbers_messages_and_validates_after_decoding() -> None:
    capture = _CaptureSocket()
    publisher = _publisher(
        UdpOutputConfig(enabled=True, encoding="deflate_dictionary", dictionary_id=1), capture
    )

    assert publisher.publish_snapshot_boundary(
        begin=True, snapshot_id="snapshot:test", expected_track_count=0
    )
    assert publisher.publish_snapshot_boundary(
        begin=False, snapshot_id="snapshot:test", published_track_count=0, failed_track_count=0
    )
    assert publisher.publish_heartbeat(
        active_tracks=0, cue_eligible_tracks=0, active_observers=1, enabled_emitters=1,
        last_full_snapshot_utc=GENERATED, status="running",
    )
    assert publisher.publish_prediction(_prediction_with_usable_window())

    dictionaries = load_dictionaries()
    payloads = [json.loads(decode_datagram(item, dictionaries)) for item in capture.payloads]
    assert all(item[0] == COMPRESSED_FRAME_TAG for item in capture.payloads)
    assert [item["sequence_number"] for item in payloads] == [1, 2, 3, 4]
    for payload in payloads:
        assert not _errors(payload), payload["message_type"]


def test_udp_publisher_applies_configured_opportunity_limit() -> None:
    capture = _CaptureSocket()
    publisher = UdpCuePublisher(
        UdpOutputConfig(
            enabled=True, maximum_opportunities_per_cue=2, maximum_datagram_bytes=65_507
        ),
        source_instance_id="test-source",
        udp_socket=capture,  # type: ignore[arg-type]
    )

    result = publisher.publish_prediction(
        _prediction_with_ranked_opportunities([1.0, 5.0, 3.0, -4.0])
    )

    assert result is not None
    assert result.opportunities_sent == 2
    assert _emitter_ids(json.loads(capture.payloads[0])) == ["emitter-1", "emitter-2"]


def test_udp_publisher_sheds_weakest_opportunities_to_fit_the_datagram() -> None:
    prediction = _prediction_with_ranked_opportunities([6.0, 5.0, 4.0, 3.0])
    full = len(json.dumps(_cue(CueSerializer(), prediction), separators=(",", ":")))
    capture = _CaptureSocket()
    publisher = _publisher(
        UdpOutputConfig(enabled=True, maximum_datagram_bytes=full - 1), capture
    )

    result = publisher.publish_prediction(prediction)

    assert result is not None
    assert result.opportunities_shed >= 1
    assert result.opportunities_sent == 4 - result.opportunities_shed
    assert len(capture.payloads[0]) <= full - 1
    assert _emitter_ids(json.loads(capture.payloads[0]))[0] == "emitter-0"
    assert publisher.shed_opportunity_count == result.opportunities_shed


def test_udp_publisher_drops_a_cue_that_cannot_fit_even_without_opportunities() -> None:
    capture = _CaptureSocket()
    publisher = _publisher(UdpOutputConfig(enabled=True, maximum_datagram_bytes=100), capture)

    assert publisher.publish_prediction(_prediction_with_usable_window()) is None
    assert capture.payloads == []
    assert (publisher.oversize_count, publisher.failure_count) == (1, 1)


# ------------------------------------------------------------------ fixtures

def _emitter_ids(payload: dict[str, Any]) -> list[object]:
    return [item["emitter_id"] for item in payload["opportunities"]]


def _prediction_with_ranked_opportunities(peak_snrs_db: list[float]) -> TrackPrediction:
    prediction = _prediction_with_usable_window()
    base = prediction.opportunities[0]
    opportunities = tuple(
        replace(
            base,
            emitter_id=f"emitter-{index}",
            windows=(replace(base.windows[0], max_snr_db=peak_snr_db),),
        )
        for index, peak_snr_db in enumerate(peak_snrs_db)
    )
    return replace(prediction, opportunities=opportunities)


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
