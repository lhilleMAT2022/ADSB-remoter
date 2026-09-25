"""Configuration loading for passive-radar cue prediction and UDP output."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from adsb_console.cue import UdpOutputConfig
from adsb_console.prediction import PredictionConfig


class CuePublicationMode(StrEnum):
    AUTOMATIC = "automatic"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class CueRuntimeConfig:
    prediction: PredictionConfig
    udp_output: UdpOutputConfig
    publication_mode: CuePublicationMode = CuePublicationMode.AUTOMATIC


def disabled_cue_runtime_config() -> CueRuntimeConfig:
    return CueRuntimeConfig(prediction=PredictionConfig(), udp_output=UdpOutputConfig())


def load_cue_runtime_config(path: str | Path | None) -> CueRuntimeConfig:
    """Load a validated JSON cue configuration, or a disabled default."""

    if path is None:
        return disabled_cue_runtime_config()
    config_path = Path(path)
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Cue configuration root must be a JSON object")
    _validate_schema(payload, config_path)
    prediction_values = _section(payload, "cue_prediction")
    udp_values = _section(payload, "udp_output")
    prediction = PredictionConfig(
        enabled=_bool(prediction_values, "enabled"),
        prediction_horizon_s=_positive(prediction_values, "prediction_horizon_s", 600.0),
        prediction_sample_interval_s=_positive(
            prediction_values, "prediction_sample_interval_s", 10.0
        ),
        maximum_prediction_age_s=_positive(prediction_values, "maximum_prediction_age_s", 30.0),
        minimum_prediction_horizon_s=_positive(
            prediction_values, "minimum_prediction_horizon_s", 30.0
        ),
        regeneration_debounce_s=_nonnegative(
            prediction_values, "regeneration_debounce_s", 1.0
        ),
        minimum_track_history_s=_nonnegative(prediction_values, "minimum_track_history_s", 2.0),
        stable_track_history_s=_nonnegative(prediction_values, "stable_track_history_s", 30.0),
        maximum_adsb_report_age_s=_positive(
            prediction_values, "maximum_adsb_report_age_s", 20.0
        ),
        maneuver_heading_change_deg=_nonnegative(
            prediction_values, "maneuver_heading_change_deg", 3.0
        ),
        maneuver_speed_change_mps=_nonnegative(
            prediction_values, "maneuver_speed_change_mps", 10.0
        ),
        maneuver_vertical_rate_change_mps=_nonnegative(
            prediction_values, "maneuver_vertical_rate_change_mps", 3.0
        ),
        maneuver_position_error_m=_nonnegative(
            prediction_values, "maneuver_position_error_m", 1_000.0
        ),
        maneuver_altitude_error_m=_nonnegative(
            prediction_values, "maneuver_altitude_error_m", 150.0
        ),
        position_error_absolute_m=_nonnegative(
            prediction_values, "position_error_absolute_m", 1_000.0
        ),
        position_error_relative_fraction=_nonnegative(
            prediction_values, "position_error_relative_fraction", 0.02
        ),
        velocity_error_absolute_mps=_nonnegative(
            prediction_values, "velocity_error_absolute_mps", 10.0
        ),
        velocity_error_relative_fraction=_nonnegative(
            prediction_values, "velocity_error_relative_fraction", 0.05
        ),
        bistatic_range_error_absolute_m=_nonnegative(
            prediction_values, "bistatic_range_error_absolute_m", 500.0
        ),
        bistatic_range_error_relative_fraction=_nonnegative(
            prediction_values, "bistatic_range_error_relative_fraction", 0.01
        ),
        bistatic_doppler_error_absolute_hz=_nonnegative(
            prediction_values, "bistatic_doppler_error_absolute_hz", 10.0
        ),
        bistatic_doppler_error_relative_fraction=_nonnegative(
            prediction_values, "bistatic_doppler_error_relative_fraction", 0.05
        ),
        detection_threshold_db=_number(prediction_values, "default_detection_threshold_db", -10.0),
        maximum_bistatic_range_m=_optional_positive(
            prediction_values, "maximum_bistatic_range_m"
        ),
        maximum_abs_bistatic_doppler_hz=_optional_positive(
            prediction_values, "maximum_abs_bistatic_doppler_hz"
        ),
    )
    maximum_datagram_bytes = int(
        _positive(udp_values, "maximum_datagram_bytes", 16_384.0)
    )
    if maximum_datagram_bytes > 65_507:
        raise ValueError("udp_output.maximum_datagram_bytes must be <= 65507")
    udp = UdpOutputConfig(
        enabled=_bool(udp_values, "enabled"),
        destination_address=_string(udp_values, "destination_address", "127.0.0.1"),
        destination_port=int(_positive(udp_values, "destination_port", 31_001.0)),
        source_address=_optional_string(udp_values, "source_address"),
        maximum_datagram_bytes=maximum_datagram_bytes,
        oversize_policy=_choice(udp_values, "oversize_policy", {"omit_history", "reject"}),
        heartbeat_interval_s=_positive(udp_values, "heartbeat_interval_s", 10.0),
        snapshot_interval_s=_positive(udp_values, "snapshot_interval_s", 60.0),
    )
    if not 1 <= udp.destination_port <= 65_535:
        raise ValueError("udp_output.destination_port must be between 1 and 65535")
    mode_value = _choice(
        payload,
        "publication_mode",
        {item.value for item in CuePublicationMode},
        CuePublicationMode.AUTOMATIC.value,
    )
    return CueRuntimeConfig(
        prediction=prediction,
        udp_output=udp,
        publication_mode=CuePublicationMode(mode_value),
    )


def _validate_schema(payload: dict[str, Any], config_path: Path) -> None:
    schema_path = config_path.parents[1] / "schemas" / "cue-config-1.0.0.json"
    if not schema_path.exists():
        return
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=str)
    if errors:
        raise ValueError(f"Invalid cue configuration: {errors[0].message}")


def _section(payload: dict[str, Any], name: str) -> dict[str, Any]:
    value = payload.get(name)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _bool(values: dict[str, Any], name: str) -> bool:
    value = values.get(name, False)
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def _number(values: dict[str, Any], name: str, default: float) -> float:
    value = values.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be a number")
    return float(value)


def _positive(values: dict[str, Any], name: str, default: float) -> float:
    value = _number(values, name, default)
    if value <= 0.0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def _nonnegative(values: dict[str, Any], name: str, default: float) -> float:
    value = _number(values, name, default)
    if value < 0.0:
        raise ValueError(f"{name} must be nonnegative")
    return value


def _optional_positive(values: dict[str, Any], name: str) -> float | None:
    if name not in values or values[name] is None:
        return None
    return _positive(values, name, 0.0)


def _string(values: dict[str, Any], name: str, default: str) -> str:
    value = values.get(name, default)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _optional_string(values: dict[str, Any], name: str) -> str | None:
    if name not in values or values[name] is None:
        return None
    return _string(values, name, "")


def _choice(values: dict[str, Any], name: str, allowed: set[str], default: str = "omit_history") -> str:
    value = _string(values, name, default)
    if value not in allowed:
        options = ", ".join(sorted(allowed))
        raise ValueError(f"{name} must be one of: {options}")
    return value
