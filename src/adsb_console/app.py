"""Textual TUI for monitoring BaseStation streams."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
from pathlib import Path
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import DataTable, Footer, Header, RichLog, Static

from adsb_console.config import load_observers_or_default, parse_endpoint
from adsb_console.models import ObserverConfig, TrackState
from adsb_console.tracker import BaseStationTracker, ObservedTrack


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
        ("o", "next_observer", "Next observer"),
        ("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        source: tuple[str, int],
        observers: list[ObserverConfig] | None = None,
        selected_observer: str | None = None,
    ) -> None:
        super().__init__()
        self.source = source
        self.observers = observers or load_observers_or_default(None)
        self.selected_observer_index = _selected_observer_index(self.observers, selected_observer)
        self.tracker = BaseStationTracker()
        self.table: DataTable[str] | None = None
        self.summary: Static | None = None
        self.event_log: RichLog | None = None

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
        self.event_log = self.query_one("#log", RichLog)
        self.table.add_columns(
            "ICAO", "Callsign", "Msgs", "Alt ft", "Rng km", "Az deg", "El deg", "GS kt", "Age s"
        )
        self._write_log(f"Loaded {len(self.observers)} observer(s)")
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
                        f"Observer: {self.selected_observer.name} | "
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
        for observed_track in self.tracker.observed_tracks([self.selected_observer])[:200]:
            track = self.tracker.tracks[observed_track.icao]
            self.table.add_row(*_track_row(observed_track, track, now))

    def _write_log(self, message: str) -> None:
        if self.event_log is not None:
            self.event_log.write(message)

    def _update_summary(self, message: str) -> None:
        if self.summary is not None:
            self.summary.update(message)

    @property
    def selected_observer(self) -> ObserverConfig:
        return self.observers[self.selected_observer_index]

    def action_next_observer(self) -> None:
        self.selected_observer_index = (self.selected_observer_index + 1) % len(self.observers)
        self._refresh_table()
        observer = self.selected_observer
        self._write_log(f"Selected observer: {observer.name} ({observer.role.value})")
        self._update_summary(
            f"Messages: {self.tracker.message_count} | "
            f"Tracks: {len(self.tracker.tracks)} | "
            f"Observer: {observer.name}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Textual ADS-B BaseStation stream monitor.")
    parser.add_argument("--source", default="127.0.0.1:28887", type=parse_endpoint)
    parser.add_argument("--observerfile", default=None, type=Path)
    parser.add_argument("--observer", default=None)
    args = parser.parse_args()

    ADSBConsoleApp(
        source=args.source,
        observers=load_observers_or_default(args.observerfile),
        selected_observer=args.observer,
    ).run()


def _track_row(
    observed_track: ObservedTrack, track: TrackState, now: datetime
) -> tuple[str, str, str, str, str, str, str, str, str]:
    position = track.last_position
    velocity = track.last_velocity
    age_s = (now - track.last_seen).total_seconds()
    range_az_el = observed_track.range_az_el
    return (
        track.icao,
        track.callsign or "",
        str(track.message_count),
        "" if position is None else f"{position.altitude_ft:.0f}",
        f"{range_az_el.range_m / 1000.0:.1f}",
        f"{range_az_el.azimuth_deg:.1f}",
        f"{range_az_el.elevation_deg:.1f}",
        "" if velocity is None else f"{velocity.ground_speed_kt:.0f}",
        f"{age_s:.1f}",
    )


def _selected_observer_index(observers: list[ObserverConfig], selected: str | None) -> int:
    if not observers:
        raise ValueError("At least one observer is required")
    if selected is not None:
        for index, observer in enumerate(observers):
            if observer.name == selected:
                return index
    for index, observer in enumerate(observers):
        if observer.is_local:
            return index
    return 0


if __name__ == "__main__":
    main()
