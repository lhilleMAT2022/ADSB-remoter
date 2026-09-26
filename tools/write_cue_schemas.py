"""Write the CT 2.0.0 JSON schemas (ICD_Messages.md section 2) into schemas/.

One generator keeps the shared definitions (envelope, epoch-millisecond times) identical
across the message schemas. Run from the repository root:

    python3 tools/write_cue_schemas.py
"""

from __future__ import annotations

import json
from pathlib import Path

VERSION = "2.0.0"
ID_ROOT = "https://passive-radar.local/schemas"
SCHEMAS = Path(__file__).resolve().parents[1] / "schemas"

UTC_MS = {"type": "integer", "minimum": 0}
UTC_MS_OR_NULL = {"type": ["integer", "null"], "minimum": 0}
NUMBER_OR_NULL = {"type": ["number", "null"]}
WINDOW_REASON = {
    "enum": [
        "prediction_start_inside_usable", "prediction_horizon", "geometric_visibility",
        "rf_availability", "bistatic_range_limit", "doppler_limit", "snr_threshold",
        "state_transition",
    ]
}


def envelope(message_type: str) -> dict:
    return {
        "schema_version": {"const": VERSION},
        "message_type": {"const": message_type},
        "message_id": {"type": "string", "format": "uuid"},
        "source": {"const": "ADSBConsoleApp"},
        "source_instance_id": {"type": "string", "minLength": 1},
        "sequence_number": {"type": "integer", "minimum": 1},
        "generated_utc_ms": UTC_MS,
    }


def message(name: str, message_type: str, properties: dict, defs: dict | None = None,
            optional: tuple[str, ...] = ()) -> dict:
    props = envelope(message_type) | properties
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"{ID_ROOT}/{name}-{VERSION}.json",
        "type": "object",
        "additionalProperties": False,
        "required": [k for k in props if k not in optional],
        "properties": props,
    }
    if defs:
        schema["$defs"] = defs
    return schema


