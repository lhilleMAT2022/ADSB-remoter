from __future__ import annotations

from datetime import datetime, timedelta
from math import isclose

from adsb_console.bistatic import DtvEmitter, bistatic_measurement
from adsb_console.models import (
    ObserverConfig,
    ObserverRole,
    PositionReport,
    TrackState,
    VelocityReport,
)
from adsb_console.prediction import (
    PredictionConfig,
    PredictionMaturity,
    PredictionRevisionManager,
    PredictionTriggerEvaluator,
    PredictionUpdateReason,
    build_track_prediction,
)


def test_constant_velocity_prediction_reuses_current_bistatic_model() -> None:
    epoch = datetime(2025, 1, 1, 12, 0, 0)
    observer = _observer("receiver")
    emitter = _emitter()
    track = _track(epoch)
    config = PredictionConfig(
        enabled=True,
        prediction_horizon_s=20.0,
        prediction_sample_interval_s=10.0,
        minimum_track_history_s=0.0,
        detection_threshold_db=-100.0,
    )
    token = PredictionRevisionManager().request("adsb:ABC123")

    prediction = build_track_prediction(
        track=track,
        reference_origin=observer,
        observers=[observer],
        emitters=[emitter],
        config=config,
        token=token,
        created_utc=epoch,
        update_reason=PredictionUpdateReason.INITIAL_TRACK,
    )

    assert prediction.state is not None
    assert prediction.maturity is PredictionMaturity.STABILIZING
    assert len(prediction.opportunities) == 1
    samples = prediction.opportunities[0].samples
    assert [sample.time_offset_s for sample in samples] == [0.0, 10.0, 20.0]
    assert track.last_position is not None
    assert track.last_velocity is not None
    current = bistatic_measurement(
        emitter=emitter,
        receiver=observer,
        position=track.last_position,
        velocity=track.last_velocity,
    )
    assert current is not None
    assert isclose(samples[0].bistatic_range_m, current.bistatic_range_m or 0.0)
    assert isclose(
        samples[0].bistatic_range_rate_mps,
        current.bistatic_range_rate_mps or 0.0,
    )
    assert isclose(samples[0].bistatic_doppler_hz, current.bistatic_doppler_hz or 0.0)


def test_historical_reports_are_invalid_until_playback_rebases_timestamps() -> None:
    recorded_epoch = datetime(2022, 4, 13, 6, 8, 50)
    live_epoch = datetime(2026, 9, 25, 12, 0, 0)
    observer = _observer("receiver")
    config = PredictionConfig(enabled=True, minimum_track_history_s=0.0)

    stale_prediction = build_track_prediction(
        track=_track(recorded_epoch),
        reference_origin=observer,
        observers=[observer],
        emitters=[_emitter()],
        config=config,
        token=PredictionRevisionManager().request("adsb:ABC123"),
        created_utc=live_epoch,
        update_reason=PredictionUpdateReason.INITIAL_TRACK,
    )
    rebased_prediction = build_track_prediction(
        track=_track(live_epoch),
        reference_origin=observer,
        observers=[observer],
        emitters=[_emitter()],
        config=config,
        token=PredictionRevisionManager().request("adsb:ABC123"),
        created_utc=live_epoch,
        update_reason=PredictionUpdateReason.INITIAL_TRACK,
    )

    assert stale_prediction.state is None
    assert stale_prediction.maturity is PredictionMaturity.INVALID
    assert rebased_prediction.state is not None


def test_revision_manager_rejects_superseded_async_result() -> None:
    manager = PredictionRevisionManager()
    first = manager.request("adsb:ABC123")
    second = manager.request("adsb:ABC123")

    assert not manager.is_current(first)
    assert manager.is_current(second)
    manager.invalidate("adsb:ABC123")
    assert not manager.is_current(second)


def test_trigger_evaluator_requests_periodic_refresh() -> None:
    epoch = datetime(2025, 1, 1, 12, 0, 0)
    observer = _observer("receiver")
    track = _track(epoch)
    config = PredictionConfig(
        enabled=True,
        prediction_horizon_s=60.0,
        prediction_sample_interval_s=10.0,
        minimum_track_history_s=0.0,
        maximum_prediction_age_s=5.0,
    )
    token = PredictionRevisionManager().request("adsb:ABC123")
    prediction = build_track_prediction(
        track=track,
        reference_origin=observer,
        observers=[observer],
        emitters=[],
        config=config,
        token=token,
        created_utc=epoch,
        update_reason=PredictionUpdateReason.INITIAL_TRACK,
    )

    decision = PredictionTriggerEvaluator(config).evaluate(
        track,
        prediction,
        epoch + timedelta(seconds=6),
    )

    assert decision.regenerate
    assert decision.reason is PredictionUpdateReason.PERIODIC_REFRESH


def _observer(name: str) -> ObserverConfig:
    return ObserverConfig(
        name=name,
        role=ObserverRole.LOCAL,
        latitude_deg=42.0,
        longitude_deg=-71.0,
        altitude_m=100.0,
        receiver_gain_dbi=10.0,
        noise_figure_db=3.0,
        bandwidth_mhz=8.0,
    )


def _emitter() -> DtvEmitter:
    return DtvEmitter(
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


def _track(epoch: datetime) -> TrackState:
    position = PositionReport(
        latitude_deg=42.02,
        longitude_deg=-71.01,
        altitude_ft=10_000.0,
        reported_at=epoch,
    )
    velocity = VelocityReport(
        ground_speed_kt=200.0,
        track_deg=90.0,
        vertical_rate_fpm=0.0,
        reported_at=epoch,
    )
    return TrackState(
        icao="ABC123",
        first_seen=epoch - timedelta(seconds=10),
        last_seen=epoch,
        last_position=position,
        last_velocity=velocity,
    )
