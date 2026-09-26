# Engineering Specification: ADS-B Cued Passive-Radar Observation Opportunities

> **Interface contract superseded (2026-09-26):** the messages, schemas, transport and rates are now governed by the ICD, [flightTest `docs/system/ICD_Messages.md`](https://github.com/pwilliamMAT/flightTest/blob/main/docs/system/ICD_Messages.md) (master copy on `main`). This spec remains the implementation reference for the prediction design (FR-001–FR-014). Where the two differ, the ICD governs.

**Application:** `ADSBConsoleApp`  
**Downstream activity:** `passiveTrackerPlanner`  
**Interface:** UDP/IP carrying UTF-8 JSON  
**Document status:** Implementation specification  
**Schema version:** `1.0.0`  

---

## 1. Purpose

Enhance the existing `ADSBConsoleApp` so that it acts as the track-cueing source for passive-radar resource planning. The application shall continue to receive, organize, and display ADS-B tracks while additionally maintaining future passive-radar observation predictions for every valid combination of:

- active ADS-B track,
- observer site,
- enabled DTV emitter.

For each combination, the console shall calculate:

1. predicted bistatic range history,
2. predicted bistatic range-rate history,
3. predicted bistatic Doppler history derived from bistatic range rate,
4. predicted bistatic SNR history,
5. geometric, RF-available, detectable, and usable observation intervals.

The application shall emit versioned cue messages to `passiveTrackerPlanner` as UTF-8 JSON over UDP/IP. The planner, rather than the console, remains responsible for final resource allocation and RF task scheduling.

---

## 2. System Context

```text
[ADSB Receiver] --> [ADSB Console] --> [passiveTrackerPlanner]
                                              |
                                              v
                                  [passiveTrackerManager]
                                              |
                                              v
                              [passiveTrackerDataReduction]
                                              |
                                              v
                                  [passiveTracking_EKF]
                                              |
                                              v
                                 [passiveTrackerDisplay]
                                              ^
                                              |
             +----------------Track Update Requests----------------+
```

### 2.1 Component responsibilities

#### ADSB Receiver

- Runs `dump1090` or an equivalent source.
- Provides SBS/BaseStation reports over the existing input connection.

#### ADSB Console

- Receives and organizes SBS reports.
- Maintains active ADS-B tracks.
- Maintains observer, transmitter-site, and emitter configuration.
- Calculates observer CPA and current passive-radar quantities.
- Predicts future passive-radar observation opportunities.
- Publishes cues to `passiveTrackerPlanner`.

#### passiveTrackerPlanner

- Consumes cue messages.
- Chooses which track, observer, emitter, frequency, start time, and dwell to schedule.
- Resolves conflicts among tracks and RF resources.
- Plans calibration, cued tracking, and future surveillance tasks.

#### passiveTrackerManager

- Executes the planned receiver tasking.
- Tunes and controls RF hardware.
- Collects RF data and transfers data to processing resources.

#### passiveTrackerDataReduction

- Processes collected RF data.
- Produces detections and reduced plotting or tracking products.

#### passiveTracking_EKF

- Maintains passive-only state estimates and covariance.
- May later request additional observations through `passiveTrackerPlanner`.

#### passiveTrackerDisplay

- Displays passive tracks.
- May overlay passive results with the original ADS-B tracks for validation.

---

## 3. Scope

### 3.1 Included

This feature includes:

- per-track ENU state prediction,
- predictions for every compatible track-observer-emitter combination,
- bistatic range and range-rate calculation,
- derived bistatic Doppler calculation,
- time-varying BiSNR prediction using the existing link-budget model,
- field-of-view and usability evaluation,
- observation-window extraction and boundary refinement,
- prediction revision and validity management,
- maneuver and prediction-error detection,
- UDP JSON cue publication,
- cue withdrawal and heartbeat messages,
- periodic full-state recovery snapshots,
- Observer Focus and Track Focus integration,
- unit, integration, deterministic replay, and endurance testing.

### 3.2 Excluded

This feature does not include:

- final global RF scheduling,
- scheduling conflict resolution,
- direct RF receiver control,
- passive range-Doppler processing,
- passive target detection,
- passive EKF implementation,
- estimation of track state from passive returns,
- independent surveillance scheduling,
- replacement of ADS-B by passive track initiation,
- treating multiple frequencies at one tower as independent geometric sites.

---

## 4. Architectural Principles

### 4.1 ADSBConsoleApp is a cue generator, not a scheduler

The console shall describe predicted opportunities. The planner shall decide which opportunities are used.

### 4.2 Emitter and transmitter site are distinct entities

An emitter represents a waveform or RF channel. A transmitter site represents physical geometry. Multiple emitters may share one transmitter site.

Every planner-facing opportunity shall contain both:

```text
emitter_id
transmitter_site_id
```

### 4.3 Bistatic range is the fundamental predicted observable

For target position \(\mathbf{p}(t)\), transmitter position \(\mathbf{p}_{TX}\), and receiver position \(\mathbf{p}_{RX}\):

\[
\rho_b(t)=
\|\mathbf{p}(t)-\mathbf{p}_{TX}\|+
\|\mathbf{p}(t)-\mathbf{p}_{RX}\|-
\|\mathbf{p}_{TX}-\mathbf{p}_{RX}\|
\]

The output is excess bistatic path length in meters.

### 4.4 Doppler is derived but still emitted

Range rate shall be calculated analytically:

\[
\dot{\rho}_b(t)=
\mathbf{u}_{TX}(t)^T\mathbf{v}(t)+
\mathbf{u}_{RX}(t)^T\mathbf{v}(t)
\]

where:

\[
\mathbf{u}_{TX}(t)=
\frac{\mathbf{p}(t)-\mathbf{p}_{TX}}
{\|\mathbf{p}(t)-\mathbf{p}_{TX}\|}
\]

and:

\[
\mathbf{u}_{RX}(t)=
\frac{\mathbf{p}(t)-\mathbf{p}_{RX}}
{\|\mathbf{p}(t)-\mathbf{p}_{RX}\|}.
\]

Doppler shall be calculated as:

\[
f_D(t)=s_D\frac{\dot{\rho}_b(t)}{\lambda},
\qquad \lambda=\frac{c}{f_c}
\]

where \(s_D\) is the configured sign convention. Doppler shall not be independently fitted or propagated, but shall be emitted because downstream planning and processing need an explicit Doppler gate.

The initial implementation shall preserve the existing console convention:

\[
f_D(t)=-\frac{\dot{\rho}_b(t)}{\lambda}
\]

Under this convention, positive bistatic Doppler means a decreasing (closing) bistatic path and negative bistatic Doppler means an increasing (opening) bistatic path. The wire value of `doppler_sign_convention` shall be `positive_for_decreasing_bistatic_path`. A future sign-convention change requires a new schema version.

### 4.5 Internal and wire representations are separate

The prediction engine shall produce typed internal domain objects. Versioned data-transfer objects shall define the external JSON contract. Terminal-layout classes shall not be used as planner-facing DTOs.

### 4.6 Summary payloads are the operational default

The console shall calculate sampled predictions internally. Operational UDP messages shall default to compact summary output containing the track state and observation windows. Sampled histories shall be available for diagnostics and validation.

### 4.7 Engineering-review clarifications

The following clarifications are normative and take precedence over earlier text in this
document when a conflict exists.

#### 4.7.1 Time, clocks, and determinism

The system shall use separate injected time sources:

- `EventClock` supplies the SBS/event epoch used for track age, state validity,
  propagation, prediction maturity, and replay determinism.
- `PublishClock` supplies timezone-aware UTC timestamps used in externally visible
  JSON messages.
- A monotonic scheduler clock supplies heartbeat, snapshot, debounce, and
  periodic-refresh scheduling intervals.

Internal legacy SBS datetimes may remain naive UTC for compatibility, but all wire
timestamps shall be timezone-aware ISO 8601 UTC values ending in `Z`. Playback mode
shall explicitly control whether source event timestamps are preserved or rebased.

#### 4.7.2 Prediction reference frame

Each prediction shall use one stable, configured ENU reference origin for the complete
prediction lifetime. The origin shall be identified by `reference_origin_id` and shall
not vary by observer. The default origin is the configured local observer. The engine
shall preserve WGS-84 LLA state and may use ECEF internally when evaluating individual
observer and emitter geometries. Observer-local ENU is permitted only as a derived
calculation frame, not as the identity of the track prediction state.

#### 4.7.3 Stable identities and configuration compatibility

`track_id` shall be the stable `adsb:<ICAO>` value for the lifetime of an ICAO track.
`transmitter_site_id` shall be derived from ASRN when available, otherwise from a
deterministic normalized site-name and geodetic-location key. `emitter_id` shall be
stable and distinct for each facility/channel/frequency emitter; a callsign alone is
not sufficient. Observer enablement, emitter enablement, and observer/emitter receive
compatibility shall be explicit configuration data. Existing DTV rows remain the source
of RF and location data.

#### 4.7.4 Revision ownership and asynchronous supersession

Prediction revisions are monotonically increasing per `track_id` within a source
instance. Every queued computation shall carry the track generation and intended
revision. A result may be committed or published only if its generation and revision
still equal the current state. Track purge shall invalidate outstanding generations
before removal and shall retain enough prior state to publish exactly one withdrawal
for the latest published revision. Sequence-number allocation shall be serialized by
the publisher.

#### 4.7.5 Visibility and window semantics

The first implementation shall distinguish geometry, RF compatibility, range limits,
Doppler limits, SNR threshold, and usability. Terrain masks and antenna patterns are
optional future constraints and shall not be represented as implemented when absent.
Window `mean_snr_db` shall be calculated by averaging linear SNR power over samples and
then converting the result to dB. Boundary refinement applies to continuous threshold
crossings; simultaneous crossings shall use a documented, deterministic reason-priority
order. A one-second boundary target applies only to supported continuous gates.

#### 4.7.6 UDP operational profile and recovery

The default maximum operational UDP payload shall be 1200 bytes to avoid ordinary
path-MTU fragmentation. Larger payload limits require explicit deployment configuration.
Summary payloads shall be preferred; oversize handling shall first omit sampled history,
then use a versioned chunk message when enabled, otherwise reject and count the message.

Snapshot begin, end, and chunk message types shall have versioned schemas. Every cue
published as part of a snapshot shall include the matching `snapshot_id`. A planner
shall reconcile its state only after receiving a valid matching snapshot end message.

#### 4.7.7 Versioned contracts and validation

All externally visible quality, validation, diagnostic, chunk, and snapshot objects
shall have explicit versioned schema definitions. Operational payloads shall not rely on
unbounded `additionalProperties`. JSON Schema Draft 2020-12 validation is a required
test and planner-emulator dependency.

#### 4.7.8 UI compatibility and BiSNR baseline

Prediction processing shall not block SBS ingestion or terminal rendering. Observer and
Track Focus shall use the latest committed prediction when it matches the active track
generation; otherwise they shall retain the existing direct instantaneous computation as
a non-blocking fallback. The current `bistatic_measurement` implementation at the
implementation baseline is the BiSNR regression oracle. Selected corpus epochs shall be
captured before extraction, and future values shall match within 0.1 dB unless an
approved model change is documented.

#### 4.7.9 Replay corpus policy

New replay manifests and logical partitions shall reference an externally configurable
source-corpus directory, `ADSB_REPLAY_CORPUS_DIR`, whose developer default is the
sibling `../000_sbs_for_ELAD_cleanup/sbs_clean` directory. The manifest shall record
relative source paths and hashes. Existing fixture files remain untouched; no new copy
of the 11-file source corpus shall be added for this feature.

#### 4.7.10 Automatic and manual cueing

The console shall expose two cue-publication modes:

- **Automatic:** publish each newly committed, eligible prediction that passes the
  configured gate tests. Periodic recovery snapshots remain enabled in this mode.
- **Manual:** continue computing and displaying predictions, but publish a track cue
  only when an operator selects that track in Observer Focus or Track Focus and presses
  uppercase `Q`. Manual mode shall not publish automatic cue revisions or automatic
  track snapshots for unselected tracks.

The UI shall display the active cueing mode. A mode change affects publication only; it
shall not discard existing track state or predictions. A withdrawal is required only for
the latest revision that was actually published for the relevant track.

---

## 5. Existing Application Behavior

### 5.1 Observer Focus

For the selected observer, the application currently displays active tracks and the three emitters with the highest instantaneous predicted bistatic SNR. Existing instantaneous values such as range, azimuth, elevation, range rate, Doppler, BiSNR, ground speed, and track age shall remain available.

### 5.2 Track Focus

For a selected track, the application currently displays track information, observer CPA information, and ranked emitter/link-budget details. The demonstration supports two displayed observers, although only one observer currently has implemented RF collection capability. The new prediction engine shall support multiple configured observers independently of current RF implementation status.

---

## 6. Functional Requirements

## FR-001: Maintain prediction state per track

For each active ADS-B track, maintain:

- internal track ID,
- ICAO address,
- callsign when available,
- latest ADS-B state epoch,
- latitude, longitude, and altitude,
- local ENU position,
- local ENU velocity,
- ground speed,
- track angle,
- vertical rate,
- state quality indicators,
- prediction creation time,
- prediction horizon,
- prediction revision,
- maturity state,
- update reason,
- validity expiration.

The nominal state vector is:

\[
\mathbf{x}=
[E,N,U,v_E,v_N,v_U]^T.
\]

The initial motion model is constant velocity:

\[
\mathbf{p}(t)=\mathbf{p}_0+\mathbf{v}_0(t-t_0).
\]

A track lacking enough information to form a prediction shall remain visible but shall not emit a valid cue.

## FR-002: Generate predictions on initial track eligibility

A prediction shall be generated when a track first has:

- valid position,
- valid altitude,
- valid or estimated horizontal velocity,
- acceptable report age,
- enough history to satisfy the configured initial eligibility rule.

If vertical velocity is unavailable, zero may be assumed. The assumption shall be represented in state quality or assumptions metadata.

The first emitted cue shall use:

```json
"update_reason": "initial_track"
```

## FR-003: Predict over a configurable horizon

Defaults:

```text
prediction_horizon_s = 600
prediction_sample_interval_s = 10
minimum_prediction_horizon_s = 300
```

The prediction sample interval shall be independent of UI refresh rate. The implementation may sample more densely near:

- observer CPA,
- transmitter CPA,
- FOV boundaries,
- SNR threshold crossings,
- range or Doppler processing limits,
- rapid Doppler-rate intervals.

## FR-004: Calculate every valid Track × Observer × Emitter combination

For each eligible track, compute predictions for every compatible enabled combination.

A combination is compatible only if:

- observer is enabled,
- emitter is enabled,
- observer configuration indicates receive support for the emitter,
- required geometry and RF configuration are valid.

Unavailable combinations may be omitted from operational cues or included with a machine-readable rejection reason in diagnostic mode.

## FR-005: Predict bistatic range history

For each valid combination, compute bistatic range at each internal prediction sample.

Required summary values:

- current bistatic range,
- minimum predicted bistatic range,
- maximum predicted bistatic range,
- range at each window boundary,
- time of minimum bistatic range when applicable.

Units shall be meters.

## FR-006: Predict bistatic range-rate history

Calculate the analytic derivative of bistatic range from propagated velocity and geometry.

Required outputs:

- current range rate,
- minimum and maximum range rate over each window.

Units shall be meters per second.

Numerical differencing may be used only as a cross-check or documented fallback.

## FR-007: Predict bistatic Doppler history

Calculate Doppler from range rate and carrier frequency.

Required outputs:

- current Doppler,
- minimum and maximum Doppler over each window,
- maximum absolute Doppler rate when available,
- explicit sign convention.

Units shall be hertz and hertz per second.

Range and range rate shall remain frequency-independent. Doppler shall scale with carrier frequency.

## FR-008: Predict bistatic SNR history

Reuse the existing instantaneous BiSNR/link-budget implementation as the authoritative model unless a deliberate model revision is separately approved.

Evaluate the model over the prediction horizon.

Required summary values:

- current SNR,
- minimum SNR within each usable window,
- mean SNR within each usable window,
- maximum SNR within each usable window,
- time of maximum SNR,
- total time above threshold.

Make model assumptions available where known:

- emitter EIRP,
- transmitter gain or pattern assumption,
- receiver gain or pattern assumption,
- target RCS assumption,
- transmitter-target path loss,
- target-receiver path loss,
- spreading or bistatic loss,
- receiver noise,
- polarization loss,
- system loss,
- detection threshold.

## FR-009: Predict field-of-view and usable observation intervals

Evaluate field of view as a set of independent conditions:

- geometric visibility,
- observer range limits,
- observer elevation limits,
- terrain or horizon mask if available,
- transmitter illumination constraints,
- receiver antenna constraints,
- RF compatibility and receiver availability,
- minimum predicted SNR,
- processable bistatic-range limits,
- processable Doppler limits,
- prediction-horizon limits.

Expose separate flags for:

```text
geometrically_visible
rf_available
within_range_limits
within_doppler_limits
above_snr_threshold
usable
```

Nominal definition:

```text
usable = geometrically_visible
         AND rf_available
         AND within_range_limits
         AND within_doppler_limits
         AND above_snr_threshold
```

A combination may contain zero, one, or multiple disjoint usable windows.

Each window shall include:

- start UTC,
- end UTC,
- duration,
- entry reason,
- exit reason,
- range bounds,
- range-rate bounds,
- Doppler bounds,
- maximum absolute Doppler rate,
- SNR statistics.

Coarse sample transitions shall be refined by interpolation or bounded root finding. The target boundary accuracy is one second or better.

## FR-010: Detect track maneuvers

Compare the latest ADS-B state against the prior prediction and regenerate when configured maneuver thresholds are exceeded.

Initial configurable values:

```text
maneuver_heading_change_deg = 3.0
maneuver_speed_change_mps = 10.0
maneuver_vertical_rate_change_mps = 3.0
maneuver_position_error_m = 1000.0
maneuver_altitude_error_m = 150.0
```

A maneuver update shall increment the revision and set:

```json
"update_reason": "track_maneuver"
```

The implementation shall include hysteresis or debounce behavior sufficient to prevent ordinary ADS-B jitter from causing revision storms.

## FR-011: Detect prediction error

Use combined absolute and relative thresholds rather than a percentage-only test.

For position:

\[
e_p > \max(e_{p,abs}, e_{p,rel}R).
\]

Initial values:

```text
position_error_absolute_m = 1000
position_error_relative_fraction = 0.02
altitude_error_absolute_m = 150
velocity_error_absolute_mps = 10
velocity_error_relative_fraction = 0.05
bistatic_range_error_absolute_m = 500
bistatic_range_error_relative_fraction = 0.01
bistatic_doppler_error_absolute_hz = 10
bistatic_doppler_error_relative_fraction = 0.05
```

ADS-B state error is the primary invalidation criterion. Per-combination bistatic range and Doppler errors are diagnostic criteria.

A prediction-error update shall set:

```json
"update_reason": "prediction_error"
```

## FR-012: Periodically refresh predictions and track maturity

Regenerate a prediction when its age exceeds:

```text
maximum_prediction_age_s = 30
```

Supported maturity states:

```text
initial
stabilizing
stable
invalid
```

Recommended policy:

- `initial`: less than 10 seconds of usable history,
- `stabilizing`: 10 to 30 seconds of usable history,
- `stable`: at least 30 seconds of usable history without a detected maneuver,
- `invalid`: stale, incomplete, or inconsistent state.

A timed refresh shall set:

```json
"update_reason": "periodic_refresh"
```

## FR-013: Publish cue, withdrawal, heartbeat, and recovery messages

Supported message types:

- `track_cue`,
- `track_cue_withdrawal`,
- `cue_heartbeat`,
- `cue_snapshot_begin`,
- `cue_snapshot_end`.

Publish a withdrawal when:

- a track is purged,
- a track becomes stale or invalid,
- required state fields become unavailable,
- observer or emitter configuration disables an opportunity,
- an opportunity is invalidated,
- configuration changes invalidate a prior revision.

Publish periodic full snapshots so the planner can recover from UDP loss or restart.

## FR-014: Preserve and enhance existing UI behavior

Observer Focus shall continue to rank the top three instantaneous BiSNR emitters for the selected observer.

Track Focus shall continue to show observer CPA and emitter/link-budget details.

Both views shall use the new prediction engine as the common source of instantaneous and future values.

Recommended Observer Focus additions:

```text
PredAge
PredRev
CueStatus
NextWin
WinDur
```

Recommended Track Focus additions:

- prediction epoch,
- maturity,
- revision,
- update reason,
- valid-until time,
- number of upcoming windows,
- best next window,
- transmitter-site ID,
- range and Doppler bounds,
- peak SNR and time,
- window duration.

---

## 7. Data Model Definitions

### 7.1 TrackPrediction

```text
TrackPrediction
    trackId: string
    icao: string
    callsign: string|null
    status: TrackStatus
    state: TrackState
    predictionId: string
    revision: integer
    createdUtc: timestamp
    validUntilUtc: timestamp
    horizonSeconds: number
    sampleIntervalSeconds: number
    motionModel: string
    maturity: PredictionMaturity
    updateReason: PredictionUpdateReason
    validation: PredictionValidation
    opportunities: ObservationOpportunity[]
```

### 7.2 TrackState

```text
TrackState
    epochUtc: timestamp
    referenceFrame: "ENU"
    referenceOriginId: string
    positionEnuM: [number, number, number]
    velocityEnuMps: [number, number, number]
    latitudeDeg: number
    longitudeDeg: number
    altitudeMslM: number
    groundSpeedMps: number|null
    trackAngleDeg: number|null
    verticalRateMps: number|null
    quality: StateQuality
```

### 7.3 ObservationOpportunity

```text
ObservationOpportunity
    opportunityId: string
    observerId: string
    observerName: string
    emitterId: string
    transmitterSiteId: string
    carrierFrequencyHz: number
    rfChannel: string|number|null
    emitterEnabled: boolean
    observerCanReceive: boolean
    models: OpportunityModels
    current: ObservationSample|null
    summary: OpportunitySummary
    windows: ObservationWindow[]
    history: ObservationSample[]|null
```

### 7.4 ObservationWindow

```text
ObservationWindow
    windowId: string
    startUtc: timestamp
    endUtc: timestamp
    durationS: number
    entryReason: string
    exitReason: string
    minBistaticRangeM: number
    maxBistaticRangeM: number
    minBistaticRangeRateMps: number
    maxBistaticRangeRateMps: number
    minBistaticDopplerHz: number
    maxBistaticDopplerHz: number
    maximumAbsDopplerRateHzps: number|null
    minSnrDb: number
    meanSnrDb: number
    maxSnrDb: number
    peakSnrUtc: timestamp
```

### 7.5 ObservationSample

```text
ObservationSample
    timeOffsetS: number
    sampleUtc: timestamp|null
    bistaticRangeM: number
    bistaticRangeRateMps: number
    bistaticDopplerHz: number
    predictedBistaticSnrDb: number
    geometricallyVisible: boolean
    rfAvailable: boolean
    withinRangeLimits: boolean
    withinDopplerLimits: boolean
    aboveSnrThreshold: boolean
    usable: boolean
```

### 7.6 Enumerations

```text
PredictionMaturity:
    initial
    stabilizing
    stable
    invalid

PredictionUpdateReason:
    initial_track
    track_maneuver
    prediction_error
    periodic_refresh
    observer_configuration_change
    emitter_configuration_change
    application_snapshot

TrackStatus:
    active
    stale
    purged
    invalid
```

---

## 8. UDP Transport Requirements

### 8.1 Encoding

Each datagram shall contain one complete JSON object encoded as UTF-8 without a byte-order mark. Newline termination is optional but shall be consistent.

### 8.2 Reliability metadata

Because UDP does not guarantee delivery, order, or uniqueness, every message shall contain:

- schema version,
- message type,
- message ID,
- source instance ID,
- sequence number,
- generation timestamp,
- prediction revision where applicable.

The planner shall be able to reject duplicates, out-of-order messages, and stale revisions.

### 8.3 Message sizing

Normal operation shall use one track cue per logical message. Avoid IP fragmentation where practical.

Default maximum:

```text
maximum_datagram_bytes = 1200
```

If a message exceeds the configured maximum, the publisher shall use one of these configured policies:

1. omit sampled history and retry,
2. split into chunks,
3. reject and log the message.

Chunked messages shall include:

```text
logical_message_id
chunk_index
chunk_count
```

### 8.4 Payload modes

```text
summary
sampled
```

`summary` is the operational default. `sampled` is intended for development and validation.

---

## 9. Full JSON Schemas

The schemas below use JSON Schema Draft 2020-12.

### 9.1 Common definitions and track-cue schema

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://passive-radar.local/schemas/track-cue-1.0.0.json",
  "title": "Passive Radar Track Cue",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "schema_version", "message_type", "message_id", "source",
    "source_instance_id", "sequence_number", "generated_utc",
    "track", "prediction", "opportunities"
  ],
  "properties": {
    "schema_version": { "const": "1.0.0" },
    "message_type": { "const": "track_cue" },
    "message_id": { "type": "string", "format": "uuid" },
    "source": { "const": "ADSBConsoleApp" },
    "source_instance_id": { "type": "string", "minLength": 1 },
    "sequence_number": { "type": "integer", "minimum": 0 },
    "generated_utc": { "type": "string", "format": "date-time" },
    "snapshot_id": { "type": ["string", "null"], "minLength": 1 },
    "track": { "$ref": "#/$defs/track" },
    "prediction": { "$ref": "#/$defs/prediction" },
    "opportunities": {
      "type": "array",
      "items": { "$ref": "#/$defs/opportunity" }
    }
  },
  "$defs": {
    "vector3": {
      "type": "array", "minItems": 3, "maxItems": 3,
      "items": { "type": "number" }
    },
    "state_quality": {
      "type": ["object", "null"],
      "additionalProperties": false,
      "properties": {
        "vertical_rate_assumed": { "type": "boolean" },
        "horizontal_velocity_estimated": { "type": "boolean" },
        "position_valid": { "type": "boolean" },
        "velocity_valid": { "type": "boolean" }
      }
    },
    "prediction_validation": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "position_error_m": { "type": ["number", "null"], "minimum": 0 },
        "position_error_threshold_m": { "type": ["number", "null"], "minimum": 0 },
        "velocity_error_mps": { "type": ["number", "null"], "minimum": 0 },
        "velocity_error_threshold_mps": { "type": ["number", "null"], "minimum": 0 },
        "heading_change_deg": { "type": ["number", "null"], "minimum": 0 },
        "heading_change_threshold_deg": { "type": ["number", "null"], "minimum": 0 }
      }
    },
    "track": {
      "type": "object",
      "additionalProperties": false,
      "required": ["track_id", "icao", "status", "last_report_utc", "report_age_s", "state"],
      "properties": {
        "track_id": { "type": "string", "minLength": 1 },
        "icao": { "type": "string", "pattern": "^[0-9A-Fa-f]{6}$" },
        "callsign": { "type": ["string", "null"] },
        "status": { "enum": ["active", "stale", "purged", "invalid"] },
        "last_report_utc": { "type": "string", "format": "date-time" },
        "report_age_s": { "type": "number", "minimum": 0 },
        "state": { "$ref": "#/$defs/state" }
      }
    },
    "state": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "epoch_utc", "reference_frame", "reference_origin_id",
        "position_enu_m", "velocity_enu_mps", "latitude_deg",
        "longitude_deg", "altitude_m_msl"
      ],
      "properties": {
        "epoch_utc": { "type": "string", "format": "date-time" },
        "reference_frame": { "const": "ENU" },
        "reference_origin_id": { "type": "string", "minLength": 1 },
        "position_enu_m": { "$ref": "#/$defs/vector3" },
        "velocity_enu_mps": { "$ref": "#/$defs/vector3" },
        "latitude_deg": { "type": "number", "minimum": -90, "maximum": 90 },
        "longitude_deg": { "type": "number", "minimum": -180, "maximum": 180 },
        "altitude_m_msl": { "type": "number" },
        "ground_speed_mps": { "type": ["number", "null"], "minimum": 0 },
        "track_angle_deg": { "type": ["number", "null"], "minimum": 0, "exclusiveMaximum": 360 },
        "vertical_rate_mps": { "type": ["number", "null"] },
        "quality": { "$ref": "#/$defs/state_quality" }
      }
    },
    "prediction": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "prediction_id", "revision", "created_utc", "valid_until_utc",
        "horizon_s", "sample_interval_s", "motion_model", "maturity",
        "update_reason", "validation"
      ],
      "properties": {
        "prediction_id": { "type": "string", "minLength": 1 },
        "revision": { "type": "integer", "minimum": 1 },
        "created_utc": { "type": "string", "format": "date-time" },
        "valid_until_utc": { "type": "string", "format": "date-time" },
        "horizon_s": { "type": "number", "exclusiveMinimum": 0 },
        "sample_interval_s": { "type": "number", "exclusiveMinimum": 0 },
        "motion_model": { "type": "string", "minLength": 1 },
        "maturity": { "enum": ["initial", "stabilizing", "stable", "invalid"] },
        "update_reason": {
          "enum": [
            "initial_track", "track_maneuver", "prediction_error",
            "periodic_refresh", "observer_configuration_change",
            "emitter_configuration_change", "application_snapshot"
          ]
        },
        "validation": { "$ref": "#/$defs/prediction_validation" }
      }
    },
    "opportunity": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "opportunity_id", "observer_id", "observer_name", "emitter_id",
        "transmitter_site_id", "carrier_frequency_hz", "emitter_enabled",
        "observer_can_receive", "models", "summary", "windows"
      ],
      "properties": {
        "opportunity_id": { "type": "string", "minLength": 1 },
        "observer_id": { "type": "string", "minLength": 1 },
        "observer_name": { "type": "string", "minLength": 1 },
        "emitter_id": { "type": "string", "minLength": 1 },
        "transmitter_site_id": { "type": "string", "minLength": 1 },
        "carrier_frequency_hz": { "type": "number", "exclusiveMinimum": 0 },
        "rf_channel": { "type": ["string", "number", "null"] },
        "emitter_enabled": { "type": "boolean" },
        "observer_can_receive": { "type": "boolean" },
        "models": { "$ref": "#/$defs/models" },
        "current": { "oneOf": [{ "$ref": "#/$defs/sample" }, { "type": "null" }] },
        "summary": { "$ref": "#/$defs/summary" },
        "windows": { "type": "array", "items": { "$ref": "#/$defs/window" } },
        "history": {
          "oneOf": [
            { "type": "null" },
            { "type": "array", "items": { "$ref": "#/$defs/sample" } }
          ]
        }
      }
    },
    "models": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "bistatic_range_definition", "doppler_source",
        "doppler_sign_convention", "snr_model_id", "detection_threshold_db"
      ],
      "properties": {
        "bistatic_range_definition": { "const": "tx_target_plus_target_rx_minus_tx_rx" },
        "doppler_source": { "const": "analytic_derivative_of_bistatic_range" },
        "doppler_sign_convention": { "const": "positive_for_decreasing_bistatic_path" },
        "snr_model_id": { "type": "string", "minLength": 1 },
        "assumed_rcs_dbsm": { "type": ["number", "null"] },
        "detection_threshold_db": { "type": "number" }
      }
    },
    "summary": {
      "type": "object",
      "additionalProperties": false,
      "required": ["has_usable_window", "total_usable_duration_s"],
      "properties": {
        "has_usable_window": { "type": "boolean" },
        "next_window_start_utc": { "type": ["string", "null"], "format": "date-time" },
        "next_window_end_utc": { "type": ["string", "null"], "format": "date-time" },
        "total_usable_duration_s": { "type": "number", "minimum": 0 },
        "maximum_snr_db": { "type": ["number", "null"] },
        "maximum_snr_utc": { "type": ["string", "null"], "format": "date-time" },
        "minimum_bistatic_range_m": { "type": ["number", "null"] },
        "maximum_bistatic_range_m": { "type": ["number", "null"] },
        "minimum_bistatic_doppler_hz": { "type": ["number", "null"] },
        "maximum_bistatic_doppler_hz": { "type": ["number", "null"] }
      }
    },
    "window": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "window_id", "start_utc", "end_utc", "duration_s",
        "entry_reason", "exit_reason", "min_bistatic_range_m",
        "max_bistatic_range_m", "min_bistatic_range_rate_mps",
        "max_bistatic_range_rate_mps", "min_bistatic_doppler_hz",
        "max_bistatic_doppler_hz", "min_snr_db", "mean_snr_db",
        "max_snr_db", "peak_snr_utc"
      ],
      "properties": {
        "window_id": { "type": "string", "minLength": 1 },
        "start_utc": { "type": "string", "format": "date-time" },
        "end_utc": { "type": "string", "format": "date-time" },
        "duration_s": { "type": "number", "minimum": 0 },
        "entry_reason": { "type": "string" },
        "exit_reason": { "type": "string" },
        "min_bistatic_range_m": { "type": "number" },
        "max_bistatic_range_m": { "type": "number" },
        "min_bistatic_range_rate_mps": { "type": "number" },
        "max_bistatic_range_rate_mps": { "type": "number" },
        "min_bistatic_doppler_hz": { "type": "number" },
        "max_bistatic_doppler_hz": { "type": "number" },
        "maximum_abs_doppler_rate_hzps": { "type": ["number", "null"], "minimum": 0 },
        "min_snr_db": { "type": "number" },
        "mean_snr_db": { "type": "number" },
        "max_snr_db": { "type": "number" },
        "peak_snr_utc": { "type": "string", "format": "date-time" }
      }
    },
    "sample": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "time_offset_s", "bistatic_range_m", "bistatic_range_rate_mps",
        "bistatic_doppler_hz", "predicted_bistatic_snr_db",
        "geometrically_visible", "rf_available", "within_range_limits",
        "within_doppler_limits", "above_snr_threshold", "usable"
      ],
      "properties": {
        "time_offset_s": { "type": "number", "minimum": 0 },
        "sample_utc": { "type": ["string", "null"], "format": "date-time" },
        "bistatic_range_m": { "type": "number" },
        "bistatic_range_rate_mps": { "type": "number" },
        "bistatic_doppler_hz": { "type": "number" },
        "predicted_bistatic_snr_db": { "type": "number" },
        "geometrically_visible": { "type": "boolean" },
        "rf_available": { "type": "boolean" },
        "within_range_limits": { "type": "boolean" },
        "within_doppler_limits": { "type": "boolean" },
        "above_snr_threshold": { "type": "boolean" },
        "usable": { "type": "boolean" }
      }
    }
  }
}
```

### 9.2 Track-cue-withdrawal schema

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://passive-radar.local/schemas/track-cue-withdrawal-1.0.0.json",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "schema_version", "message_type", "message_id", "source",
    "source_instance_id", "sequence_number", "generated_utc",
    "track_id", "icao", "withdrawn_prediction_revision", "reason"
  ],
  "properties": {
    "schema_version": { "const": "1.0.0" },
    "message_type": { "const": "track_cue_withdrawal" },
    "message_id": { "type": "string", "format": "uuid" },
    "source": { "const": "ADSBConsoleApp" },
    "source_instance_id": { "type": "string", "minLength": 1 },
    "sequence_number": { "type": "integer", "minimum": 0 },
    "generated_utc": { "type": "string", "format": "date-time" },
    "track_id": { "type": "string", "minLength": 1 },
    "icao": { "type": "string", "pattern": "^[0-9A-Fa-f]{6}$" },
    "withdrawn_prediction_revision": { "type": "integer", "minimum": 1 },
    "reason": {
      "enum": [
        "track_purged", "track_stale", "track_invalid",
        "observer_disabled", "emitter_disabled",
        "configuration_invalidated", "opportunity_removed"
      ]
    }
  }
}
```

