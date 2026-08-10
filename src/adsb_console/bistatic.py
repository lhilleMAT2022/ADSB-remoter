"""Passive bistatic calculations for DTV illuminators of opportunity."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from math import log10, pi
from pathlib import Path

from adsb_console.models import (
    FEET_TO_METERS,
    ObserverConfig,
    ObserverRole,
    PositionReport,
    VelocityReport,
)
from adsb_console.transforms import (
    SPEED_OF_LIGHT_MPS,
    position_to_range_az_el,
    position_velocity_to_range_rate_mps,
)

BOLTZMANN_J_PER_K = 1.380_649e-23
STANDARD_NOISE_TEMPERATURE_K = 290.0
DEFAULT_RCS_DBSM = 10.0


@dataclass(frozen=True, slots=True)
class DtvEmitter:
    """DTV transmitter entry used as a passive-radar illuminator."""

    call_sign: str
    site_name: str
    rf_channel: int
    center_frequency_mhz: float
    latitude_deg: float
    longitude_deg: float
    altitude_m: float
    eirp_kw: float

    @property
    def observer_config(self) -> ObserverConfig:
        return ObserverConfig(
            name=self.call_sign or self.site_name,
            role=ObserverRole.REMOTE,
            latitude_deg=self.latitude_deg,
            longitude_deg=self.longitude_deg,
            altitude_m=self.altitude_m,
        )


@dataclass(frozen=True, slots=True)
class BistaticMeasurement:
    """Passive bistatic result for one emitter, target, and receiver."""

    emitter: DtvEmitter
    snr_db: float
    bistatic_range_km: float
    bistatic_doppler_hz: float | None
    bearing_to_emitter_deg: float


def load_dtv_emitters(path: str | Path | None) -> list[DtvEmitter]:
    """Load DTV emitter rows from the FCC-derived CSV export."""

    if path is None:
        return []
    csv_path = Path(path)
    if not csv_path.exists():
        return []

    emitters: list[DtvEmitter] = []
    with csv_path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            try:
                emitters.append(
                    DtvEmitter(
                        call_sign=row.get("call_sign", "").strip(),
                        site_name=row.get("site_name", "").strip(),
                        rf_channel=int(row.get("rf_channel", "0") or "0"),
                        center_frequency_mhz=float(row["center_frequency_mhz"]),
                        latitude_deg=float(row["tx_latitude_deg"]),
                        longitude_deg=float(row["tx_longitude_deg"]),
                        altitude_m=float(row["tx_altitude_m"]),
                        eirp_kw=float(row["eirp_kw"]),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
    return emitters


def top_bistatic_measurements(
    *,
    emitters: list[DtvEmitter],
    receiver: ObserverConfig,
    position: PositionReport,
    velocity: VelocityReport | None,
    count: int,
    rcs_dbsm: float = DEFAULT_RCS_DBSM,
) -> list[BistaticMeasurement]:
    """Return the strongest passive bistatic emitter geometries by SNR."""

    measurements = [
        measurement
        for emitter in emitters
        if (
            measurement := bistatic_measurement(
                emitter=emitter,
                receiver=receiver,
                position=position,
                velocity=velocity,
                rcs_dbsm=rcs_dbsm,
            )
        )
        is not None
    ]
    return sorted(measurements, key=lambda item: item.snr_db, reverse=True)[:count]


def bistatic_measurement(
    *,
    emitter: DtvEmitter,
    receiver: ObserverConfig,
    position: PositionReport,
    velocity: VelocityReport | None,
    rcs_dbsm: float = DEFAULT_RCS_DBSM,
) -> BistaticMeasurement | None:
    """Compute passive bistatic SNR, excess range, and bistatic Doppler."""

    if emitter.eirp_kw <= 0.0 or emitter.center_frequency_mhz <= 0.0:
        return None

    transmitter = emitter.observer_config
    tx_to_target_m = position_to_range_az_el(position, transmitter).range_m
    rx_to_target_m = position_to_range_az_el(position, receiver).range_m
    tx_to_rx = position_to_range_az_el(
        PositionReport(
            latitude_deg=emitter.latitude_deg,
            longitude_deg=emitter.longitude_deg,
            altitude_ft=emitter.altitude_m / FEET_TO_METERS,
            reported_at=position.reported_at,
        ),
        receiver,
    )
    if tx_to_target_m <= 0.0 or rx_to_target_m <= 0.0:
        return None

    carrier_frequency_hz = emitter.center_frequency_mhz * 1_000_000.0
    wavelength_m = SPEED_OF_LIGHT_MPS / carrier_frequency_hz
    snr_db = bistatic_snr_db(
        eirp_kw=emitter.eirp_kw,
        receiver_gain_dbi=receiver.receiver_gain_dbi,
        receiver_noise_figure_db=receiver.noise_figure_db,
        receiver_bandwidth_mhz=receiver.bandwidth_mhz,
        wavelength_m=wavelength_m,
        rcs_dbsm=rcs_dbsm,
        transmitter_target_range_m=tx_to_target_m,
        target_receiver_range_m=rx_to_target_m,
    )
    bistatic_range_rate_mps = None
    if velocity is not None:
        bistatic_range_rate_mps = position_velocity_to_range_rate_mps(
            position, velocity, transmitter
        ) + position_velocity_to_range_rate_mps(position, velocity, receiver)

    return BistaticMeasurement(
        emitter=emitter,
        snr_db=snr_db,
        bistatic_range_km=(tx_to_target_m + rx_to_target_m - tx_to_rx.range_m) / 1000.0,
        bistatic_doppler_hz=None
        if bistatic_range_rate_mps is None
        else -bistatic_range_rate_mps / wavelength_m,
        bearing_to_emitter_deg=tx_to_rx.azimuth_deg,
    )


def bistatic_snr_db(
    *,
    eirp_kw: float,
    receiver_gain_dbi: float,
    receiver_noise_figure_db: float,
    receiver_bandwidth_mhz: float,
    wavelength_m: float,
    rcs_dbsm: float,
    transmitter_target_range_m: float,
    target_receiver_range_m: float,
) -> float:
    """Return bistatic radar equation SNR in dB."""

    eirp_dbw = 10.0 * log10(eirp_kw * 1000.0)
    noise_power_dbw = thermal_noise_power_dbw(receiver_bandwidth_mhz) + receiver_noise_figure_db
    return (
        eirp_dbw
        + receiver_gain_dbi
        + 20.0 * log10(wavelength_m)
        + rcs_dbsm
        - 30.0 * log10(4.0 * pi)
        - 20.0 * log10(transmitter_target_range_m)
        - 20.0 * log10(target_receiver_range_m)
        - noise_power_dbw
    )


def thermal_noise_power_dbw(bandwidth_mhz: float) -> float:
    bandwidth_hz = max(bandwidth_mhz, 1e-12) * 1_000_000.0
    return 10.0 * log10(BOLTZMANN_J_PER_K * STANDARD_NOISE_TEMPERATURE_K * bandwidth_hz)
