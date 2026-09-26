from __future__ import annotations

import asyncio
import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft202012Validator, FormatChecker

from adsb_console.bistatic import DtvEmitter
from adsb_console.cue import CueSerializer
from adsb_console.models import ObserverConfig, ObserverRole
from adsb_console.playback import PlaybackConfig, replay_messages
from adsb_console.prediction import (
    PredictionConfig,
    PredictionRevisionManager,
    PredictionUpdateReason,
    build_track_prediction,
    observer_id,
)
from adsb_console.tracker import BaseStationTracker


def test_startup_replay_partition_produces_schema_valid_cue() -> None:
    """Exercise partition A through the existing asynchronous SBS playback source."""

    root = Path(
        os.environ.get(
            "ADSB_REPLAY_CORPUS_DIR",
            Path(__file__).parents[2] / "000_sbs_for_ELAD_cleanup" / "sbs_clean",
        )
    )
    source = root / "10_adsb_20220413_060850.csv.gz"
    if not source.exists():
        return

    track = asyncio.run(_first_eligible_track(source))
    assert track is not None
    observer = ObserverConfig(
        name="replay_observer",
        role=ObserverRole.LOCAL,
        latitude_deg=42.299350798761694,
        longitude_deg=-71.34948267330608,
        altitude_m=75.0,
        receiver_gain_dbi=10.0,
    )
    emitter = DtvEmitter(
        facility_id="replay",
        call_sign="WTEST",
        site_name="Replay Tower",
        asrn="replay",
        rf_channel=20,
        center_frequency_mhz=509.0,
        latitude_deg=42.308259720042415,
        longitude_deg=-71.21565924108836,
        altitude_m=350.0,
        eirp_kw=1_000.0,
    )
    second_observer = replace(
        observer,
        name="replay_observer_two",
        latitude_deg=42.292,
        longitude_deg=-71.325,
    )
    emitters = (
        emitter,
        replace(
            emitter,
            facility_id="replay-two",
            call_sign="WTEST2",
            rf_channel=21,
            center_frequency_mhz=515.0,
        ),
        replace(
            emitter,
            facility_id="replay-three",
            call_sign="WTEST3",
            rf_channel=22,
            center_frequency_mhz=521.0,
        ),
    )
    prediction = build_track_prediction(
        track=track,
        reference_origin=observer,
        observers=[observer, second_observer],
        emitters=emitters,
        config=PredictionConfig(
            enabled=True,
            prediction_horizon_s=30.0,
            prediction_sample_interval_s=10.0,
            minimum_track_history_s=0.0,
            detection_threshold_db=-100.0,
            receiver_compatible_emitter_ids={
                observer_id(second_observer): frozenset(
                    {emitters[0].emitter_id, emitters[1].emitter_id}
                )
            },
        ),
        token=PredictionRevisionManager().request(f"adsb:{track.icao}"),
        created_utc=track.last_seen,
        update_reason=PredictionUpdateReason.INITIAL_TRACK,
    )
    payload = CueSerializer().track_cue(
        prediction,
        source_instance_id="replay-test",
        sequence_number=1,
        message_id="123e4567-e89b-12d3-a456-426614174000",
        generated_utc=track.last_seen,
    )
    schema_path = Path(__file__).parents[1] / "schemas" / "track-cue-2.0.0.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert len(prediction.opportunities) == 6
    opportunities = payload["opportunities"]
    assert isinstance(opportunities, list)
    assert len(cast(list[object], opportunities)) == 5
    validator: Any = Draft202012Validator(schema, format_checker=FormatChecker())
    assert not list(validator.iter_errors(payload))


async def _first_eligible_track(source: Path):
    tracker = BaseStationTracker(stale_track_seconds=None)
    config = PlaybackConfig(
        files=(source,),
        max_lines=20_000,
        preserve_timing=False,
        rebase_timestamps=False,
    )
    async for message in replay_messages(config):
        track = tracker.update(message)
        if (
            track is not None
            and track.last_position is not None
            and track.last_velocity is not None
        ):
            return track
    return None