### 9.3 Heartbeat schema

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://passive-radar.local/schemas/cue-heartbeat-1.0.0.json",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "schema_version", "message_type", "message_id", "source",
    "source_instance_id", "sequence_number", "generated_utc",
    "status", "active_tracks", "cue_eligible_tracks",
    "active_observers", "implemented_observers", "enabled_emitters"
  ],
  "properties": {
    "schema_version": { "const": "1.0.0" },
    "message_type": { "const": "cue_heartbeat" },
    "message_id": { "type": "string", "format": "uuid" },
    "source": { "const": "ADSBConsoleApp" },
    "source_instance_id": { "type": "string", "minLength": 1 },
    "sequence_number": { "type": "integer", "minimum": 0 },
    "generated_utc": { "type": "string", "format": "date-time" },
    "status": { "enum": ["starting", "running", "degraded", "stopping"] },
    "active_tracks": { "type": "integer", "minimum": 0 },
    "cue_eligible_tracks": { "type": "integer", "minimum": 0 },
    "active_observers": { "type": "integer", "minimum": 0 },
    "implemented_observers": { "type": "integer", "minimum": 0 },
    "enabled_emitters": { "type": "integer", "minimum": 0 },
    "udp_destination": { "type": ["string", "null"] },
    "last_full_snapshot_utc": { "type": ["string", "null"], "format": "date-time" }
  }
}
```

### 9.4 Snapshot boundary and chunk schemas

Snapshot boundary messages shall use the same common envelope fields as the other cue
messages and the following versioned payloads:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://passive-radar.local/schemas/cue-snapshot-boundary-1.0.0.json",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "schema_version", "message_type", "message_id", "source",
    "source_instance_id", "sequence_number", "generated_utc", "snapshot_id"
  ],
  "properties": {
    "schema_version": { "const": "1.0.0" },
    "message_type": { "enum": ["cue_snapshot_begin", "cue_snapshot_end"] },
    "message_id": { "type": "string", "format": "uuid" },
    "source": { "const": "ADSBConsoleApp" },
    "source_instance_id": { "type": "string", "minLength": 1 },
    "sequence_number": { "type": "integer", "minimum": 0 },
    "generated_utc": { "type": "string", "format": "date-time" },
    "snapshot_id": { "type": "string", "minLength": 1 },
    "expected_track_count": { "type": "integer", "minimum": 0 },
    "published_track_count": { "type": "integer", "minimum": 0 },
    "failed_track_count": { "type": "integer", "minimum": 0 }
  },
  "allOf": [
    {
      "if": { "properties": { "message_type": { "const": "cue_snapshot_begin" } } },
      "then": { "required": ["expected_track_count"] }
    },
    {
      "if": { "properties": { "message_type": { "const": "cue_snapshot_end" } } },
      "then": { "required": ["published_track_count", "failed_track_count"] }
    }
  ]
}
```