def closed(properties: dict, optional: tuple[str, ...] = ()) -> dict:
    """An object whose every property is required unless listed as optional."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [k for k in properties if k not in optional],
        "properties": properties,
    }


TRACK_CUE_DEFS = {
    "vector3": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
    "state": closed({
        "epoch_utc_ms": UTC_MS,
        "reference_frame": {"const": "ENU"},
        "reference_origin_id": {"type": "string", "minLength": 1},
        "position_enu_m": {"$ref": "#/$defs/vector3"},
        "velocity_enu_mps": {"$ref": "#/$defs/vector3"},
        "latitude_deg": {"type": "number", "minimum": -90, "maximum": 90},
        "longitude_deg": {"type": "number", "minimum": -180, "maximum": 180},
        "altitude_m_msl": {"type": "number"},
        "ground_speed_mps": NUMBER_OR_NULL,
        "track_angle_deg": NUMBER_OR_NULL,
        "vertical_rate_mps": NUMBER_OR_NULL,
        "quality": {"$ref": "#/$defs/quality"},
    }),
    "quality": closed({
        "vertical_rate_assumed": {"type": "boolean"},
        "horizontal_velocity_estimated": {"type": "boolean"},
        "position_valid": {"type": "boolean"},
        "velocity_valid": {"type": "boolean"},
    }),
    "prediction": closed({
        "revision": {"type": "integer", "minimum": 1},
        "created_utc_ms": UTC_MS,
        "valid_until_utc_ms": UTC_MS,
        "horizon_s": {"type": "number", "exclusiveMinimum": 0},
        "sample_interval_s": {"type": "number", "exclusiveMinimum": 0},
        "motion_model": {"const": "constant_velocity_enu"},
        "maturity": {"enum": ["initial", "stabilizing", "stable", "invalid"]},
        "update_reason": {"enum": [
            "initial_track", "track_maneuver", "prediction_error", "periodic_refresh",
            "observer_configuration_change", "emitter_configuration_change",
            "application_snapshot",
        ]},
        "validation": {"$ref": "#/$defs/validation"},
    }),
    "validation": closed({
        "position_error_m": NUMBER_OR_NULL,
        "position_error_threshold_m": NUMBER_OR_NULL,
        "velocity_error_mps": NUMBER_OR_NULL,
        "velocity_error_threshold_mps": NUMBER_OR_NULL,
        "heading_change_deg": NUMBER_OR_NULL,
        "heading_change_threshold_deg": NUMBER_OR_NULL,
    }),
    "models": closed({
        "bistatic_range_definition": {"const": "tx_target_plus_target_rx_minus_tx_rx"},
        "doppler_source": {"const": "analytic_derivative_of_bistatic_range"},
        "doppler_sign_convention": {"const": "positive_for_decreasing_bistatic_path"},
        "snr_model_id": {"type": "string", "minLength": 1},
        "assumed_rcs_dbsm": {"type": "number"},
        "detection_threshold_db": {"type": "number"},
    }),
    "opportunity": closed({
        "observer_id": {"type": "string", "minLength": 1},
        "emitter_id": {"type": "string", "minLength": 1},
        "carrier_frequency_hz": {"type": "number", "exclusiveMinimum": 0},
        "rf_channel": {"type": ["integer", "null"], "minimum": 2, "maximum": 69},
        "current": {"$ref": "#/$defs/sample"},
        "windows": {"type": "array", "minItems": 1, "items": {"$ref": "#/$defs/window"}},
        "summary": {"$ref": "#/$defs/summary"},
    }, optional=("summary",)),
    "sample": closed({
        "time_offset_s": {"type": "number"},
        "sample_utc_ms": UTC_MS,
        "bistatic_range_m": {"type": "number", "minimum": 0},
        "bistatic_range_rate_mps": {"type": "number"},
        "bistatic_doppler_hz": {"type": "number"},
        "predicted_bistatic_snr_db": {"type": "number"},
        "geometrically_visible": {"type": "boolean"},
        "rf_available": {"type": "boolean"},
        "within_range_limits": {"type": "boolean"},
        "within_doppler_limits": {"type": "boolean"},
        "above_snr_threshold": {"type": "boolean"},
        "usable": {"type": "boolean"},
    }),
    "window": closed({
        "start_utc_ms": UTC_MS,
        "end_utc_ms": UTC_MS,
        "duration_s": {"type": "number", "minimum": 0},
        "entry_reason": WINDOW_REASON,
        "exit_reason": WINDOW_REASON,
        "min_bistatic_range_m": {"type": "number"},
        "max_bistatic_range_m": {"type": "number"},
        "min_bistatic_range_rate_mps": {"type": "number"},
        "max_bistatic_range_rate_mps": {"type": "number"},
        "min_bistatic_doppler_hz": {"type": "number"},
        "max_bistatic_doppler_hz": {"type": "number"},
        "maximum_abs_doppler_rate_hzps": {"type": ["number", "null"], "minimum": 0},
        "min_snr_db": {"type": "number"},
        "mean_snr_db": {"type": "number"},
        "max_snr_db": {"type": "number"},
        "peak_snr_utc_ms": UTC_MS,
    }),
    "summary": closed({
        "has_usable_window": {"type": "boolean"},
        "next_window_start_utc_ms": UTC_MS_OR_NULL,
        "next_window_end_utc_ms": UTC_MS_OR_NULL,
        "total_usable_duration_s": {"type": "number", "minimum": 0},
        "maximum_snr_db": NUMBER_OR_NULL,
        "maximum_snr_utc_ms": UTC_MS_OR_NULL,
        "minimum_bistatic_range_m": NUMBER_OR_NULL,
        "maximum_bistatic_range_m": NUMBER_OR_NULL,
        "minimum_bistatic_doppler_hz": NUMBER_OR_NULL,
        "maximum_bistatic_doppler_hz": NUMBER_OR_NULL,
    }),
}


def build() -> dict[str, dict]:
    track = closed({
        "track_id": {"type": "string", "minLength": 1},
        "icao": {"type": "string", "pattern": "^[0-9A-F]{6}$"},
        "callsign": {"type": ["string", "null"]},
        "status": {"enum": ["active", "stale", "purged", "invalid"]},
        "last_report_utc_ms": UTC_MS,
        "report_age_s": {"type": "number", "minimum": 0},
        "state": {"$ref": "#/$defs/state"},
    })
    return {
        "track-cue": message("track-cue", "track_cue", {
            "snapshot_id": {"type": ["string", "null"]},
            "track": track,
            "prediction": {"$ref": "#/$defs/prediction"},
            "models": {"$ref": "#/$defs/models"},
            "opportunities": {"type": "array", "items": {"$ref": "#/$defs/opportunity"}},
        }, TRACK_CUE_DEFS),
        "track-cue-withdrawal": message("track-cue-withdrawal", "track_cue_withdrawal", {
            "track_id": {"type": "string", "minLength": 1},
            "icao": {"type": "string", "pattern": "^[0-9A-F]{6}$"},
            "withdrawn_prediction_revision": {"type": "integer", "minimum": 1},
            "reason": {"type": "string", "minLength": 1},
        }),
        "cue-heartbeat": message("cue-heartbeat", "cue_heartbeat", {
            "status": {"enum": ["starting", "running", "degraded", "stopping"]},
            "active_tracks": {"type": "integer", "minimum": 0},
            "cue_eligible_tracks": {"type": "integer", "minimum": 0},
            "active_observers": {"type": "integer", "minimum": 0},
            "enabled_emitters": {"type": "integer", "minimum": 0},
            "udp_destination": {"type": ["string", "null"]},
            "last_full_snapshot_utc_ms": UTC_MS_OR_NULL,
        }),
        "cue-snapshot-begin": message("cue-snapshot-begin", "cue_snapshot_begin", {
            "snapshot_id": {"type": "string", "minLength": 1},
            "expected_track_count": {"type": "integer", "minimum": 0},
        }),
        "cue-snapshot-end": message("cue-snapshot-end", "cue_snapshot_end", {
            "snapshot_id": {"type": "string", "minLength": 1},
            "published_track_count": {"type": "integer", "minimum": 0},
            "failed_track_count": {"type": "integer", "minimum": 0},
        }),
    }


def main() -> None:
    for name, schema in build().items():
        (SCHEMAS / f"{name}-{VERSION}.json").write_text(json.dumps(schema, indent=2) + "\n")
    # cue-config 2.0.0: 1.1.0 plus framing, dictionary and summary options, and no
    # oversize (history) policy, which fit-to-frame replaces.
    config_source = SCHEMAS / "cue-config-1.1.0.json"
    if not config_source.exists():
        config_source = SCHEMAS / "archive" / "cue-config-1.1.0.json"
    schema = json.loads(config_source.read_text())
    schema["$id"] = f"{ID_ROOT}/cue-config-{VERSION}.json"
    schema["properties"]["cue_prediction"]["properties"]["include_summary"] = {"type": "boolean"}
    udp = schema["properties"]["udp_output"]["properties"]
    udp.pop("oversize_policy", None)
    udp["encoding"] = {"enum": ["json", "deflate_dictionary"]}
    udp["dictionary_id"] = {"type": "integer", "minimum": 1, "maximum": 255}
    udp["maximum_datagram_bytes"] = {"type": "integer", "minimum": 512, "maximum": 65507}
    udp["maximum_opportunities_per_cue"] = {"type": "integer", "minimum": 1}
    (SCHEMAS / f"cue-config-{VERSION}.json").write_text(json.dumps(schema, indent=2) + "\n")
    print("wrote", ", ".join(sorted(p.name for p in SCHEMAS.glob(f"*-{VERSION}.json"))))


if __name__ == "__main__":
    main()
