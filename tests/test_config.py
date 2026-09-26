from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from adsb_console.config import (
    default_observers,
    ensure_local_observer,
    load_observers,
    load_observers_or_default,
    parse_endpoint,
    parse_endpoint_list,
)
from adsb_console.cue_config import CuePublicationMode, load_cue_runtime_config
from adsb_console.models import ObserverConfig, ObserverRole


def test_parse_endpoint() -> None:
    assert parse_endpoint("127.0.0.1:28887") == ("127.0.0.1", 28887)
    assert parse_endpoint_list("127.0.0.1:1,localhost:2") == [
        ("127.0.0.1", 1),
        ("localhost", 2),
    ]


def test_load_observers() -> None:
    observers = load_observers("tests/fixtures/observers.ini")

    assert len(observers) == 2
    assert observers[0].name == "FLEXDAR"
    assert observers[0].role is ObserverRole.LOCAL
    assert observers[0].latitude_deg == 41.576006
    assert observers[0].max_range_m == 40000.0
    assert observers[0].seek_pattern == "A.*"
    assert observers[1].name == "REMOTE_NORTH"
    assert observers[1].role is ObserverRole.REMOTE
    assert observers[1].min_azimuth_deg == 0.0
    assert observers[1].max_azimuth_deg == 180.0
    assert observers[1].report_endpoint == "127.0.0.1:63542"


def test_ensure_local_observer_adds_local_from_first_remote() -> None:
    remote = ObserverConfig(
        name="Remote",
        role=ObserverRole.REMOTE,
        latitude_deg=1.0,
        longitude_deg=2.0,
        altitude_m=3.0,
    )

    observers = ensure_local_observer([remote])

    assert len(observers) == 2
    assert observers[0].is_local
    assert observers[0].latitude_deg == 1.0
    assert observers[1] is remote


def test_default_observers_are_deployment_observers() -> None:
    observers = default_observers()

    assert len(observers) == 2
    assert observers[0].name == "MathWorks Apple Hill Parking Lot"
    assert observers[0].role is ObserverRole.LOCAL
    assert observers[0].latitude_deg == 42.299350798761694
    assert observers[0].longitude_deg == -71.34948267330608
    assert observers[0].altitude_m == 75.0
    assert observers[0].receiver_gain_dbi == 10.0
    assert observers[0].noise_figure_db == 3.0
    assert observers[0].bandwidth_mhz == 8.0
    assert observers[1].name == "CBS Broadcast Tower"
    assert observers[1].role is ObserverRole.REMOTE
    assert observers[1].latitude_deg == 42.308259720042415
    assert observers[1].longitude_deg == -71.21565924108836
    assert observers[1].altitude_m == 350.0
    assert load_observers_or_default(None) == observers


def test_cue_configuration_loads_manual_publication_mode() -> None:
    config = load_cue_runtime_config("tests/fixtures/cue-manual.json")

    assert config.prediction.enabled
    assert config.udp_output.enabled
    assert config.publication_mode is CuePublicationMode.MANUAL


def test_cue_configuration_defaults_and_validates_opportunity_limit(tmp_path: Path) -> None:
    (tmp_path / "schemas").mkdir()
    shutil.copy("schemas/cue-config-2.0.0.json", tmp_path / "schemas")
    (tmp_path / "configs").mkdir()
    config_path = tmp_path / "configs" / "cue.json"

    config_path.write_text(json.dumps({"udp_output": {"enabled": True}}))
    defaults = load_cue_runtime_config(config_path)
    assert defaults.udp_output.maximum_opportunities_per_cue == 8
    assert defaults.udp_output.maximum_datagram_bytes == 1472
    assert defaults.udp_output.encoding == "json"
    assert defaults.include_summary is False

    config_path.write_text(
        json.dumps({"udp_output": {"enabled": True, "maximum_opportunities_per_cue": 5}})
    )
    assert load_cue_runtime_config(config_path).udp_output.maximum_opportunities_per_cue == 5

    config_path.write_text(
        json.dumps({"udp_output": {"enabled": True, "maximum_opportunities_per_cue": 0}})
    )
    with pytest.raises(ValueError, match="Invalid cue configuration"):
        load_cue_runtime_config(config_path)


def test_example_cue_configurations_validate() -> None:
    example = load_cue_runtime_config("examples/passive-radar-cueing.json")
    deployed = load_cue_runtime_config("deploy/pi-cue-config.json")

    assert example.udp_output.encoding == "json"
    assert deployed.udp_output.encoding == "deflate_dictionary"
    for config in (example, deployed):
        assert config.udp_output.maximum_opportunities_per_cue == 8
        assert config.udp_output.maximum_datagram_bytes == 1472
        assert config.include_summary is False


def test_cue_configuration_rejects_the_retired_oversize_policy(tmp_path: Path) -> None:
    (tmp_path / "schemas").mkdir()
    shutil.copy("schemas/cue-config-2.0.0.json", tmp_path / "schemas")
    (tmp_path / "configs").mkdir()
    config_path = tmp_path / "configs" / "cue.json"
    config_path.write_text(json.dumps({"udp_output": {"oversize_policy": "omit_history"}}))

    with pytest.raises(ValueError, match="Invalid cue configuration"):
        load_cue_runtime_config(config_path)


def test_pi_deployment_observers_are_the_receive_site_only() -> None:
    observers = load_observers_or_default(Path("deploy/pi-observers.ini"))

    assert [observer.name for observer in observers] == ["Apple Hill Receive Site"]
    assert observers[0].role is ObserverRole.LOCAL
    assert observers[0].receiver_gain_dbi == 10.0
    assert (observers[0].noise_figure_db, observers[0].bandwidth_mhz) == (3.0, 8.0)