Chunking shall be disabled by default. When enabled, every chunk shall use a separate
versioned schema and shall contain `logical_message_id`, `chunk_index`, `chunk_count`,
and a complete UTF-8 fragment. Chunks shall only be reassembled for diagnostics or
planner recovery; operational summary payloads should avoid chunking.

---

## 10. Example UDP Messages

### 10.1 Track cue

Values below are illustrative.

```json
{
  "schema_version": "1.0.0",
  "message_type": "track_cue",
  "message_id": "7f590108-868a-4ca6-9ef4-4f514606e860",
  "source": "ADSBConsoleApp",
  "source_instance_id": "adsb-console-mathworks-01",
  "sequence_number": 18432,
  "generated_utc": "2026-09-08T17:42:00.125Z",
  "snapshot_id": null,
  "track": {
    "track_id": "adsb:A38F98",
    "icao": "A38F98",
    "callsign": null,
    "status": "active",
    "last_report_utc": "2026-09-08T17:41:59.980Z",
    "report_age_s": 0.145,
    "state": {
      "epoch_utc": "2026-09-08T17:41:59.980Z",
      "reference_frame": "ENU",
      "reference_origin_id": "boston_enu_origin",
      "position_enu_m": [12042.3, -8410.5, 6477.0],
      "velocity_enu_mps": [-91.4, 153.8, 0.0],
      "latitude_deg": 42.173110,
      "longitude_deg": -71.298570,
      "altitude_m_msl": 6477.0,
      "ground_speed_mps": 177.9,
    "track_angle_deg": 329.2,
    "vertical_rate_mps": 0.0,
    "quality": { "vertical_rate_assumed": true }
    }
  },
  "prediction": {
    "prediction_id": "A38F98-r17",
    "revision": 17,
    "created_utc": "2026-09-08T17:42:00.100Z",
    "valid_until_utc": "2026-09-08T17:52:00.100Z",
    "horizon_s": 600,
    "sample_interval_s": 10,
    "motion_model": "constant_velocity_enu",
    "maturity": "stable",
    "update_reason": "periodic_refresh",
    "validation": {
      "position_error_m": 83.4,
      "position_error_threshold_m": 1000.0,
      "velocity_error_mps": 1.7,
      "velocity_error_threshold_mps": 10.0,
      "heading_change_deg": 0.4,
      "heading_change_threshold_deg": 3.0
    }
  },
  "opportunities": [
    {
      "opportunity_id": "A38F98:mathworks_apple_hill:WUTF-TV:r17",
      "observer_id": "mathworks_apple_hill",
      "observer_name": "MathWorks Apple Hill Parking Lot",
      "emitter_id": "WUTF-TV",
      "transmitter_site_id": "cbs_tower_needham_ma",
      "carrier_frequency_hz": 503000000,
      "rf_channel": 19,
      "emitter_enabled": true,
      "observer_can_receive": true,
      "models": {
        "bistatic_range_definition": "tx_target_plus_target_rx_minus_tx_rx",
        "doppler_source": "analytic_derivative_of_bistatic_range",
        "doppler_sign_convention": "positive_for_decreasing_bistatic_path",
        "snr_model_id": "console_bisnr_v1",
        "assumed_rcs_dbsm": 10.0,
        "detection_threshold_db": -10.0
      },
      "current": {
        "time_offset_s": 0,
        "sample_utc": "2026-09-08T17:42:00.100Z",
        "bistatic_range_m": 27083.4,
        "bistatic_range_rate_mps": -107.2,
        "bistatic_doppler_hz": 179.7,
        "predicted_bistatic_snr_db": 3.1,
        "geometrically_visible": true,
        "rf_available": true,
        "within_range_limits": true,
        "within_doppler_limits": true,
        "above_snr_threshold": true,
        "usable": true
      },
      "summary": {
        "has_usable_window": true,
        "next_window_start_utc": "2026-09-08T17:42:00.100Z",
        "next_window_end_utc": "2026-09-08T17:47:20.100Z",
        "total_usable_duration_s": 320.0,
        "maximum_snr_db": 7.2,
        "maximum_snr_utc": "2026-09-08T17:44:10.100Z",
        "minimum_bistatic_range_m": 18453.2,
        "maximum_bistatic_range_m": 51220.8,
        "minimum_bistatic_doppler_hz": -221.5,
        "maximum_bistatic_doppler_hz": 46.2
      },
      "windows": [
        {
          "window_id": "A38F98:mathworks_apple_hill:WUTF-TV:r17:w0",
          "start_utc": "2026-09-08T17:42:00.100Z",
          "end_utc": "2026-09-08T17:47:20.100Z",
          "duration_s": 320.0,
          "entry_reason": "prediction_start_inside_usable_fov",
          "exit_reason": "snr_below_threshold",
          "min_bistatic_range_m": 18453.2,
          "max_bistatic_range_m": 51220.8,
          "min_bistatic_range_rate_mps": -118.4,
          "max_bistatic_range_rate_mps": 24.7,
          "min_bistatic_doppler_hz": -198.5,
          "max_bistatic_doppler_hz": 41.4,
          "maximum_abs_doppler_rate_hzps": 2.6,
          "min_snr_db": -9.9,
          "mean_snr_db": 1.8,
          "max_snr_db": 7.2,
          "peak_snr_utc": "2026-09-08T17:44:10.100Z"
        }
      ],
      "history": null
    }
  ]
}
```

