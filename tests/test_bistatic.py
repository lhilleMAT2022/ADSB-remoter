from __future__ import annotations

from datetime import datetime
from math import isclose

from adsb_console.bistatic import (
    DtvBand,
    DtvEmitter,
    bistatic_measurement,
    load_dtv_emitters,
    parse_dtv_bands,
    top_bistatic_measurements,
)
from adsb_console.models import ObserverConfig, ObserverRole, PositionReport, VelocityReport


def test_load_dtv_emitters_from_project_file() -> None:
    emitters = load_dtv_emitters("20_DTV_direct_path_input.csv")

    assert emitters
    assert emitters[0].call_sign == "WGBH-TV"
    assert emitters[0].center_frequency_mhz == 79.0


def test_load_dtv_emitters_can_filter_to_uhf_default_band() -> None:
    emitters = load_dtv_emitters("20_DTV_direct_path_input.csv", parse_dtv_bands("uhf"))

    assert emitters
    assert {emitter.band for emitter in emitters} == {DtvBand.UHF}


def test_bistatic_measurement_has_snr_range_doppler_and_bearing() -> None:
    now = datetime.now()
    emitter = DtvEmitter(
        facility_id="1",
        call_sign="WBZ-TV",
        site_name="CBS Tower",
        asrn="100",
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
        facility_id="1",
        call_sign="WEAK",
        site_name="Weak",
        asrn="100",
        rf_channel=1,
        center_frequency_mhz=500.0,
        latitude_deg=0.0,
        longitude_deg=0.02,
        altitude_m=0.0,
        eirp_kw=1.0,
    )
    strong = DtvEmitter(
        facility_id="2",
        call_sign="STRONG",
        site_name="Strong",
        asrn="200",
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


def test_top_bistatic_measurements_keeps_one_emitter_per_tower() -> None:
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
    first = DtvEmitter(
        facility_id="1",
        call_sign="FIRST",
        site_name="Shared",
        asrn="100",
        rf_channel=20,
        center_frequency_mhz=500.0,
        latitude_deg=0.0,
        longitude_deg=0.02,
        altitude_m=0.0,
        eirp_kw=1000.0,
    )
    second_same_tower = DtvEmitter(
        facility_id="2",
        call_sign="SECOND",
        site_name="Shared",
        asrn="100",
        rf_channel=21,
        center_frequency_mhz=510.0,
        latitude_deg=0.0,
        longitude_deg=0.02,
        altitude_m=0.0,
        eirp_kw=900.0,
    )

    results = top_bistatic_measurements(
        emitters=[first, second_same_tower],
        receiver=receiver,
        position=position,
        velocity=None,
        count=2,
    )

    assert len(results) == 1
