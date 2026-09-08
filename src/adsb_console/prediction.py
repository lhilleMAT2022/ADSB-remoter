"""UI-independent passive-radar cue prediction domain services."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from math import cos, log10, radians
import re

from adsb_console.bistatic import (
    DEFAULT_POLARIZATION_LOSS_DB,
    DEFAULT_RCS_DBSM,
    DEFAULT_SYSTEM_LOSS_DB,
    DtvEmitter,
    bistatic_measurement,
)
from adsb_console.models import ObserverConfig, PositionReport, TrackState, VelocityReport
from adsb_console.transforms import (
    EnuPoint,
    EnuVector,
    is_observable_by,
    position_to_enu,
    position_to_range_az_el,
    velocity_to_observer_enu,
    enu_to_position,
)


class PredictionMaturity(StrEnum):
    INITIAL = "initial"
    STABILIZING = "stabilizing"
    STABLE = "stable"
    INVALID = "invalid"


class PredictionUpdateReason(StrEnum):
    INITIAL_TRACK = "initial_track"
    TRACK_MANEUVER = "track_maneuver"
    PREDICTION_ERROR = "prediction_error"
    PERIODIC_REFRESH = "periodic_refresh"
    OBSERVER_CONFIGURATION_CHANGE = "observer_configuration_change"
    EMITTER_CONFIGURATION_CHANGE = "emitter_configuration_change"
    APPLICATION_SNAPSHOT = "application_snapshot"


class PredictionTrackStatus(StrEnum):
    ACTIVE = "active"
    STALE = "stale"
    PURGED = "purged"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class PredictionConfig:
    """Feature configuration intentionally independent of terminal refresh settings."""

    enabled: bool = False
    prediction_horizon_s: float = 600.0
    prediction_sample_interval_s: float = 10.0
    maximum_prediction_age_s: float = 30.0
    minimum_prediction_horizon_s: float = 30.0
    regeneration_debounce_s: float = 1.0
    minimum_track_history_s: float = 2.0
    stable_track_history_s: float = 30.0
    maximum_adsb_report_age_s: float = 20.0
    maneuver_heading_change_deg: float = 3.0
    maneuver_speed_change_mps: float = 10.0
    maneuver_vertical_rate_change_mps: float = 3.0
    maneuver_position_error_m: float = 1_000.0
    maneuver_altitude_error_m: float = 150.0
    position_error_absolute_m: float = 1_000.0
    position_error_relative_fraction: float = 0.02
    velocity_error_absolute_mps: float = 10.0
    velocity_error_relative_fraction: float = 0.05
    bistatic_range_error_absolute_m: float = 500.0
    bistatic_range_error_relative_fraction: float = 0.01
    bistatic_doppler_error_absolute_hz: float = 10.0
    bistatic_doppler_error_relative_fraction: float = 0.05
    detection_threshold_db: float = -10.0
    maximum_bistatic_range_m: float | None = None
    maximum_abs_bistatic_doppler_hz: float | None = None
    assumed_rcs_dbsm: float = DEFAULT_RCS_DBSM
    polarization_loss_db: float = DEFAULT_POLARIZATION_LOSS_DB
    system_loss_db: float = DEFAULT_SYSTEM_LOSS_DB
    disabled_observer_ids: frozenset[str] = frozenset()
    disabled_emitter_ids: frozenset[str] = frozenset()
    receiver_compatible_emitter_ids: Mapping[str, frozenset[str]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PredictedTrackState:
    epoch_utc: datetime
    reference_origin_id: str
    position_enu_m: EnuPoint
    velocity_enu_mps: EnuVector
    latitude_deg: float
    longitude_deg: float
    altitude_m: float
    ground_speed_mps: float | None
    track_angle_deg: float | None
    vertical_rate_mps: float | None
    vertical_rate_assumed: bool


@dataclass(frozen=True, slots=True)
class ObservationSample:
    time_offset_s: float
    sample_utc: datetime
    bistatic_range_m: float
    bistatic_range_rate_mps: float
    bistatic_doppler_hz: float
    predicted_bistatic_snr_db: float
    geometrically_visible: bool
    rf_available: bool
    within_range_limits: bool
    within_doppler_limits: bool
    above_snr_threshold: bool
    usable: bool


@dataclass(frozen=True, slots=True)
class ObservationWindow:
    start_utc: datetime
    end_utc: datetime
    entry_reason: str
    exit_reason: str
    min_bistatic_range_m: float
    max_bistatic_range_m: float
    min_bistatic_range_rate_mps: float
    max_bistatic_range_rate_mps: float
    min_bistatic_doppler_hz: float
    max_bistatic_doppler_hz: float
    maximum_abs_doppler_rate_hzps: float | None
    min_snr_db: float
    mean_snr_db: float
    max_snr_db: float
    peak_snr_utc: datetime

    @property
    def duration_s(self) -> float:
        return max((self.end_utc - self.start_utc).total_seconds(), 0.0)


@dataclass(frozen=True, slots=True)
class ObservationOpportunity:
    observer_id: str
    observer_name: str
    emitter_id: str
    transmitter_site_id: str
    emitter: DtvEmitter
    observer_can_receive: bool
    emitter_enabled: bool
    assumed_rcs_dbsm: float
    detection_threshold_db: float
    current: ObservationSample | None
    samples: tuple[ObservationSample, ...]
    windows: tuple[ObservationWindow, ...]


@dataclass(frozen=True, slots=True)
class PredictionValidation:
    position_error_m: float | None = None
    position_error_threshold_m: float | None = None
    velocity_error_mps: float | None = None
    velocity_error_threshold_mps: float | None = None
    heading_change_deg: float | None = None
    heading_change_threshold_deg: float | None = None


@dataclass(frozen=True, slots=True)
class TrackPrediction:
    track_id: str
    icao: str
    callsign: str | None
    status: PredictionTrackStatus
    state: PredictedTrackState | None
    prediction_id: str
    revision: int
    created_utc: datetime
    valid_until_utc: datetime
    horizon_s: float
    sample_interval_s: float
    maturity: PredictionMaturity
    update_reason: PredictionUpdateReason
    validation: PredictionValidation
    opportunities: tuple[ObservationOpportunity, ...]
    generation: int


@dataclass(frozen=True, slots=True)
class PredictionToken:
    track_id: str
    generation: int
    revision: int


class PredictionRevisionManager:
    """Own generation tokens so stale asynchronous work cannot be committed."""

    def __init__(self) -> None:
        self._generations: dict[str, int] = {}
        self._revisions: dict[str, int] = {}

    def request(self, track_id: str) -> PredictionToken:
        generation = self._generations.get(track_id, 0) + 1
        revision = self._revisions.get(track_id, 0) + 1
        self._generations[track_id] = generation
        self._revisions[track_id] = revision
        return PredictionToken(track_id=track_id, generation=generation, revision=revision)

    def is_current(self, token: PredictionToken) -> bool:
        return (
            self._generations.get(token.track_id) == token.generation
            and self._revisions.get(token.track_id) == token.revision
        )

    def invalidate(self, track_id: str) -> int:
        generation = self._generations.get(track_id, 0) + 1
        self._generations[track_id] = generation
        return generation

    def latest_revision(self, track_id: str) -> int | None:
        return self._revisions.get(track_id)


@dataclass(frozen=True, slots=True)
class TriggerDecision:
    regenerate: bool
    reason: PredictionUpdateReason | None
    validation: PredictionValidation


class PredictionTriggerEvaluator:
    """Determine regeneration reasons from a prior committed prediction."""

    def __init__(self, config: PredictionConfig) -> None:
        self._config = config
        self._last_regeneration_utc: dict[str, datetime] = {}

    def evaluate(
        self,
        track: TrackState,
        previous: TrackPrediction | None,
        now: datetime,
    ) -> TriggerDecision:
        if previous is None:
            decision = TriggerDecision(
                regenerate=True,
                reason=PredictionUpdateReason.INITIAL_TRACK,
                validation=PredictionValidation(),
            )
            self._remember(track, now, decision)
            return decision
        if previous.state is None or track.last_position is None or track.last_velocity is None:
            decision = TriggerDecision(
                regenerate=True,
                reason=PredictionUpdateReason.PREDICTION_ERROR,
                validation=PredictionValidation(),
            )
            self._remember(track, now, decision)
            return decision

        state = previous.state
        propagated_position = _propagated_state_position(state, track.last_position.reported_at)
        position_error_m = _distance_between_reports(track.last_position, propagated_position)
        position_threshold_m = max(
            self._config.position_error_absolute_m,
            self._config.position_error_relative_fraction
            * max(_enu_norm(state.position_enu_m), 1.0),
        )
        heading_change_deg = _heading_difference(
            track.last_velocity.track_deg,
            state.track_angle_deg,
        )
        speed_mps = track.last_velocity.ground_speed_kt * 0.514444
        velocity_error_mps = (
            None if state.ground_speed_mps is None else abs(speed_mps - state.ground_speed_mps)
        )
        vertical_rate_mps = (
            0.0
            if track.last_velocity.vertical_rate_fpm is None
            else track.last_velocity.vertical_rate_fpm * 0.00508
        )
        vertical_error_mps = (
            None
            if state.vertical_rate_mps is None
            else abs(vertical_rate_mps - state.vertical_rate_mps)
        )
        validation = PredictionValidation(
            position_error_m=position_error_m,
            position_error_threshold_m=position_threshold_m,
            velocity_error_mps=velocity_error_mps,
            velocity_error_threshold_mps=self._config.velocity_error_absolute_mps,
            heading_change_deg=heading_change_deg,
            heading_change_threshold_deg=self._config.maneuver_heading_change_deg,
        )
        maneuver = (
            (heading_change_deg is not None and heading_change_deg >= self._config.maneuver_heading_change_deg)
            or (
                velocity_error_mps is not None
                and velocity_error_mps >= self._config.maneuver_speed_change_mps
            )
            or (
                vertical_error_mps is not None
                and vertical_error_mps >= self._config.maneuver_vertical_rate_change_mps
            )
        )
        if maneuver:
            decision = TriggerDecision(True, PredictionUpdateReason.TRACK_MANEUVER, validation)
        elif position_error_m > position_threshold_m or (
            velocity_error_mps is not None
            and velocity_error_mps > self._config.velocity_error_absolute_mps
        ):
            decision = TriggerDecision(True, PredictionUpdateReason.PREDICTION_ERROR, validation)
        elif _seconds_between(now, previous.created_utc) >= self._config.maximum_prediction_age_s:
            decision = TriggerDecision(True, PredictionUpdateReason.PERIODIC_REFRESH, validation)
        else:
            decision = TriggerDecision(False, None, validation)
        if decision.regenerate and self._is_debounced(track, now, decision.reason):
            return TriggerDecision(False, None, validation)
        self._remember(track, now, decision)
        return decision

    def _is_debounced(
        self, track: TrackState, now: datetime, reason: PredictionUpdateReason | None
    ) -> bool:
        if reason is PredictionUpdateReason.INITIAL_TRACK:
            return False
        previous = self._last_regeneration_utc.get(track_id(track))
        return (
            previous is not None
            and _seconds_between(now, previous) < self._config.regeneration_debounce_s
        )

    def _remember(self, track: TrackState, now: datetime, decision: TriggerDecision) -> None:
        if decision.regenerate:
            self._last_regeneration_utc[track_id(track)] = _naive_utc(now)


def observer_id(observer: ObserverConfig) -> str:
    """Produce a deterministic identifier from a configured observer name."""

    normalized = re.sub(r"[^a-z0-9]+", "_", observer.name.lower()).strip("_")
    return normalized or "observer"


def track_id(track: TrackState) -> str:
    return f"adsb:{track.icao}"


def build_track_prediction(
    *,
    track: TrackState,
    reference_origin: ObserverConfig,
    observers: tuple[ObserverConfig, ...] | list[ObserverConfig],
    emitters: tuple[DtvEmitter, ...] | list[DtvEmitter],
    config: PredictionConfig,
    token: PredictionToken,
    created_utc: datetime,
    update_reason: PredictionUpdateReason,
    validation: PredictionValidation = PredictionValidation(),
) -> TrackPrediction:
    """Build a deterministic constant-velocity prediction without UI dependencies."""

    created_utc = _naive_utc(created_utc)
    prediction_id = f"{track_id(track)}:r{token.revision}"
    invalid_until = created_utc + timedelta(seconds=config.prediction_horizon_s)
    if not _is_eligible(track, created_utc, config):
        return TrackPrediction(
            track_id=track_id(track),
            icao=track.icao,
            callsign=track.callsign,
            status=PredictionTrackStatus.INVALID,
            state=None,
            prediction_id=prediction_id,
            revision=token.revision,
            created_utc=created_utc,
            valid_until_utc=invalid_until,
            horizon_s=config.prediction_horizon_s,
            sample_interval_s=config.prediction_sample_interval_s,
            maturity=PredictionMaturity.INVALID,
            update_reason=update_reason,
            validation=validation,
            opportunities=(),
            generation=token.generation,
        )

    assert track.last_position is not None
    assert track.last_velocity is not None
    epoch = _naive_utc(track.last_position.reported_at)
    position_enu = position_to_enu(track.last_position, reference_origin)
    velocity_enu = velocity_to_observer_enu(
        track.last_position, track.last_velocity, reference_origin
    )
    vertical_rate_assumed = track.last_velocity.vertical_rate_fpm is None
    state = PredictedTrackState(
        epoch_utc=epoch,
        reference_origin_id=observer_id(reference_origin),
        position_enu_m=position_enu,
        velocity_enu_mps=velocity_enu,
        latitude_deg=track.last_position.latitude_deg,
        longitude_deg=track.last_position.longitude_deg,
        altitude_m=track.last_position.altitude_m,
        ground_speed_mps=track.last_velocity.ground_speed_kt * 0.514444,
        track_angle_deg=track.last_velocity.track_deg,
        vertical_rate_mps=(
            None
            if vertical_rate_assumed
            else track.last_velocity.vertical_rate_fpm * 0.00508
        ),
        vertical_rate_assumed=vertical_rate_assumed,
    )
    samples_times = _sample_offsets(config.prediction_horizon_s, config.prediction_sample_interval_s)
    opportunities = tuple(
        _predict_opportunity(
            state=state,
            velocity=track.last_velocity,
            observer=observer,
            emitter=emitter,
            config=config,
            sample_offsets_s=samples_times,
            reference_origin=reference_origin,
        )
        for observer in observers
        for emitter in emitters
        if observer_id(observer) not in config.disabled_observer_ids
        and emitter.emitter_id not in config.disabled_emitter_ids
    )
    return TrackPrediction(
        track_id=track_id(track),
        icao=track.icao,
        callsign=track.callsign,
        status=PredictionTrackStatus.ACTIVE,
        state=state,
        prediction_id=prediction_id,
        revision=token.revision,
        created_utc=created_utc,
        valid_until_utc=epoch + timedelta(seconds=config.prediction_horizon_s),
        horizon_s=config.prediction_horizon_s,
        sample_interval_s=config.prediction_sample_interval_s,
        maturity=_maturity(track, created_utc, config),
        update_reason=update_reason,
        validation=validation,
        opportunities=opportunities,
        generation=token.generation,
    )


def _predict_opportunity(
    *,
    state: PredictedTrackState,
    velocity: VelocityReport,
    observer: ObserverConfig,
    emitter: DtvEmitter,
    config: PredictionConfig,
    sample_offsets_s: tuple[float, ...],
    reference_origin: ObserverConfig,
) -> ObservationOpportunity:
    receiver_id = observer_id(observer)
    compatible_ids = config.receiver_compatible_emitter_ids.get(receiver_id)
    observer_can_receive = compatible_ids is None or emitter.emitter_id in compatible_ids
    samples: list[ObservationSample] = []
    for offset_s in sample_offsets_s:
        sample_time = state.epoch_utc + timedelta(seconds=offset_s)
        position = _propagated_position(state, reference_origin, sample_time)
        propagated_velocity = VelocityReport(
            ground_speed_kt=velocity.ground_speed_kt,
            track_deg=velocity.track_deg,
            vertical_rate_fpm=velocity.vertical_rate_fpm,
            reported_at=sample_time,
        )
        measurement = bistatic_measurement(
            emitter=emitter,
            receiver=observer,
            position=position,
            velocity=propagated_velocity,
            rcs_dbsm=config.assumed_rcs_dbsm,
            polarization_loss_db=config.polarization_loss_db,
            system_loss_db=config.system_loss_db,
        )
        if measurement is None or measurement.bistatic_range_m is None:
            continue
        range_az_el = position_to_range_az_el(position, observer)
        geometrically_visible = is_observable_by(position, range_az_el, observer)
        within_range_limits = (
            config.maximum_bistatic_range_m is None
            or measurement.bistatic_range_m <= config.maximum_bistatic_range_m
        )
        doppler_hz = measurement.bistatic_doppler_hz
        within_doppler_limits = (
            doppler_hz is not None
            and (
                config.maximum_abs_bistatic_doppler_hz is None
                or abs(doppler_hz) <= config.maximum_abs_bistatic_doppler_hz
            )
        )
        above_snr_threshold = measurement.snr_db >= config.detection_threshold_db
        usable = (
            geometrically_visible
            and observer_can_receive
            and within_range_limits
            and within_doppler_limits
            and above_snr_threshold
        )
        samples.append(
            ObservationSample(
                time_offset_s=offset_s,
                sample_utc=sample_time,
                bistatic_range_m=measurement.bistatic_range_m,
                bistatic_range_rate_mps=measurement.bistatic_range_rate_mps or 0.0,
                bistatic_doppler_hz=doppler_hz or 0.0,
                predicted_bistatic_snr_db=measurement.snr_db,
                geometrically_visible=geometrically_visible,
                rf_available=observer_can_receive,
                within_range_limits=within_range_limits,
                within_doppler_limits=within_doppler_limits,
                above_snr_threshold=above_snr_threshold,
                usable=usable,
            )
        )
    windows = _extract_windows(tuple(samples), config)
    return ObservationOpportunity(
        observer_id=receiver_id,
        observer_name=observer.name,
        emitter_id=emitter.emitter_id,
        transmitter_site_id=emitter.transmitter_site_id,
        emitter=emitter,
        observer_can_receive=observer_can_receive,
        emitter_enabled=emitter.emitter_id not in config.disabled_emitter_ids,
        assumed_rcs_dbsm=config.assumed_rcs_dbsm,
        detection_threshold_db=config.detection_threshold_db,
        current=samples[0] if samples else None,
        samples=tuple(samples),
        windows=windows,
    )


def _extract_windows(
    samples: tuple[ObservationSample, ...], config: PredictionConfig
) -> tuple[ObservationWindow, ...]:
    windows: list[ObservationWindow] = []
    active: list[ObservationSample] = []
    entry_reason = "prediction_start_inside_usable"
    for sample in samples:
        if sample.usable:
            if not active:
                entry_reason = _transition_reason(None, sample, entering=True)
                if windows or samples[0] is not sample:
                    previous = samples[samples.index(sample) - 1]
                    active.append(_refine_boundary(previous, sample, config, entering=True))
            active.append(sample)
            continue
        if active:
            exit_reason = _transition_reason(active[-1], sample, entering=False)
            active.append(_refine_boundary(active[-1], sample, config, entering=False))
            windows.append(_window_from_samples(tuple(active), entry_reason, exit_reason))
            active = []
    if active:
        windows.append(_window_from_samples(tuple(active), entry_reason, "prediction_horizon"))
    return tuple(windows)


def _window_from_samples(
    samples: tuple[ObservationSample, ...], entry_reason: str, exit_reason: str
) -> ObservationWindow:
    snr_linear_mean = sum(10.0 ** (sample.predicted_bistatic_snr_db / 10.0) for sample in samples) / len(samples)
    peak = max(samples, key=lambda item: item.predicted_bistatic_snr_db)
    doppler_rates = [
        abs(
            (right.bistatic_doppler_hz - left.bistatic_doppler_hz)
            / max(right.time_offset_s - left.time_offset_s, 1e-12)
        )
        for left, right in zip(samples, samples[1:], strict=False)
    ]
    return ObservationWindow(
        start_utc=samples[0].sample_utc,
        end_utc=samples[-1].sample_utc,
        entry_reason=entry_reason,
        exit_reason=exit_reason,
        min_bistatic_range_m=min(item.bistatic_range_m for item in samples),
        max_bistatic_range_m=max(item.bistatic_range_m for item in samples),
        min_bistatic_range_rate_mps=min(item.bistatic_range_rate_mps for item in samples),
        max_bistatic_range_rate_mps=max(item.bistatic_range_rate_mps for item in samples),
        min_bistatic_doppler_hz=min(item.bistatic_doppler_hz for item in samples),
        max_bistatic_doppler_hz=max(item.bistatic_doppler_hz for item in samples),
        maximum_abs_doppler_rate_hzps=max(doppler_rates) if doppler_rates else None,
        min_snr_db=min(item.predicted_bistatic_snr_db for item in samples),
        mean_snr_db=10.0 * log10(snr_linear_mean),
        max_snr_db=peak.predicted_bistatic_snr_db,
        peak_snr_utc=peak.sample_utc,
    )


def _transition_reason(
    previous: ObservationSample | None, current: ObservationSample, *, entering: bool
) -> str:
    if previous is None:
        return "prediction_start_inside_usable"
    checks = (
        ("geometric_visibility", previous.geometrically_visible, current.geometrically_visible),
        ("rf_availability", previous.rf_available, current.rf_available),
        ("bistatic_range_limit", previous.within_range_limits, current.within_range_limits),
        ("doppler_limit", previous.within_doppler_limits, current.within_doppler_limits),
        ("snr_threshold", previous.above_snr_threshold, current.above_snr_threshold),
    )
    expected = True if entering else False
    for name, before, after in checks:
        if after == expected and before != after:
            return f"{'entry' if entering else 'exit'}_{name}"
    return "state_transition"


def _refine_boundary(
    before: ObservationSample,
    after: ObservationSample,
    config: PredictionConfig,
    *,
    entering: bool,
) -> ObservationSample:
    """Linearly refine supported threshold crossings inside a sample interval.

    Geometry and RF availability are discrete configuration gates; their
    boundary remains at the sample where the state changes. Numeric threshold
    crossings are refined deterministically to the nearest second.
    """

    crossing_fraction = _continuous_crossing_fraction(before, after, config)
    if crossing_fraction is None:
        return after if entering else before
    crossing_fraction = round(crossing_fraction * (after.time_offset_s - before.time_offset_s))
    duration_s = after.time_offset_s - before.time_offset_s
    if duration_s <= 0.0:
        return after if entering else before
    fraction = min(max(crossing_fraction / duration_s, 0.0), 1.0)
    return _interpolate_sample(before, after, fraction, usable=True)


def _continuous_crossing_fraction(
    before: ObservationSample, after: ObservationSample, config: PredictionConfig
) -> float | None:
    candidates: list[float] = []
    if before.above_snr_threshold != after.above_snr_threshold:
        candidates.append(
            _threshold_fraction(
                before.predicted_bistatic_snr_db, after.predicted_bistatic_snr_db, config.detection_threshold_db
            )
        )
    if (
        config.maximum_bistatic_range_m is not None
        and before.within_range_limits != after.within_range_limits
    ):
        candidates.append(
            _threshold_fraction(
                before.bistatic_range_m, after.bistatic_range_m, config.maximum_bistatic_range_m
            )
        )
    if (
        config.maximum_abs_bistatic_doppler_hz is not None
        and before.within_doppler_limits != after.within_doppler_limits
    ):
        candidates.append(
            _threshold_fraction(
                abs(before.bistatic_doppler_hz),
                abs(after.bistatic_doppler_hz),
                config.maximum_abs_bistatic_doppler_hz,
            )
        )
    return min((item for item in candidates if item is not None), default=None)


def _threshold_fraction(start: float, end: float, threshold: float) -> float | None:
    denominator = end - start
    if denominator == 0.0:
        return None
    return min(max((threshold - start) / denominator, 0.0), 1.0)


def _interpolate_sample(
    before: ObservationSample, after: ObservationSample, fraction: float, *, usable: bool
) -> ObservationSample:
    def interpolate(left: float, right: float) -> float:
        return left + fraction * (right - left)

    offset_s = interpolate(before.time_offset_s, after.time_offset_s)
    return ObservationSample(
        time_offset_s=offset_s,
        sample_utc=before.sample_utc + (after.sample_utc - before.sample_utc) * fraction,
        bistatic_range_m=interpolate(before.bistatic_range_m, after.bistatic_range_m),
        bistatic_range_rate_mps=interpolate(
            before.bistatic_range_rate_mps, after.bistatic_range_rate_mps
        ),
        bistatic_doppler_hz=interpolate(before.bistatic_doppler_hz, after.bistatic_doppler_hz),
        predicted_bistatic_snr_db=interpolate(
            before.predicted_bistatic_snr_db, after.predicted_bistatic_snr_db
        ),
        geometrically_visible=usable,
        rf_available=usable,
        within_range_limits=usable,
        within_doppler_limits=usable,
        above_snr_threshold=usable,
        usable=usable,
    )


def _propagated_position(
    state: PredictedTrackState, reference_origin: ObserverConfig, sample_time: datetime
) -> PositionReport:
    delta_s = _seconds_between(sample_time, state.epoch_utc)
    point = EnuPoint(
        east_m=state.position_enu_m.east_m + state.velocity_enu_mps.east_mps * delta_s,
        north_m=state.position_enu_m.north_m + state.velocity_enu_mps.north_mps * delta_s,
        up_m=state.position_enu_m.up_m + state.velocity_enu_mps.up_mps * delta_s,
    )
    return enu_to_position(point, reference_origin, sample_time)


def _is_eligible(track: TrackState, now: datetime, config: PredictionConfig) -> bool:
    if track.last_position is None or track.last_velocity is None:
        return False
    age_s = _seconds_between(now, track.last_seen)
    history_s = _seconds_between(track.last_seen, track.first_seen)
    return (
        age_s <= config.maximum_adsb_report_age_s
        and history_s >= config.minimum_track_history_s
        and config.prediction_horizon_s > 0.0
        and config.prediction_sample_interval_s > 0.0
    )


def _maturity(
    track: TrackState, now: datetime, config: PredictionConfig
) -> PredictionMaturity:
    history_s = _seconds_between(now, track.first_seen)
    if history_s < config.minimum_track_history_s:
        return PredictionMaturity.INITIAL
    if history_s < config.stable_track_history_s:
        return PredictionMaturity.STABILIZING
    return PredictionMaturity.STABLE


def _sample_offsets(horizon_s: float, interval_s: float) -> tuple[float, ...]:
    steps = max(int(horizon_s // interval_s), 0)
    offsets = tuple(index * interval_s for index in range(steps + 1))
    if not offsets or offsets[-1] < horizon_s:
        return (*offsets, horizon_s)
    return offsets


def _propagated_state_position(state: PredictedTrackState, epoch: datetime) -> PositionReport:
    """Propagate an ENU state at its fixed origin to an equivalent local LLA report."""

    elapsed_s = _seconds_between(epoch, state.epoch_utc)
    latitude_deg = state.latitude_deg + (
        state.velocity_enu_mps.north_mps * elapsed_s / 111_132.0
    )
    longitude_scale_m = 111_320.0 * cos(radians(state.latitude_deg))
    longitude_deg = state.longitude_deg + (
        0.0
        if abs(longitude_scale_m) < 1e-9
        else state.velocity_enu_mps.east_mps * elapsed_s / longitude_scale_m
    )
    return PositionReport(
        latitude_deg=latitude_deg,
        longitude_deg=longitude_deg,
        altitude_ft=(
            state.altitude_m + state.velocity_enu_mps.up_mps * elapsed_s
        )
        / 0.3048,
        reported_at=epoch,
    )


def _distance_between_reports(position: PositionReport, expected: PositionReport) -> float:
    lat_scale_m = 111_132.0
    lon_scale_m = 111_320.0
    north_m = (position.latitude_deg - expected.latitude_deg) * lat_scale_m
    east_m = (
        (position.longitude_deg - expected.longitude_deg)
        * lon_scale_m
        * cos(radians(expected.latitude_deg))
    )
    up_m = position.altitude_m - expected.altitude_m
    return (east_m * east_m + north_m * north_m + up_m * up_m) ** 0.5


def _heading_difference(first_deg: float | None, second_deg: float | None) -> float | None:
    if first_deg is None or second_deg is None:
        return None
    return abs((first_deg - second_deg + 180.0) % 360.0 - 180.0)


def _enu_norm(point: EnuPoint) -> float:
    return (point.east_m * point.east_m + point.north_m * point.north_m + point.up_m * point.up_m) ** 0.5


def _seconds_between(later: datetime, earlier: datetime) -> float:
    return (_naive_utc(later) - _naive_utc(earlier)).total_seconds()


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)