### 10.2 Withdrawal

```json
{
  "schema_version": "1.0.0",
  "message_type": "track_cue_withdrawal",
  "message_id": "fa88d7d1-8067-40e8-b86c-b29ac88538fe",
  "source": "ADSBConsoleApp",
  "source_instance_id": "adsb-console-mathworks-01",
  "sequence_number": 18433,
  "generated_utc": "2026-09-08T17:43:02.500Z",
  "track_id": "adsb:A38F98",
  "icao": "A38F98",
  "withdrawn_prediction_revision": 17,
  "reason": "track_purged"
}
```

### 10.3 Heartbeat

```json
{
  "schema_version": "1.0.0",
  "message_type": "cue_heartbeat",
  "message_id": "c1469676-f82b-44f8-b9ba-77172bcceb86",
  "source": "ADSBConsoleApp",
  "source_instance_id": "adsb-console-mathworks-01",
  "sequence_number": 18434,
  "generated_utc": "2026-09-08T17:43:05.000Z",
  "status": "running",
  "active_tracks": 129,
  "cue_eligible_tracks": 106,
  "active_observers": 2,
  "implemented_observers": 1,
  "enabled_emitters": 16,
  "udp_destination": "127.0.0.1:31001",
  "last_full_snapshot_utc": "2026-09-08T17:42:30.000Z"
}
```

