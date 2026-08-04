"""Playback recorded BaseStation files as live TCP streams."""

from __future__ import annotations

import argparse
import asyncio
import gzip
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TextIO

from adsb_console.config import parse_endpoint
from adsb_console.models import BaseStationMessage


@dataclass(frozen=True, slots=True)
class PlaybackConfig:
    files: tuple[Path, ...]
    bind_host: str = "127.0.0.1"
    bind_port: int = 28887
    max_lines: int | None = None
    nth: int = 1
    rate: float = 1.0
    preserve_timing: bool = True
    rebase_timestamps: bool = True


async def replay_messages(config: PlaybackConfig) -> AsyncIterator[BaseStationMessage]:
    first_recorded_at: datetime | None = None
    first_wall_time = asyncio.get_running_loop().time()
    emitted = 0
    seen = 0

    for line in _iter_lines(config.files):
        seen += 1
        if config.nth > 1 and (seen - 1) % config.nth != 0:
            continue

        try:
            message = BaseStationMessage.parse(line)
        except ValueError:
            continue

        recorded_at = message.generated_at
        if config.preserve_timing and recorded_at is not None:
            if first_recorded_at is None:
                first_recorded_at = recorded_at
            delay = (recorded_at - first_recorded_at).total_seconds() / config.rate
            sleep_for = first_wall_time + delay - asyncio.get_running_loop().time()
            if sleep_for > 0.0:
                await asyncio.sleep(sleep_for)

        emitted += 1
        if config.rebase_timestamps:
            message = message.with_rebased_timestamps(datetime.now())
        yield message

        if config.max_lines is not None and emitted >= config.max_lines:
            return


async def serve_playback(config: PlaybackConfig) -> None:
    async def handle_client(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            async for message in replay_messages(config):
                writer.write(f"{message.to_csv_line()}\n".encode())
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handle_client, config.bind_host, config.bind_port)
    async with server:
        await server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay BaseStation CSV/TXT/GZ files over TCP.")
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--bind", default="127.0.0.1:28887", type=parse_endpoint)
    parser.add_argument("--max-lines", default=None, type=int)
    parser.add_argument("--nth", default=1, type=int)
    parser.add_argument("--rate", default=1.0, type=float)
    parser.add_argument("--no-preserve-timing", action="store_true")
    parser.add_argument("--no-rebase-timestamps", action="store_true")
    args = parser.parse_args()

    host, port = args.bind
    config = PlaybackConfig(
        files=tuple(args.files),
        bind_host=host,
        bind_port=port,
        max_lines=args.max_lines,
        nth=args.nth,
        rate=args.rate,
        preserve_timing=not args.no_preserve_timing,
        rebase_timestamps=not args.no_rebase_timestamps,
    )
    asyncio.run(serve_playback(config))


def _iter_lines(files: tuple[Path, ...]) -> Iterator[str]:
    for path in files:
        with _open_text(path) as stream:
            for line in stream:
                if line.strip():
                    yield line


def _open_text(path: Path) -> TextIO:
    if path.suffix.lower() == ".gz":
        return gzip.open(path, "rt", newline="")
    return path.open("rt", newline="")


if __name__ == "__main__":
    main()
