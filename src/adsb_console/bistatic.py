"""Passive bistatic calculations for DTV illuminators of opportunity."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from enum import StrEnum
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
# Residual bistatic depolarization loss for a horizontally-polarized receive
# Yagi against horizontally-polarized ATSC DTV broadcast (roughly matched,
# small penalty from multipath/aircraft-skin scattering depolarization).
DEFAULT_POLARIZATION_LOSS_DB = 1.0
# Connector/insertion and implementation loss downstream of the receive
# antenna. Feedline loss is not separately budgeted here because a
# mast-mounted powered preamp overcomes it ahead of the run to the receiver.
DEFAULT_SYSTEM_LOSS_DB = 2.0


class DtvBand(StrEnum):
    """Supported DTV bands for illuminator filtering."""

    LOW_VHF = "low-vhf"
    HIGH_VHF = "high-vhf"
    UHF = "uhf"


@dataclass(frozen=True, slots=True)
class DtvEmitter:
    """DTV transmitter entry used as a passive-radar illuminator."""

    facility_id: str
    call_sign: str
    site_name: str
    asrn: str
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

    @property
    def band(self) -> DtvBand | None:
        return dtv_band(self.rf_channel, self.center_frequency_mhz)

    @property
    def tower_key(self) -> str:
        if self.asrn:
            return f"asrn:{self.asrn}"
        return (
            f"geo:{self.site_name.lower()}:"
            f"{self.latitude_deg:.5f}:{self.longitude_deg:.5f}:{self.altitude_m:.1f}"
        )


@dataclass(frozen=True, slots=True)
class BistaticSnrBreakdown:
    """Bistatic radar equation SNR, itemized by term, all in dB.

    Every field except `snr_db` is a signed contribution; summing them
    reproduces `snr_db`.
    """

    eirp_dbw: float
    receiver_gain_dbi: float
    wavelength_gain_db: float
    rcs_dbsm: float
    spreading_loss_db: float
    tx_range_loss_db: float
    rx_range_loss_db: float
    noise_floor_db: float
    polarization_loss_db: float
    system_loss_db: float
    snr_db: float


@dataclass(frozen=True, slots=True)
class BistaticMeasurement:
    """Passive bistatic result for one emitter, target, and receiver."""

    emitter: DtvEmitter
    snr_db: float
    snr_breakdown: BistaticSnrBreakdown
    bistatic_range_km: float
    bistatic_doppler_hz: float | None
    bearing_to_emitter_deg: float


def load_dtv_emitters(
    path: str | Path | None, bands: set[DtvBand] | None = None
) -> list[DtvEmitter]:
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
                emitter = DtvEmitter(
                    facility_id=row.get("facility_id", "").strip(),
                    call_sign=row.get("call_sign", "").strip(),
                    site_name=row.get("site_name", "").strip(),
                    asrn=row.get("asrn", "").strip(),
                    rf_channel=int(row.get("rf_channel", "0") or "0"),
                    center_frequency_mhz=float(row["center_frequency_mhz"]),
                    latitude_deg=float(row["tx_latitude_deg"]),
                    longitude_deg=float(row["tx_longitude_deg"]),
                    altitude_m=float(row["tx_altitude_m"]),
                    eirp_kw=float(row["eirp_kw"]),
                )
            except (KeyError, TypeError, ValueError):
                continue
            if bands is not None and emitter.band not in bands:
                continue
            emitters.append(emitter)
    return emitters


def top_bistatic_measurements(
    *,
    emitters: list[DtvEmitter],
    receiver: ObserverConfig,
    position: PositionReport,
    velocity: VelocityReport | None,
    count: int,
    rcs_dbsm: float = DEFAULT_RCS_DBSM,
    polarization_loss_db: float = DEFAULT_POLARIZATION_LOSS_DB,
    system_loss_db: float = DEFAULT_SYSTEM_LOSS_DB,
) -> list[BistaticMeasurement]:
    """Return the strongest passive bistatic emitter geometries by SNR."""

    measurements: list[BistaticMeasurement] = []
    for emitter in emitters:
        measurement = bistatic_measurement(
            emitter=emitter,
            receiver=receiver,
            position=position,
            velocity=velocity,
            rcs_dbsm=rcs_dbsm,
            polarization_loss_db=polarization_loss_db,
            system_loss_db=system_loss_db,
        )
        if measurement is not None:
            measurements.append(measurement)
    measurements = sorted(measurements, key=lambda item: item.snr_db, reverse=True)
    diverse_measurements: list[BistaticMeasurement] = []
    used_towers: set[str] = set()
    for measurement in measurements:
        tower_key = measurement.emitter.tower_key
        if tower_key in used_towers:
            continue
        diverse_measurements.append(measurement)
        used_towers.add(tower_key)
        if len(diverse_measurements) >= count:
            break
    return diverse_measurements


def parse_dtv_bands(value: str) -> set[DtvBand]:
    """Parse a comma-separated DTV band option."""

    bands: set[DtvBand] = set()
    for raw_part in value.split(","):
        part = raw_part.strip().lower()
        if not part:
            continue
        if part == "all":
            return {DtvBand.LOW_VHF, DtvBand.HIGH_VHF, DtvBand.UHF}
        if part in {"low", "low_vhf", "low-vhf", "vhf-low"}:
            bands.add(DtvBand.LOW_VHF)
        elif part in {"high", "high_vhf", "high-vhf", "vhf-high"}:
            bands.add(DtvBand.HIGH_VHF)
        elif part == "uhf":
            bands.add(DtvBand.UHF)
        else:
            raise ValueError(f"Unsupported DTV band {raw_part!r}")
    return bands or {DtvBand.UHF}


def dtv_band(rf_channel: int, center_frequency_mhz: float) -> DtvBand | None:
    """Return the DTV band for an RF channel/frequency pair."""

    if 2 <= rf_channel <= 6 or 54.0 <= center_frequency_mhz <= 88.0:
        return DtvBand.LOW_VHF
    if 7 <= rf_channel <= 13 or 174.0 <= center_frequency_mhz <= 216.0:
        return DtvBand.HIGH_VHF
    if 14 <= rf_channel <= 36 or 470.0 <= center_frequency_mhz <= 608.0:
        return DtvBand.UHF
    return None


def bistatic_measurement(
    *,
    emitter: DtvEmitter,
    receiver: ObserverConfig,
    position: PositionReport,
    velocity: VelocityReport | None,
    rcs_dbsm: float = DEFAULT_RCS_DBSM,
    polarization_loss_db: float = DEFAULT_POLARIZATION_LOSS_DB,
    system_loss_db: float = DEFAULT_SYSTEM_LOSS_DB,
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
    breakdown = bistatic_snr_breakdown(
        eirp_kw=emitter.eirp_kw,
        receiver_gain_dbi=receiver.receiver_gain_dbi,
        receiver_noise_figure_db=receiver.noise_figure_db,
        receiver_bandwidth_mhz=receiver.bandwidth_mhz,
        wavelength_m=wavelength_m,
        rcs_dbsm=rcs_dbsm,
        transmitter_target_range_m=tx_to_target_m,
        target_receiver_range_m=rx_to_target_m,
        polarization_loss_db=polarization_loss_db,
        system_loss_db=system_loss_db,
    )
    bistatic_range_rate_mps = None
    if velocity is not None:
        bistatic_range_rate_mps = position_velocity_to_range_rate_mps(
            position, velocity, transmitter
        ) + position_velocity_to_range_rate_mps(position, velocity, receiver)

    return BistaticMeasurement(
        emitter=emitter,
        snr_db=breakdown.snr_db,
        snr_breakdown=breakdown,
        bistatic_range_km=(tx_to_target_m + rx_to_target_m - tx_to_rx.range_m) / 1000.0,
        bistatic_doppler_hz=None
        if bistatic_range_rate_mps is None
        else -bistatic_range_rate_mps / wavelength_m,
        bearing_to_emitter_deg=tx_to_rx.azimuth_deg,
    )


def bistatic_snr_breakdown(
    *,
    eirp_kw: float,
    receiver_gain_dbi: float,
    receiver_noise_figure_db: float,
    receiver_bandwidth_mhz: float,
    wavelength_m: float,
    rcs_dbsm: float,
    transmitter_target_range_m: float,
    target_receiver_range_m: float,
    polarization_loss_db: float = DEFAULT_POLARIZATION_LOSS_DB,
    system_loss_db: float = DEFAULT_SYSTEM_LOSS_DB,
) -> BistaticSnrBreakdown:
    """Return the bistatic radar equation SNR, itemized term-by-term in dB."""

    eirp_dbw = 10.0 * log10(eirp_kw * 1000.0)
    wavelength_gain_db = 20.0 * log10(wavelength_m)
    spreading_loss_db = -30.0 * log10(4.0 * pi)
    tx_range_loss_db = -20.0 * log10(transmitter_target_range_m)
    rx_range_loss_db = -20.0 * log10(target_receiver_range_m)
    noise_floor_db = -(
        thermal_noise_power_dbw(receiver_bandwidth_mhz) + receiver_noise_figure_db
    )
    negative_polarization_loss_db = -polarization_loss_db
    negative_system_loss_db = -system_loss_db
    snr_db = (
        eirp_dbw
        + receiver_gain_dbi
        + wavelength_gain_db
        + rcs_dbsm
        + spreading_loss_db
        + tx_range_loss_db
        + rx_range_loss_db
        + noise_floor_db
        + negative_polarization_loss_db
        + negative_system_loss_db
    )
    return BistaticSnrBreakdown(
        eirp_dbw=eirp_dbw,
        receiver_gain_dbi=receiver_gain_dbi,
        wavelength_gain_db=wavelength_gain_db,
        rcs_dbsm=rcs_dbsm,
        spreading_loss_db=spreading_loss_db,
        tx_range_loss_db=tx_range_loss_db,
        rx_range_loss_db=rx_range_loss_db,
        noise_floor_db=noise_floor_db,
        polarization_loss_db=negative_polarization_loss_db,
        system_loss_db=negative_system_loss_db,
        snr_db=snr_db,
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
    polarization_loss_db: float = DEFAULT_POLARIZATION_LOSS_DB,
    system_loss_db: float = DEFAULT_SYSTEM_LOSS_DB,
) -> float:
    """Return bistatic radar equation SNR in dB."""

    return bistatic_snr_breakdown(
        eirp_kw=eirp_kw,
        receiver_gain_dbi=receiver_gain_dbi,
        receiver_noise_figure_db=receiver_noise_figure_db,
        receiver_bandwidth_mhz=receiver_bandwidth_mhz,
        wavelength_m=wavelength_m,
        rcs_dbsm=rcs_dbsm,
        transmitter_target_range_m=transmitter_target_range_m,
        target_receiver_range_m=target_receiver_range_m,
        polarization_loss_db=polarization_loss_db,
        system_loss_db=system_loss_db,
    ).snr_db


def thermal_noise_power_dbw(bandwidth_mhz: float) -> float:
    bandwidth_hz = max(bandwidth_mhz, 1e-12) * 1_000_000.0
    return 10.0 * log10(BOLTZMANN_J_PER_K * STANDARD_NOISE_TEMPERATURE_K * bandwidth_hz)