### 10.4 Snapshot boundary messages

```json
{
  "schema_version": "1.0.0",
  "message_type": "cue_snapshot_begin",
  "message_id": "e542cd02-2d1a-420f-ba15-b8d823aba5fa",
  "source": "ADSBConsoleApp",
  "source_instance_id": "adsb-console-mathworks-01",
  "sequence_number": 19000,
  "generated_utc": "2026-09-08T17:45:00.000Z",
  "snapshot_id": "snapshot-20260908T174500Z",
  "expected_track_count": 106
}
```

```json
{
  "schema_version": "1.0.0",
  "message_type": "cue_snapshot_end",
  "message_id": "2937d6a7-3955-418e-8e00-5317e89c318d",
  "source": "ADSBConsoleApp",
  "source_instance_id": "adsb-console-mathworks-01",
  "sequence_number": 19107,
  "generated_utc": "2026-09-08T17:45:01.250Z",
  "snapshot_id": "snapshot-20260908T174500Z",
  "published_track_count": 106,
  "failed_track_count": 0
}
```

---

## 11. Configuration Schema and Example

### 11.1 Configuration schema

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://passive-radar.local/schemas/cue-config-1.0.0.json",
  "type": "object",
  "additionalProperties": false,
  "required": ["cue_prediction", "udp_output"],
  "properties": {
    "cue_prediction": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "enabled", "prediction_horizon_s", "prediction_sample_interval_s",
        "maximum_prediction_age_s", "minimum_track_history_s",
        "stable_track_history_s", "maximum_adsb_report_age_s",
        "payload_mode"
      ],
      "properties": {
        "enabled": { "type": "boolean" },
        "prediction_horizon_s": { "type": "number", "minimum": 300 },
        "prediction_sample_interval_s": { "type": "number", "exclusiveMinimum": 0 },
        "maximum_prediction_age_s": { "type": "number", "exclusiveMinimum": 0 },
        "minimum_track_history_s": { "type": "number", "minimum": 0 },
        "stable_track_history_s": { "type": "number", "minimum": 0 },
        "maximum_adsb_report_age_s": { "type": "number", "exclusiveMinimum": 0 },
        "maneuver_heading_change_deg": { "type": "number", "minimum": 0, "maximum": 180 },
        "maneuver_speed_change_mps": { "type": "number", "minimum": 0 },
        "maneuver_vertical_rate_change_mps": { "type": "number", "minimum": 0 },
        "position_error_absolute_m": { "type": "number", "minimum": 0 },
        "position_error_relative_fraction": { "type": "number", "minimum": 0 },
        "altitude_error_absolute_m": { "type": "number", "minimum": 0 },
        "velocity_error_absolute_mps": { "type": "number", "minimum": 0 },
        "velocity_error_relative_fraction": { "type": "number", "minimum": 0 },
        "bistatic_range_error_absolute_m": { "type": "number", "minimum": 0 },
        "bistatic_range_error_relative_fraction": { "type": "number", "minimum": 0 },
        "bistatic_doppler_error_absolute_hz": { "type": "number", "minimum": 0 },
        "bistatic_doppler_error_relative_fraction": { "type": "number", "minimum": 0 },
        "default_detection_threshold_db": { "type": "number" },
        "default_target_rcs_dbsm": { "type": "number" },
        "doppler_sign_convention": { "type": "string", "minLength": 1 },
        "payload_mode": { "enum": ["summary", "sampled"] },
        "include_current_values": { "type": "boolean" },
        "include_window_summaries": { "type": "boolean" },
        "include_sampled_history": { "type": "boolean" }
      }
    },
    "udp_output": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "enabled", "destination_address", "destination_port",
        "source_address", "maximum_datagram_bytes",
        "heartbeat_interval_s", "full_snapshot_interval_s",
        "oversize_policy"
      ],
      "properties": {
        "enabled": { "type": "boolean" },
        "destination_address": { "type": "string", "minLength": 1 },
        "destination_port": { "type": "integer", "minimum": 1, "maximum": 65535 },
        "source_address": { "type": ["string", "null"], "minLength": 1 },
        "maximum_datagram_bytes": { "type": "integer", "minimum": 512, "maximum": 65507 },
        "heartbeat_interval_s": { "type": "number", "exclusiveMinimum": 0 },
        "full_snapshot_interval_s": { "type": "number", "exclusiveMinimum": 0 },
        "oversize_policy": { "enum": ["omit_history", "chunk", "reject"] }
      }
    }
  }
}
```

### 11.2 Example configuration

```json
{
  "cue_prediction": {
    "enabled": true,
    "prediction_horizon_s": 600,
    "prediction_sample_interval_s": 10,
    "maximum_prediction_age_s": 30,
    "minimum_track_history_s": 2,
    "stable_track_history_s": 30,
    "maximum_adsb_report_age_s": 20,
    "maneuver_heading_change_deg": 3.0,
    "maneuver_speed_change_mps": 10.0,
    "maneuver_vertical_rate_change_mps": 3.0,
    "position_error_absolute_m": 1000.0,
    "position_error_relative_fraction": 0.02,
    "altitude_error_absolute_m": 150.0,
    "velocity_error_absolute_mps": 10.0,
    "velocity_error_relative_fraction": 0.05,
    "bistatic_range_error_absolute_m": 500.0,
    "bistatic_range_error_relative_fraction": 0.01,
    "bistatic_doppler_error_absolute_hz": 10.0,
    "bistatic_doppler_error_relative_fraction": 0.05,
    "default_detection_threshold_db": -10.0,
    "default_target_rcs_dbsm": 10.0,
    "doppler_sign_convention": "positive_for_decreasing_bistatic_path",
    "payload_mode": "summary",
    "include_current_values": true,
    "include_window_summaries": true,
    "include_sampled_history": false
  },
  "udp_output": {
    "enabled": true,
    "destination_address": "127.0.0.1",
    "destination_port": 31001,
    "source_address": "0.0.0.0",
    "maximum_datagram_bytes": 1200,
    "heartbeat_interval_s": 5,
    "full_snapshot_interval_s": 30,
    "oversize_policy": "omit_history"
  }
}
```

All configuration shall be validated at startup. Invalid values shall produce a specific error naming the field and shall not silently substitute an unrelated default.

---

## 12. Event and Publication Logic

```text
On new SBS/ADS-B report:

    Update track state and history.

    If track changed from ineligible to eligible:
        Generate revision 1 or next revision.
        Publish track_cue with initial_track.

    Else compare the observed state with the propagated prediction.

    If maneuver thresholds are exceeded:
        Generate next revision.
        Publish track_cue with track_maneuver.

    Else if prediction-error thresholds are exceeded:
        Generate next revision.
        Publish track_cue with prediction_error.

    Else if prediction age exceeds maximum_prediction_age_s:
        Generate next revision.
        Publish track_cue with periodic_refresh.

    Else:
        Update instantaneous UI values.
        Do not publish a redundant full revision.
