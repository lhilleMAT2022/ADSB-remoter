"""Textual TUI for monitoring BaseStation streams."""

from __future__ import annotations

import argparse
import asyncio
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import DataTable, Footer, Header, Input, RichLog, Static

from adsb_console.config import load_observers_or_default, parse_endpoint
from adsb_console.models import ObserverConfig, TrackState
from adsb_console.tracker import BaseStationTracker, ObservedTrack

DEFAULT_SCREEN_REFRESH_HZ = 0.5
DEFAULT_MAX_DISPLAY_RANGE_KM = 200.0
DISPLAY_RANGE_STEP_KM = 25.0
DEFAULT_MAX_FILTERED_ROWS = 200


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
        ("a", "toggle_show_all", "Show all"),
        ("+", "increase_range", "More range"),
        ("=", "increase_range", "More range"),
        ("-", "decrease_range", "Less range"),
        ("f", "focus_filter", "Filter ICAO"),
        ("o", "next_observer", "Next observer"),
        ("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        source: tuple[str, int],
        *,
        observers: list[ObserverConfig] | None = None,
        selected_observer: str | None = None,
        refresh_rate_hz: float = DEFAULT_SCREEN_REFRESH_HZ,
        max_display_range_km: float = DEFAULT_MAX_DISPLAY_RANGE_KM,
        icao_filter: str = "",
    ) -> None:
        super().__init__()
        self.source = source
        self.observers = observers or load_observers_or_default(None)
        self.selected_observer_index = _selected_observer_index(self.observers, selected_observer)
        self.tracker = BaseStationTracker()
        self.refresh_interval_s = refresh_interval_s(refresh_rate_hz)
        self.max_display_range_km = max_display_range_km
        self.show_all_tracks = False
        self.icao_filter_text = icao_filter
        self.icao_filter = _compile_icao_filter(icao_filter)
        self.last_track_icao = ""
        self.last_filter_result = DisplayFilterResult.empty()
        self.table: DataTable[str] | None = None
        self.summary: Static | None = None
        self.filter_input: Input | None = None
        self.event_log: RichLog | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("Disconnected", id="summary")
        yield Input(
            value=self.icao_filter_text,
            placeholder="ICAO regex filter. Press f to focus, Enter to apply.",
            id="filter",
        )
        with Horizontal(id="main"):
            yield DataTable(id="tracks")
            yield RichLog(id="log", highlight=True, markup=True)
        yield Footer()

    async def on_mount(self) -> None:
        self.table = self.query_one("#tracks", DataTable)
        self.summary = self.query_one("#summary", Static)
        self.filter_input = self.query_one("#filter", Input)
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
        next_refresh_at = 0.0
        try:
            while True:
                line = await reader.readline()
                if not line:
                    self.last_filter_result = self._refresh_table()
                    self._update_summary(self._summary_text("Source closed"))
                    self._write_log("[yellow]Source closed connection[/]")
                    return
                decoded = line.decode(errors="replace").rstrip("\r\n")
                track = self.tracker.update_line(decoded)
                if track is not None:
                    self.last_track_icao = track.icao
                    now_monotonic = asyncio.get_running_loop().time()
                    if now_monotonic >= next_refresh_at:
                        self.last_filter_result = self._refresh_table()
                        self._update_summary(self._summary_text())
                        next_refresh_at = now_monotonic + self.refresh_interval_s
        finally:
            writer.close()
            await writer.wait_closed()

    def _refresh_table(self) -> DisplayFilterResult:
        if self.table is None:
            return DisplayFilterResult.empty()
        self.table.clear()
        now = datetime.now()
        observed_tracks = self.tracker.observed_tracks([self.selected_observer])
        filter_result = filter_display_tracks(
            observed_tracks=observed_tracks,
            icao_filter=self.icao_filter,
            max_range_km=None if self.show_all_tracks else self.max_display_range_km,
            max_rows=None if self.show_all_tracks else DEFAULT_MAX_FILTERED_ROWS,
        )
        for observed_track in filter_result.visible_tracks:
            track = self.tracker.tracks[observed_track.icao]
            self.table.add_row(*_track_row(observed_track, track, now))
        return filter_result

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
        self.last_filter_result = self._refresh_table()
        observer = self.selected_observer
        self._write_log(f"Selected observer: {observer.name} ({observer.role.value})")
        self._update_summary(self._summary_text())

    def action_toggle_show_all(self) -> None:
        self.show_all_tracks = not self.show_all_tracks
        self.last_filter_result = self._refresh_table()
        mode = (
            "all tracks"
            if self.show_all_tracks
            else f"{self.max_display_range_km:.0f} km max range"
        )
        self._write_log(f"Display mode: {mode}")
        self._update_summary(self._summary_text())

    def action_increase_range(self) -> None:
        self.show_all_tracks = False
        self.max_display_range_km += DISPLAY_RANGE_STEP_KM
        self.last_filter_result = self._refresh_table()
        self._update_summary(self._summary_text())

    def action_decrease_range(self) -> None:
        self.show_all_tracks = False
        self.max_display_range_km = max(
            DISPLAY_RANGE_STEP_KM, self.max_display_range_km - DISPLAY_RANGE_STEP_KM
        )
        self.last_filter_result = self._refresh_table()
        self._update_summary(self._summary_text())

    def action_focus_filter(self) -> None:
        if self.filter_input is not None:
            self.filter_input.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.icao_filter_text = event.value.strip()
        try:
            self.icao_filter = _compile_icao_filter(self.icao_filter_text)
        except re.error as exc:
            self._write_log(f"[red]Invalid ICAO regex:[/] {exc}")
            return
        self.last_filter_result = self._refresh_table()
        filter_text = self.icao_filter_text or "<none>"
        self._write_log(f"ICAO display filter: {filter_text}")
        self._update_summary(self._summary_text())

    def _summary_text(self, status: str | None = None) -> str:
        filter_result = self.last_filter_result
        display_mode = "all" if self.show_all_tracks else f"{self.max_display_range_km:.0f} km"
        filter_text = self.icao_filter_text or "none"
        prefix = f"{status} | " if status else ""
        return (
            f"{prefix}Messages: {self.tracker.message_count} | "
            f"Tracks: {len(self.tracker.tracks)} | "
            f"Observer: {self.selected_observer.name} | "
            f"Visible: {filter_result.visible_count}/{filter_result.observer_track_count} | "
            f"Hidden: {filter_result.hidden_count} | "
            f"Range: {display_mode} | "
            f"ICAO: {filter_text} | "
            f"Last: {self.last_track_icao or '-'}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Textual ADS-B BaseStation stream monitor.")
    parser.add_argument("--source", default="127.0.0.1:28887", type=parse_endpoint)
    parser.add_argument("--observerfile", default=None, type=Path)
    parser.add_argument("--observer", default=None)
    parser.add_argument("--refresh-rate", default=DEFAULT_SCREEN_REFRESH_HZ, type=float)
    parser.add_argument("--max-display-range-km", default=DEFAULT_MAX_DISPLAY_RANGE_KM, type=float)
    parser.add_argument("--icao-filter", default="")
    args = parser.parse_args()

    ADSBConsoleApp(
        source=args.source,
        observers=load_observers_or_default(args.observerfile),
        selected_observer=args.observer,
        refresh_rate_hz=args.refresh_rate,
        max_display_range_km=args.max_display_range_km,
        icao_filter=args.icao_filter,
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


@dataclass(frozen=True, slots=True)
class DisplayFilterResult:
    visible_tracks: list[ObservedTrack]
    observer_track_count: int
    hidden_count: int

    @property
    def visible_count(self) -> int:
        return len(self.visible_tracks)

    @classmethod
    def empty(cls) -> DisplayFilterResult:
        return cls(visible_tracks=[], observer_track_count=0, hidden_count=0)


def filter_display_tracks(
    *,
    observed_tracks: list[ObservedTrack],
    icao_filter: re.Pattern[str] | None,
    max_range_km: float | None,
    max_rows: int | None,
) -> DisplayFilterResult:
    filtered: list[ObservedTrack] = []
    for observed_track in observed_tracks:
        if max_range_km is not None and observed_track.range_az_el.range_m > max_range_km * 1000.0:
            continue
        if icao_filter is not None and icao_filter.search(observed_track.icao) is None:
            continue
        filtered.append(observed_track)

    visible_tracks = filtered if max_rows is None else filtered[:max_rows]
    hidden_count = len(observed_tracks) - len(visible_tracks)
    return DisplayFilterResult(
        visible_tracks=visible_tracks,
        observer_track_count=len(observed_tracks),
        hidden_count=hidden_count,
    )


def _compile_icao_filter(pattern: str) -> re.Pattern[str] | None:
    if not pattern:
        return None
    return re.compile(pattern, re.IGNORECASE)


def refresh_interval_s(refresh_rate_hz: float) -> float:
    if refresh_rate_hz <= 0.0:
        return 0.0
    return 1.0 / refresh_rate_hz


if __name__ == "__main__":
    main()
