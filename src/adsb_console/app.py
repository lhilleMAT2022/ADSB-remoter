"""Textual TUI for monitoring BaseStation streams."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import DataTable, Footer, Header, RichLog, Static

from adsb_console.config import parse_endpoint
from adsb_console.models import TrackState
from adsb_console.tracker import BaseStationTracker


class ADSBConsoleApp(App[None]):
    """Minimal Textual monitor for BaseStation TCP streams."""

    CSS = """
    #summary {
        height: 3;
        padding: 0 1;
    }

    #main {
        height: 1fr;
    }

    #tracks {
        width: 2fr;
    }

    #log {
        width: 1fr;
    }
    """

    BINDINGS: ClassVar = [
        ("q", "quit", "Quit"),
    ]

    def __init__(self, source: tuple[str, int]) -> None:
        super().__init__()
        self.source = source
        self.tracker = BaseStationTracker()
        self.table: DataTable[str] | None = None
        self.summary: Static | None = None
        self.log: RichLog | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("Disconnected", id="summary")
        with Horizontal(id="main"):
            yield DataTable(id="tracks")
            yield RichLog(id="log", highlight=True, markup=True)
        yield Footer()

    async def on_mount(self) -> None:
        self.table = self.query_one("#tracks", DataTable)
        self.summary = self.query_one("#summary", Static)
        self.log = self.query_one("#log", RichLog)
        self.table.add_columns("ICAO", "Callsign", "Msgs", "Alt ft", "Lat", "Lon", "GS kt", "Age s")
        self.run_worker(self._monitor_source(), name="source-monitor", exclusive=True)

    async def _monitor_source(self) -> None:
        self._write_log(f"Connecting to {self.source[0]}:{self.source[1]}")
        try:
            reader, writer = await asyncio.open_connection(*self.source)
        except OSError as exc:
            self._write_log(f"[red]Connection failed:[/] {exc}")
            self._update_summary("Connection failed")
            return

        self._update_summary(f"Connected to {self.source[0]}:{self.source[1]}")
        try:
            while True:
                line = await reader.readline()
                if not line:
                    self._write_log("[yellow]Source closed connection[/]")
                    return
                decoded = line.decode(errors="replace").rstrip("\r\n")
                track = self.tracker.update_line(decoded)
                if track is not None:
                    self._refresh_table()
                    self._update_summary(
                        f"Messages: {self.tracker.message_count} | "
                        f"Tracks: {len(self.tracker.tracks)} | "
                        f"Last: {track.icao}"
                    )
        finally:
            writer.close()
            await writer.wait_closed()

    def _refresh_table(self) -> None:
        if self.table is None:
            return
        self.table.clear()
        now = datetime.now()
        for track in self.tracker.active_tracks()[:200]:
            self.table.add_row(*_track_row(track, now))

    def _write_log(self, message: str) -> None:
        if self.log is not None:
            self.log.write(message)

    def _update_summary(self, message: str) -> None:
        if self.summary is not None:
            self.summary.update(message)


def main() -> None:
    parser = argparse.ArgumentParser(description="Textual ADS-B BaseStation stream monitor.")
    parser.add_argument("--source", default="127.0.0.1:28887", type=parse_endpoint)
    args = parser.parse_args()

    ADSBConsoleApp(source=args.source).run()


def _track_row(track: TrackState, now: datetime) -> tuple[str, str, str, str, str, str, str, str]:
    position = track.last_position
    velocity = track.last_velocity
    age_s = (now - track.last_seen).total_seconds()
    return (
        track.icao,
        track.callsign or "",
        str(track.message_count),
        "" if position is None else f"{position.altitude_ft:.0f}",
        "" if position is None else f"{position.latitude_deg:.5f}",
        "" if position is None else f"{position.longitude_deg:.5f}",
        "" if velocity is None else f"{velocity.ground_speed_kt:.0f}",
        f"{age_s:.1f}",
    )


if __name__ == "__main__":
    main()