```

Additional event behavior:

```text
On track purge or invalidation:
    Publish track_cue_withdrawal.

On observer configuration change:
    Recompute affected tracks and publish revisions.

On emitter configuration change:
    Recompute affected tracks and publish revisions.

On UDP enable, destination change, or publisher restart:
    Establish a new source_instance_id where appropriate.
    Publish a full snapshot.

On full-snapshot timer:
    Publish snapshot_begin.
    Republish every eligible active track.
    Publish snapshot_end.
```

---

## 13. Logging, Diagnostics, and Metrics

Log:

- cue engine startup and shutdown,
- UDP destination and socket status,
- initial cue creation,
- prediction revision and reason,
- maneuver detection,
- prediction-error detection,
- opportunity/window creation and removal,
- cue transmission,
- cue withdrawal,
- oversized datagram handling,
- serialization errors,
- socket errors,
- invalid combinations,
- stale tracks.

Examples:

```text
INFO CueUpdate ICAO=A38F98 Revision=17 Reason=periodic_refresh Opportunities=12 Windows=7
```

```text
INFO CueUpdate ICAO=A38F98 Revision=18 Reason=track_maneuver HeadingDeltaDeg=4.8 ThresholdDeg=3.0
```

```text
DEBUG CueTransmit ICAO=A38F98 Revision=18 Sequence=18440 Bytes=8421 Destination=127.0.0.1:31001
```

Maintain counters for:

- cues generated,
- cues transmitted,
- cues withdrawn,
- prediction recalculations by reason,
- transmission failures,
- serialization failures,
- oversized messages,
- active windows,
- current cue-eligible tracks,
- maximum and mean prediction computation latency,
- maximum and mean serialized message size.

---

## 14. Unit Test Requirements

### 14.1 Geometry tests

Cover:

1. target at receiver,
2. target at transmitter,
3. target on transmitter-receiver baseline,
4. motion parallel to baseline,
5. motion perpendicular to baseline,
6. monostatic limiting geometry,
7. stationary target,
8. climbing and descending target,
9. two emitters sharing one site,
10. two sites using the same carrier frequency.

### 14.2 Range-rate derivative tests

For deterministic and randomized trajectories:

1. calculate analytic bistatic range rate,
2. calculate a centered finite-difference derivative of bistatic range,
3. compare the results.

Normal-geometry tolerance:

```text
absolute range-rate error <= 0.01 m/s
```

### 14.3 Doppler tests

Verify:

- Doppler equals configured sign times range rate divided by wavelength,
- range and range rate do not change with carrier frequency,
- Doppler scales linearly with carrier frequency,
- sign convention is consistent with existing console display behavior,
- Doppler bounds enclose all internal samples.

### 14.4 SNR regression tests

At the current epoch, the prediction engine shall reproduce existing Observer Focus and Track Focus BiSNR calculations.

Default tolerance:

```text
absolute difference <= 0.1 dB
```

Any intentional model correction shall update the regression baseline and document the reason.

### 14.5 Window extraction tests

Cover:

- always invisible,
- always below threshold,
- always usable,
- one entry and one exit,
- multiple disjoint intervals,
- exactly tangent threshold crossing,
- entry at prediction start,
- exit at prediction horizon,
- range-limit transition,
- Doppler-limit transition,
- SNR transition,
- one-second boundary-refinement target.

### 14.6 Trigger tests

Verify revisions on:

- initial eligibility,
- heading maneuver,
- speed maneuver,
- vertical-rate change,
- absolute position error,
- relative position error,
- age timeout,
- observer configuration change,
- emitter configuration change.

Verify no revision storm from sub-threshold jitter.

### 14.7 Serialization and schema tests

Verify:

- all examples validate against schemas,
- required fields cannot be omitted,
- invalid ICAO and timestamps are rejected,
- non-finite numeric values are not serialized,
- summary mode omits history or sets it to null,
- sampled mode includes valid ordered samples,
- schema version is present,
- unknown enum values are rejected.

### 14.8 UDP publisher tests

Using a loopback receiver or mock publisher, verify:

- destination configuration,
- one JSON object per datagram,
- UTF-8 encoding,
- increasing sequence numbers,
- unique message IDs,
- source-instance behavior across restart,
- heartbeat timing,
- snapshot bracketing,
- oversize-policy behavior,
- chunk metadata if chunking is implemented,
- non-blocking failure handling.

---

## 15. Replay-Based Integration Test Plan

The existing Python playback utility shall be treated as the primary integration-test source. The playback tool presents prerecorded SBS data over TCP so that the console sees the playback source as a normal field `dump1090` receiver.

Example invocation:

```powershell
Set-Location .\100_ADSB_Console_Respin\scripts
.\adsb-playback.ps1 ..\..\000_sbs_for_ELAD_cleanup\sbs_clean\10_adsb_20220413_060850.csv.gz
```

Available playback controls include:

```text
--bind
--max-lines
--nth
--rate
--status-interval
--no-preserve-timing
--no-rebase-timestamps
```

### 15.1 Source corpus

Use the following 11 files as the versioned integration corpus:

```text
10_adsb_20220413_060850.csv.gz
11_adsb_20220413_060850.csv.gz
12_adsb_20220413_060850.csv.gz
13_adsb_20220413_060850.csv.gz
14_adsb_20220413_060850.csv.gz
15_adsb_20220413_060850.csv.gz
16_adsb_20220413_060850.csv.gz
17_adsb_20220413_060850.csv.gz
18_adsb_20220413_060850.csv.gz
19_adsb_20220413_060850.csv.gz
20_adsb_20220413_060850.csv.gz
```

The corpus represents approximately two hours of prerecorded ADS-B traffic. Tests shall not alter the source files.

### 15.2 Corpus manifest

Create a generated manifest containing, for every source file:

- file name,
- byte size,
- SHA-256 hash,
- first source timestamp,
- last source timestamp,
- elapsed source duration,
- line/message count,
- distinct ICAO count,
- count of position-bearing reports,
- count of velocity-bearing reports,
- count of altitude-bearing reports,
- candidate tracks with at least 10, 30, 60, 300, and 600 seconds of continuity,
- candidate heading, speed, altitude, and vertical-rate changes,
- malformed-line count.

The manifest shall make regression tests reproducible and shall detect accidental corpus changes.

### 15.3 Logical test partitions

Do not physically duplicate large source data unless necessary. Prefer a partition manifest that points to:

- source file,
- starting timestamp or line,
- ending timestamp or line,
- optional ICAO filter,
- expected scenario classification.

Create these partitions:

#### Partition A: Startup smoke test

- Duration: approximately 30 to 60 seconds.
- Purpose: connection, parsing, first track eligibility, first cue, heartbeat, and UI stability.
- Run at accelerated rate where practical.

#### Partition B: Track maturity test

- Duration: at least 60 seconds.
- Select tracks that persist from initial through stabilizing to stable maturity.
- Verify expected maturity transitions and periodic refresh.

#### Partition C: Stable straight-track prediction test

- Duration: 5 to 10 minutes.
- Select tracks with low observed heading and speed variation.
- Verify that constant-velocity predictions remain within configured thresholds and do not create excessive maneuver revisions.

#### Partition D: Maneuver and prediction-error test

- Select tracks with detectable heading, speed, altitude, or vertical-rate changes.
- Verify update reason selection and revision behavior.
- If the corpus lacks a sufficiently clear maneuver, create a derived test stream by selecting and minimally transforming a copy for test use. Preserve the original corpus and label transformed data as synthetic.

#### Partition E: Track lifecycle test

- Include track appearance, eligibility, stale transition, purge, and reappearance if available.
- Verify withdrawals and new cue revisions.

#### Partition F: High-density air-picture test

- Select the interval with the highest active-track count.
- Verify computation latency, message rate, message size, UI responsiveness, and UDP loss behavior.

#### Partition G: Multi-observer and multi-emitter test

- Run against the production-like observer/emitter configuration.
- Verify all compatible combinations are generated.
- Verify colocated emitters share `transmitter_site_id` but retain unique emitter IDs and carrier frequencies.

#### Partition H: Full-corpus endurance test

- Replay all 11 files in chronological order.
- Verify no crash, unbounded memory growth, revision runaway, sequence-number reset, or stale cue leakage.
- Record aggregate metrics and compare with a stored baseline.

### 15.4 Replay modes

Use at least four modes:

#### Mode 1: Preserved timing

Use for final end-to-end behavior and timer validation.

```powershell
.\adsb-playback.ps1 <files>
```

#### Mode 2: Accelerated timing

Use `--rate` for practical regression cycles. Verify which clocks are based on message timestamps versus wall-clock time. Prediction semantics should use rebased event timestamps consistently.

```powershell
.\adsb-playback.ps1 --rate 10 <files>
```

#### Mode 3: Thinned input

Use `--nth` to evaluate robustness under reduced report rate and missing updates.

```powershell
.\adsb-playback.ps1 --nth 5 <files>
```

#### Mode 4: Bounded smoke run

Use `--max-lines` for quick CI and developer tests.

```powershell
.\adsb-playback.ps1 --max-lines 50000 --rate 20 <file>
```

### 15.5 UDP capture harness

Create a lightweight UDP test receiver that:

- binds to the configured cue port,
- records each datagram with receive time,
- parses JSON,
- validates against the appropriate schema,
- detects duplicate message IDs,
- checks monotonic sequence behavior,
- tracks latest revision per track,
- checks snapshot completeness,
- writes newline-delimited JSON and a summary report.

The harness shall calculate:

- datagrams received,
- valid and invalid messages,
- message counts by type,
- unique tracks,
- revisions by reason,
- withdrawals,
- schema failures,
- duplicate IDs,
- out-of-order sequence numbers,
- stale revisions,
- maximum datagram size,
- mean datagram size,
- cue latency from `generated_utc` to receive time.

### 15.6 Golden-output strategy

Avoid requiring byte-identical JSON because UUIDs, timestamps, ordering of independent tracks, and periodic timers may vary.

Compare normalized semantic output:

- strip message IDs,
- normalize source instance IDs,
- compare track ID and revision sequences,
- compare update reasons,
- compare opportunity counts,
- compare observer/emitter/site identities,
- compare numeric fields within tolerances,
- compare window boundaries within one second,
- compare message-type counts within documented timer tolerances.

For selected deterministic tracks, store compact golden summaries rather than the entire two-hour output.

### 15.7 Replay integration acceptance thresholds

Initial thresholds:

- zero application crashes,
- zero unhandled exceptions,
- zero invalid JSON messages,
- zero schema-invalid operational messages,
- zero datagrams above configured maximum,
- zero stale tracks remaining after expected withdrawal/purge,
- all stable eligible tracks produce cues,
- all purged tracks produce a withdrawal or are removed by a documented full-snapshot reconciliation rule,
- no duplicate message IDs,
- prediction revision monotonically increases per track,
- sequence number monotonically increases within one source instance,
- current BiSNR regression difference no greater than 0.1 dB,
- window boundaries within one second of refined internal results,
- analytic and numerical range rate agree within 0.01 m/s under normal geometry,
- memory does not exhibit unbounded growth during full-corpus replay,
- UI remains responsive during the high-density partition.

### 15.8 Suggested test execution tiers

#### Tier 0: Unit tests

No playback. Run on every build.

#### Tier 1: Developer smoke

One bounded partition, accelerated. Target runtime under two minutes.

#### Tier 2: Pull-request regression

Startup, maturity, stable-track, maneuver, lifecycle, and high-density partitions. Target runtime under 10 to 15 minutes.

#### Tier 3: Nightly full corpus

All 11 files at accelerated rate with UDP capture and semantic comparison.

#### Tier 4: Release candidate

Full corpus with preserved timing, production-like observer/emitter configuration, UI enabled, and UDP planner emulator connected.

---

## 16. End-to-End Integration Tests

### IT-001: Initial cue generation

1. Start UDP capture harness.
2. Start ADSBConsoleApp.
3. Start bounded playback.
4. Wait for first eligible track.
5. Verify a `track_cue` is received.
6. Verify maturity begins as `initial` or `stabilizing`.
7. Verify all compatible observer-emitter opportunities are present.

### IT-002: Stable maturity and periodic refresh

1. Replay a continuous track for at least 60 seconds.
2. Verify maturity becomes `stable` after the configured history interval.
3. Verify refresh occurs at the configured maximum age.
4. Verify revision increases exactly once per refresh interval, subject to event timing tolerance.

### IT-003: Maneuver-triggered revision

1. Replay a selected maneuver partition.
2. Verify a threshold is exceeded.
3. Verify `update_reason` is `track_maneuver`.
4. Verify predictions and windows are regenerated.
5. Verify downstream revision supersedes the previous revision.

### IT-004: Track purge and withdrawal

1. Replay a track that disappears.
2. Wait for configured stale and purge behavior.
3. Verify `track_cue_withdrawal`.
4. Verify the planner emulator removes the track.

### IT-005: Current-display regression

1. Capture the existing current Observer Focus top-three results for selected epochs.
2. Run the new prediction implementation at the same epochs.
3. Verify emitter ranking and BiSNR values within tolerance.
4. Verify Track Focus CPA information remains unchanged unless intentionally corrected.

### IT-006: UDP loss recovery

1. Intentionally discard selected cue datagrams in the capture harness or planner emulator.
2. Continue processing.
3. Verify the next full snapshot reconstructs current active state.
4. Verify stale tracks not present in the snapshot are removed according to planner reconciliation rules.

### IT-007: Publisher restart

1. Replay continuously.
2. Restart ADSBConsoleApp or only the publisher component.
3. Verify new source instance behavior.
4. Verify sequence handling is unambiguous.
5. Verify a full snapshot follows restart.

### IT-008: High-density performance

1. Run the densest corpus partition at accelerated rate.
2. Measure prediction latency, UI update latency, datagram rate, and message size.
3. Verify no runaway CPU, memory, or revision rate.

### IT-009: Reduced report rate

1. Run a stable partition with `--nth 5` and then `--nth 10`.
2. Verify maturity and prediction validity respond appropriately.
3. Verify stale behavior is consistent with configuration.
4. Verify no crashes from missing fields or sparse updates.

### IT-010: Full two-hour corpus

1. Run all 11 files in chronological order.
2. Capture all UDP output.
3. Validate all messages.
4. Compare semantic metrics with the stored baseline.
5. Produce an automated test report.

---

## 17. Performance and Operational Requirements

Initial targets, subject to profiling on the deployment machine:

- cue calculation shall not block SBS ingestion,
- UDP publishing shall not block track maintenance or UI rendering,
- prediction work should be cancellable or superseded when a newer revision is required,
- stale prediction results shall not overwrite newer revisions,
- one track failure shall not prevent other tracks from being processed,
- summary mode should remain below the configured datagram limit for normal track/emitter counts,
- all externally visible timestamps shall be UTC ISO 8601,
- non-finite floating-point values shall never appear in JSON,
- prediction computations shall be deterministic for the same state and configuration within floating-point tolerance.

The implementation shall expose enough timing metrics to set final latency limits after baseline profiling.

---

## 18. Acceptance Criteria

The feature is complete when:

1. Existing SBS ingestion and track organization continue to operate.
2. Existing Observer Focus behavior remains available.
3. Existing Track Focus behavior remains available.
4. Each eligible track produces predictions for every compatible observer-emitter pair.
5. Every opportunity identifies both emitter and transmitter site.
6. Bistatic range uses the documented excess-path definition.
7. Bistatic range rate is calculated analytically.
8. Doppler is derived from range rate and carrier frequency.
9. Predicted SNR uses the existing BiSNR model unless an approved change is documented.
10. Geometric, RF, threshold, range, Doppler, and usable states are distinguishable.
11. Zero, one, or multiple usable windows are supported.
12. Window boundaries are refined to one-second target accuracy.
13. Initial, maneuver, prediction-error, periodic-refresh, and configuration-change revisions are supported.
14. Track purge or invalidation produces a withdrawal or documented snapshot reconciliation.
15. UDP destination and payload behavior are configurable.
16. Operational messages are valid UTF-8 JSON and validate against versioned schemas.
17. Duplicate, stale, and out-of-order messages can be detected by the planner.
18. Periodic snapshots allow recovery from UDP message loss or planner restart.
19. Summary and sampled payload modes are supported.
20. Unit tests cover geometry, derivatives, Doppler, SNR, windows, triggers, schemas, and UDP behavior.
21. Replay integration tests use the prerecorded SBS corpus.
22. A source-corpus manifest and logical partition manifest exist.
23. Developer smoke, pull-request regression, nightly corpus, and release-candidate test tiers are documented and runnable.
24. Full-corpus replay completes without crashes, unhandled exceptions, invalid messages, or unbounded memory growth.
25. Current BiSNR values match the existing implementation within 0.1 dB unless a documented correction is approved.
26. The planner-facing contract is independent of terminal UI classes.
27. Configuration fields and example configurations are documented.
28. Logging and operational counters are implemented.

---

## 19. Implementation Guidance for CODEX

### 19.1 Repository discovery before edits

Before changing code, inspect and report:

- repository structure,
- application language and framework,
- track-management types,
- SBS parser and connection lifecycle,
- observer types and configuration,
- emitter and transmitter-site types,
- current CPA calculation,
- current BiSNR/link-budget implementation,
- existing Doppler sign convention,
- UI state model and focus-mode renderers,
- configuration loading and validation,
- logging framework,
- existing test framework,
- current concurrency model.

Do not duplicate existing geometry or link-budget logic without identifying why reuse is impossible.

### 19.2 Required module separation

Implement the following logical boundaries, adapting names to repository conventions:

```text
Track State and History
        |
        v
