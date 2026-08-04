"""Receive BaseStation TCP streams and retransmit them over UDP."""

from __future__ import annotations

import argparse
import asyncio
import socket
from dataclasses import dataclass

from adsb_console.config import Endpoint, parse_endpoint, parse_endpoint_list
from adsb_console.tracker import BaseStationTracker


@dataclass(frozen=True, slots=True)
class RelayConfig:
    source: Endpoint = ("127.0.0.1", 28887)
    destinations: tuple[Endpoint, ...] = (("127.0.0.1", 63542),)
    timeout_s: float = 10.0


async def relay_stream(config: RelayConfig, tracker: BaseStationTracker | None = None) -> None:
    tracker = tracker or BaseStationTracker()
    reader, writer = await asyncio.open_connection(*config.source)
    udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=config.timeout_s)
            if not line:
                return

            decoded = line.decode(errors="replace").rstrip("\r\n")
            tracker.update_line(decoded)
            payload = decoded.encode()
            for destination in config.destinations:
                udp_socket.sendto(payload, destination)
    finally:
        udp_socket.close()
        writer.close()
        await writer.wait_closed()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Relay BaseStation TCP messages to UDP destinations."
    )
    parser.add_argument("--source", default="127.0.0.1:28887", type=parse_endpoint)
    parser.add_argument(
        "--dest",
        default="127.0.0.1:63542",
        type=parse_endpoint_list,
        help="Comma-separated UDP destination list: HOST:PORT[,HOST:PORT]",
    )
    parser.add_argument("--timeout", default=10.0, type=float)
    args = parser.parse_args()

    config = RelayConfig(
        source=args.source,
        destinations=tuple(args.dest),
        timeout_s=args.timeout,
    )
    asyncio.run(relay_stream(config))


if __name__ == "__main__":
    main()
