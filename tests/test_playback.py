from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from adsb_console.playback import PlaybackConfig, format_peer, format_status_line, replay_messages


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


@pytest.mark.asyncio
async def test_replay_messages_from_sbs_clean_gzip_fixture() -> None:
    gzip_files = _sbs_clean_gzip_fixtures()
    assert gzip_files
    config = PlaybackConfig(
        files=(gzip_files[0],),
        max_lines=100,
        preserve_timing=False,
        rebase_timestamps=False,
    )

    messages = [message async for message in replay_messages(config)]

    assert len(messages) == 100
    assert {message.icao for message in messages}


def _gzip_fixture() -> Path:
    source = Path("tests/fixtures/basestation_sample.csv")
    scratch = Path("tests/.scratch")
    scratch.mkdir(exist_ok=True)
    compressed = scratch / "basestation_sample.csv.gz"
    compressed.write_bytes(gzip.compress(source.read_bytes()))
    return compressed


def _sbs_clean_gzip_fixtures() -> tuple[Path, ...]:
    return tuple(sorted(Path("tests/fixtures/sbs_clean").glob("*.gz")))


def test_status_helpers() -> None:
    assert format_peer(("127.0.0.1", 12345)) == "127.0.0.1:12345"
    assert "2 unique tracks sent, 3 total messages" in format_status_line({"A", "B"}, 3)