Prediction Trigger Evaluator
        |
        v
Track Motion Propagator
        |
        v
Passive Observation Prediction Engine
        |
        +--------------------+
        |                    |
        v                    v
Console View Model      Cue DTO Mapper
                              |
                              v
                       JSON Serializer
                              |
                              v
                         UDP Publisher
```

Suggested abstractions:

```text
ITrackMotionPropagator
IPassiveObservationPredictor
IPredictionTriggerEvaluator
ICueSerializer
ICuePublisher
IClock
IMessageIdProvider
ISequenceProvider
```

Use injectable time and identifier providers so tests can be deterministic.

### 19.3 Implementation sequence

1. Add or normalize `TransmitterSite` and `Emitter` separation.
2. Extract or wrap existing bistatic geometry calculations.
3. Implement analytic range-rate and Doppler calculation.
4. Add prediction-domain objects.
5. Add constant-velocity propagation.
6. Add time-varying SNR evaluation using the existing model.
7. Add visibility-state evaluation.
8. Add window extraction and boundary refinement.
9. Add trigger evaluation and revision state.
10. Add versioned DTOs and schemas.
11. Add JSON serialization.
12. Add UDP publisher interface and implementation.
13. Add heartbeat, withdrawal, and snapshot behavior.
14. Integrate prediction output into Observer Focus.
15. Integrate prediction output into Track Focus.
16. Add UDP capture/planner emulator for tests.
17. Add replay corpus manifest and test partitions.
18. Run full regression and record baselines.

### 19.4 Concurrency and stale-result rules

- Do not perform expensive prediction work on the SBS input or terminal-rendering critical path.
- Assign every prediction request a track revision or generation token.
- Before publishing or committing a result, verify that the result still corresponds to the latest track generation.
- Discard superseded results.
- Ensure a track purge cannot be followed by publication of an older in-flight prediction.
- Serialize sequence-number allocation across publisher calls.

### 19.5 Error handling

- Isolate failures by track and observer-emitter combination.
- Log invalid geometry and continue processing other combinations.
- Never emit NaN or infinity.
- If one opportunity fails, either omit it with a diagnostic or mark it invalid according to payload mode.
- If serialization or UDP transmission fails, increment counters and continue unless configuration requires fail-fast startup behavior.

### 19.6 Compatibility

- Preserve existing command-line behavior and keyboard controls unless explicitly modified.
- Preserve current Observer Focus and Track Focus calculations.
- Introduce configuration defaults so the feature can be disabled.
- When cue prediction is disabled, existing application behavior shall remain unchanged.

### 19.7 Deliverables expected from CODEX

CODEX shall produce:

1. a file-level implementation plan before edits,
2. production code,
3. versioned JSON schemas,
4. example configuration,
5. unit tests,
6. UDP loopback/capture test support,
7. replay corpus manifest generator,
8. logical partition manifest,
9. integration-test scripts or documented commands,
10. updated application documentation,
11. a summary of design decisions and any deviations from this specification,
12. test results including replay partitions executed.

### 19.8 Completion report

The final implementation report shall include:

- files added and modified,
- architecture summary,
- configuration changes,
- schema locations,
- test counts and results,
- replay corpus partitions executed,
- known limitations,
- deferred requirements,
- instructions to run the console, playback source, UDP capture harness, and tests.

---

## 20. Suggested CODEX Task Prompt

```text
Implement the attached engineering specification in the existing ADSBConsoleApp repository.

