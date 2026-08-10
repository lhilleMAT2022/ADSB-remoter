from __future__ import annotations

from datetime import datetime
from math import isclose

from adsb_console.bistatic import (
    DtvEmitter,
    bistatic_measurement,
    load_dtv_emitters,
    top_bistatic_measurements,
)
from adsb_console.models import ObserverConfig, ObserverRole, PositionReport, VelocityReport


def test_load_dtv_emitters_from_project_file() -> None:
    emitters = load_dtv_emitters("20_DTV_direct_path_input.csv")

    assert emitters
    assert emitters[0].call_sign == "WGBH-TV"
    assert emitters[0].center_frequency_mhz == 79.0


def test_bistatic_measurement_has_snr_range_doppler_and_bearing() -> None:
    now = datetime.now()
    emitter = DtvEmitter(
        call_sign="WBZ-TV",
        site_name="CBS Tower",
        rf_channel=20,
        center_frequency_mhz=509.0,
        latitude_deg=0.0,
        longitude_deg=0.02,
        altitude_m=100.0,
        eirp_kw=1000.0,
    )
    receiver = ObserverConfig(
        name="receiver",
        role=ObserverRole.LOCAL,
        latitude_deg=0.0,
        longitude_deg=0.0,
        altitude_m=0.0,
        receiver_gain_dbi=30.0,
        noise_figure_db=3.0,
        bandwidth_mhz=8.0,
    )
    position = PositionReport(
        latitude_deg=0.0,
        longitude_deg=0.01,
        altitude_ft=10_000.0,
        reported_at=now,
    )
    velocity = VelocityReport(
        ground_speed_kt=100.0,
        track_deg=90.0,
        vertical_rate_fpm=0.0,
        reported_at=now,
    )

    measurement = bistatic_measurement(
        emitter=emitter,
        receiver=receiver,
        position=position,
        velocity=velocity,
    )

    assert measurement is not None
    assert isclose(measurement.bistatic_range_km, 4.1, rel_tol=0.1)
    assert measurement.snr_db > 0.0
    assert measurement.bistatic_doppler_hz is not None
    assert isclose(measurement.bearing_to_emitter_deg, 90.0, abs_tol=0.1)


def test_top_bistatic_measurements_sorts_by_snr() -> None:
    now = datetime.now()
    receiver = ObserverConfig(
        name="receiver",
        role=ObserverRole.LOCAL,
        latitude_deg=0.0,
        longitude_deg=0.0,
        altitude_m=0.0,
        receiver_gain_dbi=30.0,
    )
    position = PositionReport(
        latitude_deg=0.0,
        longitude_deg=0.01,
        altitude_ft=10_000.0,
        reported_at=now,
    )
    weak = DtvEmitter(
        call_sign="WEAK",
        site_name="Weak",
        rf_channel=1,
        center_frequency_mhz=500.0,
        latitude_deg=0.0,
        longitude_deg=0.02,
        altitude_m=0.0,
        eirp_kw=1.0,
    )
    strong = DtvEmitter(
        call_sign="STRONG",
        site_name="Strong",
        rf_channel=2,
        center_frequency_mhz=500.0,
        latitude_deg=0.0,
        longitude_deg=0.02,
        altitude_m=0.0,
        eirp_kw=1000.0,
    )

    results = top_bistatic_measurements(
        emitters=[weak, strong],
        receiver=receiver,
        position=position,
        velocity=None,
        count=1,
    )

    assert [item.emitter.call_sign for item in results] == ["STRONG"]
