from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from adsb_console.playback import PlaybackConfig, replay_messages


@pytest.mark.asyncio
async def test_replay_messages_from_csv_fixture() -> None:
    config = PlaybackConfig(
        files=(Path("tests/fixtures/basestation_sample.csv"),),
        max_lines=2,
        preserve_timing=False,
        rebase_timestamps=False,
    )

    messages = [message async for message in replay_messages(config)]

    assert [message.icao for message in messages] == ["A5CDE9", "A5CDE9"]


@pytest.mark.asyncio
async def test_replay_messages_from_gzip_file() -> None:
    compressed = _gzip_fixture()
    config = PlaybackConfig(
        files=(compressed,),
        max_lines=1,
        preserve_timing=False,
        rebase_timestamps=False,
    )

    messages = [message async for message in replay_messages(config)]

    assert len(messages) == 1
    assert messages[0].icao == "A5CDE9"


def _gzip_fixture() -> Path:
    source = Path("tests/fixtures/basestation_sample.csv")
    scratch = Path("tests/.scratch")
    scratch.mkdir(exist_ok=True)
    compressed = scratch / "basestation_sample.csv.gz"
    compressed.write_bytes(gzip.compress(source.read_bytes()))
    return compressed