First inspect the repository and provide a concise file-level implementation plan. Identify and reuse the existing track, observer, emitter, CPA, Doppler, and BiSNR implementations. Do not begin by replacing existing behavior.

Implement the feature in testable layers:
1. prediction domain model,
2. constant-velocity propagation,
3. bistatic range/range-rate/Doppler prediction,
4. time-varying BiSNR prediction,
5. visibility and observation-window extraction,
6. trigger/revision management,
7. versioned JSON DTOs and schemas,
8. UDP publication,
9. Observer Focus and Track Focus integration,
10. replay-based integration tests.

Use the existing Python/PowerShell SBS playback capability as the primary end-to-end data source. Create a manifest and logical partitions for the 11 prerecorded .csv.gz files rather than copying the source corpus. Add a UDP capture/planner-emulator test tool that validates all messages against the JSON schemas and produces a semantic regression summary.

Preserve all existing application functionality. Keep planner-facing DTOs independent of terminal UI classes. Make clocks and IDs injectable for deterministic tests. Prevent stale asynchronous computations from overwriting newer track revisions.

After implementation, report all changed files, configuration additions, test results, replay partitions exercised, known limitations, and exact commands for running the tests.
```

---

## 21. Future Extensions

Not part of this implementation, but the design should avoid blocking:

- passive-EKF covariance feedback to the planner,
- information-gain scoring,
- independent surveillance tasks,
- passive-only track initiation,
- multiple active RF observers,
- receiver task acknowledgements,
- planner-to-console status feedback,
- covariance-aware maneuver detection,
- more advanced target motion models,
- terrain-aware propagation and antenna patterns,
- transport migration from UDP JSON if future reliability or scale requires it.
