"""Playback recorded BaseStation files as live TCP streams."""

from __future__ import annotations

import argparse
import asyncio
import gzip
from collections.abc import AsyncIterator, Iterator
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TextIO, cast

from adsb_console.config import parse_endpoint
from adsb_console.models import BaseStationMessage, utc_now


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
    status_interval_s: float = 10.0


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
            message = message.with_rebased_timestamps(utc_now())
        yield message

        if config.max_lines is not None and emitted >= config.max_lines:
            return


async def serve_playback(config: PlaybackConfig) -> None:
    async def handle_client(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = format_peer(writer.get_extra_info("peername"))
        print(f"Time: {_clock()} - client connected from IP address: {peer}", flush=True)
        total_messages = 0
        unique_tracks: set[str] = set()
        next_status_at = asyncio.get_running_loop().time() + config.status_interval_s
        disconnect_reported = False
        try:
            async for message in replay_messages(config):
                writer.write(f"{message.to_csv_line()}\n".encode())
                try:
                    await writer.drain()
                except (BrokenPipeError, ConnectionError):
                    print(
                        f"Time: {_clock()} - client disconnected from IP address: {peer}; "
                        f"{len(unique_tracks)} unique tracks sent, {total_messages} total messages",
                        flush=True,
                    )
                    disconnect_reported = True
                    return
                total_messages += 1
                if message.icao:
                    unique_tracks.add(message.icao)
                if asyncio.get_running_loop().time() >= next_status_at:
                    print(format_status_line(unique_tracks, total_messages), flush=True)
                    next_status_at += config.status_interval_s
        finally:
            writer.close()
            with suppress(BrokenPipeError, ConnectionError):
                await writer.wait_closed()
            if not disconnect_reported:
                print(
                    f"Time: {_clock()} - client disconnected from IP address: {peer}; "
                    f"{len(unique_tracks)} unique tracks sent, {total_messages} total messages",
                    flush=True,
                )

    server = await asyncio.start_server(handle_client, config.bind_host, config.bind_port)
    print(
        f"Time: {_clock()} - serving playback on {config.bind_host}:{config.bind_port}",
        flush=True,
    )
    async with server:
        await server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay BaseStation CSV/TXT/GZ files over TCP.")
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--bind", default="127.0.0.1:28887", type=parse_endpoint)
    parser.add_argument("--max-lines", default=None, type=int)
    parser.add_argument("--nth", default=1, type=int)
    parser.add_argument("--rate", default=1.0, type=float)
    parser.add_argument("--status-interval", default=10.0, type=float)
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
        status_interval_s=args.status_interval,
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


def _clock() -> str:
    return datetime.now().strftime("%H:%M:%S")


def format_status_line(unique_tracks: set[str], total_messages: int) -> str:
    return (
        f"Time: {_clock()} - {len(unique_tracks)} unique tracks sent, "
        f"{total_messages} total messages"
    )


def format_peer(peer: object) -> str:
    if peer is None:
        return "unknown"
    if not isinstance(peer, tuple):
        return str(peer)
    peer_tuple = cast(tuple[object, ...], peer)
    if not peer_tuple:
        return "unknown"
    host = str(peer_tuple[0])
    port = f":{peer_tuple[1]}" if len(peer_tuple) > 1 else ""
    return f"{host}{port}"


if __name__ == "__main__":
    main()
