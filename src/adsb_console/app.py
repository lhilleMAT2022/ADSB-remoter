"""Textual TUI for monitoring BaseStation streams."""

from __future__ import annotations

import argparse
import asyncio
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from pathlib import Path
from typing import ClassVar

from prettytable import PrettyTable
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, Input, RichLog, Static

from adsb_console.bistatic import (
    BistaticMeasurement,
    DtvEmitter,
    load_dtv_emitters,
    parse_dtv_bands,
    top_bistatic_measurements,
)
from adsb_console.config import load_observers_or_default, parse_endpoint
from adsb_console.models import ObserverConfig, PositionReport, TrackState, VelocityReport, utc_now
from adsb_console.tracker import (
    DEFAULT_STALE_TRACK_SECONDS,
    BaseStationTracker,
    ObservedTrack,
)

DEFAULT_SCREEN_REFRESH_SECONDS = 10.0
DEFAULT_SCREEN_REFRESH_HZ = 1.0 / DEFAULT_SCREEN_REFRESH_SECONDS
SCREEN_REFRESH_SECONDS_CHOICES = (2.0, 5.0, 10.0, 30.0)
DEFAULT_MAX_DISPLAY_RANGE_KM = 200.0
DISPLAY_RANGE_STEP_KM = 25.0
DEFAULT_MAX_FILTERED_ROWS = 200
DEFAULT_AGE_OUT_SECONDS = 20.0
DEFAULT_CARRIER_FREQUENCY_MHZ = 600.0
DEFAULT_LOG_MAX_LINES = 1_000
DEFAULT_TRACK_RETENTION_MINUTES = DEFAULT_STALE_TRACK_SECONDS / 60.0
DEFAULT_DTV_FILE = Path("20_DTV_direct_path_input.csv")
DEFAULT_DTV_BANDS = "uhf"
DEFAULT_BISTATIC_DISPLAY_TRACKS = 10
DEFAULT_BISNR_BREAKDOWN_REFRESH_SECONDS = 10.0
OBSERVER_TABLE = "observer"
TRACK_FOCUS_TABLE = "track-focus"
SORT_COLUMNS = (
    "ICAO",
    "Callsign",
    "Msgs",
    "Alt ft",
    "Rng km",
    "Az deg",
    "El deg",
    "RR m/s",
    "Dop Hz",
    "Best BiSNR",
    "2nd BiSNR",
    "3rd BiSNR",
    "GS kt",
    "Age s",
)
TRACK_FOCUS_COLUMNS = (
    "Observer",
    "Range km",
    "RR m/s",
    "Az deg",
    "Dop Hz",
    "CPA km",
    "CPA Brg",
    "CPA s",
    "BiSNR 1",
    "BiSNR 2",
    "BiSNR 3",
    "BiSNR 4",
    "BiSNR 5",
)
SORT_DIRECTIONS: Mapping[str, bool] = {
    "ICAO": False,
    "Callsign": False,
    "Msgs": True,
    "Alt ft": True,
    "Rng km": False,
    "Az deg": False,
    "El deg": True,
    "RR m/s": False,
    "Dop Hz": False,
    "Best BiSNR": False,
    "2nd BiSNR": False,
    "3rd BiSNR": False,
    "GS kt": True,
    "Age s": False,
}
DEFAULT_SORT_COLUMN = "Rng km"


class ADSBConsoleApp(App[None]):
    """Minimal Textual monitor for BaseStation TCP streams."""

    CSS = """
    #summary {
        height: 3;
        padding: 0 1;
    }

    #track-detail {
        height: 3;
        padding: 0 1;
    }

    #main {
        height: 1fr;
    }

    #left {
        width: 2fr;
    }

    #tracks {
        height: 1fr;
    }

    #bisnr-breakdown {
        height: auto;
        max-height: 14;
        padding: 0 1;
        overflow-y: auto;
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
        ("h", "toggle_aged_tracks", "Hide aged"),
        ("enter", "focus_selected_track", "Track focus"),
        ("o", "next_observer", "Next observer"),
        ("q", "quit", "Quit"),
        ("s", "next_sort_column", "Sort column"),
        ("u", "next_update_rate", "Update rate"),
        ("escape", "observer_focus", "Observer focus"),
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
        hide_aged_tracks: bool = True,
        age_out_seconds: float = DEFAULT_AGE_OUT_SECONDS,
        carrier_frequency_mhz: float = DEFAULT_CARRIER_FREQUENCY_MHZ,
        track_retention_minutes: float = DEFAULT_TRACK_RETENTION_MINUTES,
        dtv_file: Path | None = DEFAULT_DTV_FILE,
        dtv_bands: str = DEFAULT_DTV_BANDS,
        bistatic_display_tracks: int = DEFAULT_BISTATIC_DISPLAY_TRACKS,
    ) -> None:
        super().__init__()
        self.source = source
        self.observers = observers or load_observers_or_default(None)
        self.selected_observer_index = _selected_observer_index(self.observers, selected_observer)
        self.tracker = BaseStationTracker(
            stale_track_seconds=track_retention_minutes * 60.0
            if track_retention_minutes > 0.0
            else None
        )
        self.refresh_interval_s = refresh_interval_s(refresh_rate_hz)
        self.max_display_range_km = max_display_range_km
        self.show_all_tracks = False
        self.hide_aged_tracks = hide_aged_tracks
        self.age_out_seconds = age_out_seconds
        self.carrier_frequency_hz = carrier_frequency_mhz * 1_000_000.0
        self.dtv_bands = parse_dtv_bands(dtv_bands)
        self.dtv_emitters = load_dtv_emitters(dtv_file, self.dtv_bands)
        self.bistatic_display_tracks = max(bistatic_display_tracks, 0)
        self.reported_bistatic_towers: set[tuple[str, str, str]] = set()
        self.icao_filter_text = icao_filter
        self.icao_filter = _compile_icao_filter(icao_filter)
        self.sort_column_index = SORT_COLUMNS.index(DEFAULT_SORT_COLUMN)
        self.last_track_icao = ""
        self.last_filter_result = DisplayFilterResult.empty()
        self.screen_focus = OBSERVER_TABLE
        self.selected_track_icao: str | None = None
        self.track_focus_snapshot: TrackFocusSnapshot | None = None
        self.table_layout: str | None = None
        self.table: DataTable[str] | None = None
        self.summary: Static | None = None
        self.filter_input: Input | None = None
        self.track_detail: Static | None = None
        self.event_log: RichLog | None = None
        self.bisnr_breakdown: Static | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("Disconnected", id="summary")
        with Horizontal(id="main"):
            with Vertical(id="left"):
                yield Input(
                    value=self.icao_filter_text,
                    placeholder="ICAO regex filter. Press f to focus, Enter to apply.",
                    id="filter",
                )
                yield Static("", id="track-detail")
                yield DataTable(id="tracks")
                yield Static("", id="bisnr-breakdown")
            yield RichLog(
                id="log",
                highlight=True,
                markup=True,
                max_lines=DEFAULT_LOG_MAX_LINES,
            )
        yield Footer()

    async def on_mount(self) -> None:
        self.table = self.query_one("#tracks", DataTable)
        self.summary = self.query_one("#summary", Static)
        self.filter_input = self.query_one("#filter", Input)
        self.track_detail = self.query_one("#track-detail", Static)
        self.event_log = self.query_one("#log", RichLog)
        self.bisnr_breakdown = self.query_one("#bisnr-breakdown", Static)
        self.table.cursor_type = "row"
        self._ensure_table_layout(OBSERVER_TABLE)
        self._write_log(f"Loaded {len(self.observers)} observer(s)")
        self._write_log(f"Loaded {len(self.dtv_emitters)} DTV emitter(s)")
        self.set_interval(DEFAULT_BISNR_BREAKDOWN_REFRESH_SECONDS, self._refresh_bisnr_breakdown)
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
        if self.screen_focus == TRACK_FOCUS_TABLE:
            self._refresh_track_focus_table()
            return self.last_filter_result
        return self._refresh_observer_table()

    def _refresh_observer_table(self) -> DisplayFilterResult:
        if self.table is None:
            return DisplayFilterResult.empty()
        self._ensure_table_layout(OBSERVER_TABLE)
        self._update_track_detail("")
        self.table.clear()
        now = utc_now()
        observed_tracks = self.tracker.observed_tracks(
            [self.selected_observer],
            carrier_frequency_hz=self.carrier_frequency_hz,
        )
        filter_result = filter_display_tracks(
            observed_tracks=observed_tracks,
            icao_filter=self.icao_filter,
            max_range_km=None if self.show_all_tracks else self.max_display_range_km,
            max_rows=None if self.show_all_tracks else DEFAULT_MAX_FILTERED_ROWS,
            track_lookup=self.tracker.tracks,
            sort_column=self.sort_column,
            now=now,
            hide_aged_tracks=self.hide_aged_tracks,
            age_out_seconds=self.age_out_seconds,
        )
        bistatic_by_icao = main_display_bistatic_by_icao(
            filter_result.visible_tracks,
            self.tracker.tracks,
            self.selected_observer,
            self.dtv_emitters,
            track_limit=self.bistatic_display_tracks,
        )
        self._log_bistatic_towers(
            self.selected_observer,
            [
                measurement
                for measurements in bistatic_by_icao.values()
                for measurement in measurements
            ],
        )
        for observed_track in filter_result.visible_tracks:
            track = self.tracker.tracks[observed_track.icao]
            self.table.add_row(
                *_track_row(
                    observed_track,
                    track,
                    now,
                    bistatic_by_icao.get(observed_track.icao, ()),
                ),
                key=observed_track.icao,
            )
        return filter_result

    def _refresh_track_focus_table(self) -> None:
        if self.table is None or self.selected_track_icao is None:
            return
        self._ensure_table_layout(TRACK_FOCUS_TABLE)
        self.table.clear()
        now = utc_now()
        snapshot = self._track_focus_snapshot(now)
        if snapshot is None:
            self._update_track_detail("Track focus: no selected track")
            return
        self.track_focus_snapshot = snapshot
        self._update_track_detail(_track_focus_detail(snapshot, now, self.age_out_seconds))
        for observed_track in snapshot.observed_tracks:
            track = snapshot.track
            measurements = (
                []
                if track.last_position is None
                else top_bistatic_measurements(
                    emitters=self.dtv_emitters,
                    receiver=_observer_by_name(self.observers, observed_track.observer_name),
                    position=track.last_position,
                    velocity=track.last_velocity,
                    count=5,
                )
            )
            self._log_bistatic_towers(
                _observer_by_name(self.observers, observed_track.observer_name),
                measurements,
            )
            self.table.add_row(
                *track_focus_row(observed_track, measurements),
                key=observed_track.observer_name,
            )

    def _refresh_bisnr_breakdown(self) -> None:
        if self.bisnr_breakdown is None:
            return
        if self.screen_focus != TRACK_FOCUS_TABLE or self.selected_track_icao is None:
            self.bisnr_breakdown.update("")
            return
        track = self.tracker.tracks.get(self.selected_track_icao)
        if track is None or track.last_position is None:
            self.bisnr_breakdown.update("BiSNR breakdown: no position")
            return
        self.bisnr_breakdown.update(
            bisnr_breakdown_table(
                self.observers,
                self.dtv_emitters,
                track.last_position,
                track.last_velocity,
            )
        )

    def _track_focus_snapshot(self, now: datetime) -> TrackFocusSnapshot | None:
        if self.selected_track_icao is None:
            return None
        track = self.tracker.tracks.get(self.selected_track_icao)
        if track is None:
            if self.track_focus_snapshot is None:
                return None
            return TrackFocusSnapshot(
                icao=self.track_focus_snapshot.icao,
                track=self.track_focus_snapshot.track,
                observed_tracks=self.track_focus_snapshot.observed_tracks,
                captured_at=self.track_focus_snapshot.captured_at,
                dropped=True,
            )
        observed_tracks = [
            observed_track
            for observer in self.observers
            if (
                observed_track := self.tracker.project_track(
                    track,
                    observer,
                    carrier_frequency_hz=self.carrier_frequency_hz,
                )
            )
            is not None
        ]
        return TrackFocusSnapshot(
            icao=self.selected_track_icao,
            track=track,
            observed_tracks=observed_tracks,
            captured_at=now,
            dropped=False,
        )

    def _ensure_table_layout(self, layout: str) -> None:
        if self.table is None or self.table_layout == layout:
            return
        self.table.clear(columns=True)
        if layout == TRACK_FOCUS_TABLE:
            self.table.add_columns(*TRACK_FOCUS_COLUMNS)
        else:
            self.table.add_columns(*SORT_COLUMNS)
        self.table_layout = layout

    def _update_track_detail(self, message: str) -> None:
        if self.track_detail is not None:
            self.track_detail.update(message)

    def _write_log(self, message: str) -> None:
        if self.event_log is not None:
            self.event_log.write(message)

    def _log_bistatic_towers(
        self, receiver: ObserverConfig, measurements: list[BistaticMeasurement]
    ) -> None:
        new_measurements: list[BistaticMeasurement] = []
        for measurement in measurements:
            key = (self.screen_focus, receiver.name, measurement.emitter.tower_key)
            if key in self.reported_bistatic_towers:
                continue
            self.reported_bistatic_towers.add(key)
            new_measurements.append(measurement)
        if new_measurements:
            self._write_log(bistatic_tower_log_table(receiver, new_measurements))

    def _update_summary(self, message: str) -> None:
        if self.summary is not None:
            self.summary.update(message)

    @property
    def selected_observer(self) -> ObserverConfig:
        return self.observers[self.selected_observer_index]

    def action_next_observer(self) -> None:
        self.selected_observer_index = (self.selected_observer_index + 1) % len(self.observers)
        self.reported_bistatic_towers.clear()
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

    def action_next_sort_column(self) -> None:
        if self.screen_focus != OBSERVER_TABLE:
            return
        self.sort_column_index = (self.sort_column_index + 1) % len(SORT_COLUMNS)
        self.last_filter_result = self._refresh_table()
        self._write_log(f"Sort column: {self.sort_label}")
        self._update_summary(self._summary_text())

    def action_next_update_rate(self) -> None:
        self.refresh_interval_s = next_refresh_interval_s(self.refresh_interval_s)
        self._write_log(f"Screen update interval: {self.refresh_interval_s:.0f}s")
        self._update_summary(self._summary_text())

    def action_toggle_aged_tracks(self) -> None:
        self.hide_aged_tracks = not self.hide_aged_tracks
        self.last_filter_result = self._refresh_table()
        mode = f"hidden >{self.age_out_seconds:.0f}s" if self.hide_aged_tracks else "shown"
        self._write_log(f"Aged tracks: {mode}")
        self._update_summary(self._summary_text())

    def action_focus_selected_track(self) -> None:
        if self.screen_focus != OBSERVER_TABLE or self.table is None:
            return
        if self.filter_input is not None and self.focused is self.filter_input:
            return
        if not self.table.is_valid_row_index(self.table.cursor_row):
            return
        row = self.table.get_row_at(self.table.cursor_row)
        if not row:
            return
        self._enter_track_focus(str(row[0]))

    def action_observer_focus(self) -> None:
        if self.screen_focus == OBSERVER_TABLE:
            return
        self.screen_focus = OBSERVER_TABLE
        self.reported_bistatic_towers.clear()
        self.selected_track_icao = None
        self.track_focus_snapshot = None
        self.last_filter_result = self._refresh_table()
        self._write_log("Focus: observer")
        self._update_summary(self._summary_text())
        self._refresh_bisnr_breakdown()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if self.screen_focus != OBSERVER_TABLE:
            return
        self._enter_track_focus(str(event.row_key.value))

    def _enter_track_focus(self, icao: str) -> None:
        if not icao or icao not in self.tracker.tracks:
            return
        self.screen_focus = TRACK_FOCUS_TABLE
        self.reported_bistatic_towers.clear()
        self.selected_track_icao = icao
        self.track_focus_snapshot = None
        self._refresh_track_focus_table()
        self._write_log(f"Focus: track {icao}")
        self._update_summary(self._summary_text())
        self._refresh_bisnr_breakdown()

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
        if self.screen_focus == TRACK_FOCUS_TABLE:
            return self._track_focus_summary_text(status)
        filter_result = self.last_filter_result
        display_mode = "all" if self.show_all_tracks else f"{self.max_display_range_km:.0f} km"
        filter_text = self.icao_filter_text or "none"
        aged_text = f"hidden >{self.age_out_seconds:.0f}s" if self.hide_aged_tracks else "shown"
        prefix = f"{status} | " if status else ""
        return (
            f"{prefix}Messages: {self.tracker.message_count} | "
            f"Tracks: {len(self.tracker.tracks)} | "
            f"Purged: {self.tracker.purged_track_count} | "
            f"Observer: {self.selected_observer.name} | "
            f"Visible: {filter_result.visible_count}/{filter_result.observer_track_count} | "
            f"Hidden: {filter_result.hidden_count} | "
            f"Range: {display_mode} | "
            f"ICAO: {filter_text} | "
            f"Aged: {aged_text} | "
            f"Update: {self.refresh_interval_s:.0f}s | "
            f"Sort: {self.sort_label} | "
            f"Last: {self.last_track_icao or '-'}"
        )

    def _track_focus_summary_text(self, status: str | None = None) -> str:
        snapshot = self.track_focus_snapshot
        prefix = f"{status} | " if status else ""
        selected = self.selected_track_icao or "-"
        track_status = (
            "-"
            if snapshot is None
            else track_focus_status(snapshot, utc_now(), self.age_out_seconds)
        )
        observer_count = 0 if snapshot is None else len(snapshot.observed_tracks)
        return (
            f"{prefix}Messages: {self.tracker.message_count} | "
            f"Tracks: {len(self.tracker.tracks)} | "
            f"Purged: {self.tracker.purged_track_count} | "
            f"Focus: Track {selected} | "
            f"Status: {track_status} | "
            f"Observers: {observer_count} | "
            f"Update: {self.refresh_interval_s:.0f}s | "
            f"Esc: observer focus"
        )

    @property
    def sort_column(self) -> str:
        return SORT_COLUMNS[self.sort_column_index]

    @property
    def sort_label(self) -> str:
        direction = "desc" if SORT_DIRECTIONS[self.sort_column] else "asc"
        return f"{self.sort_column} {direction}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Textual ADS-B BaseStation stream monitor.")
    parser.add_argument("--source", default="127.0.0.1:28887", type=parse_endpoint)
    parser.add_argument("--observerfile", default=None, type=Path)
    parser.add_argument("--observer", default=None)
    parser.add_argument("--refresh-rate", default=DEFAULT_SCREEN_REFRESH_HZ, type=float)
    parser.add_argument("--max-display-range-km", default=DEFAULT_MAX_DISPLAY_RANGE_KM, type=float)
    parser.add_argument("--icao-filter", default="")
    parser.add_argument("--show-aged-tracks", action="store_true")
    parser.add_argument("--age-out-seconds", default=DEFAULT_AGE_OUT_SECONDS, type=float)
    parser.add_argument(
        "--carrier-frequency-mhz", default=DEFAULT_CARRIER_FREQUENCY_MHZ, type=float
    )
    parser.add_argument(
        "--track-retention-minutes",
        default=DEFAULT_TRACK_RETENTION_MINUTES,
        type=float,
        help="Purge tracks with no reports after this many minutes; <=0 disables purge.",
    )
    parser.add_argument(
        "--dtv-file",
        default=DEFAULT_DTV_FILE,
        type=Path,
        help="CSV file of DTV emitters used for passive bistatic SNR estimates.",
    )
    parser.add_argument(
        "--dtv-bands",
        default=DEFAULT_DTV_BANDS,
        help="Comma-separated DTV bands to include: low-vhf, high-vhf, uhf, or all.",
    )
    parser.add_argument(
        "--bistatic-display-tracks",
        default=DEFAULT_BISTATIC_DISPLAY_TRACKS,
        type=int,
        help="Compute main-table DTV bistatic columns for this many closest tracks.",
    )
    args = parser.parse_args()

    ADSBConsoleApp(
        source=args.source,
        observers=load_observers_or_default(args.observerfile),
        selected_observer=args.observer,
        refresh_rate_hz=args.refresh_rate,
        max_display_range_km=args.max_display_range_km,
        icao_filter=args.icao_filter,
        hide_aged_tracks=not args.show_aged_tracks,
        age_out_seconds=args.age_out_seconds,
        carrier_frequency_mhz=args.carrier_frequency_mhz,
        track_retention_minutes=args.track_retention_minutes,
        dtv_file=args.dtv_file,
        dtv_bands=args.dtv_bands,
        bistatic_display_tracks=args.bistatic_display_tracks,
    ).run()


def _track_row(
    observed_track: ObservedTrack,
    track: TrackState,
    now: datetime,
    bistatic_measurements: list[BistaticMeasurement] | tuple[BistaticMeasurement, ...],
) -> tuple[str, str, str, str, str, str, str, str, str, str, str, str, str, str]:
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
        "" if observed_track.range_rate_mps is None else f"{observed_track.range_rate_mps:.1f}",
        "" if observed_track.doppler_hz is None else f"{observed_track.doppler_hz:.1f}",
        _format_bistatic_summary(bistatic_measurements, 0, include_bearing=False),
        _format_bistatic_summary(bistatic_measurements, 1, include_bearing=False),
        _format_bistatic_summary(bistatic_measurements, 2, include_bearing=False),
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


def _observer_by_name(observers: list[ObserverConfig], name: str) -> ObserverConfig:
    for observer in observers:
        if observer.name == name:
            return observer
    raise ValueError(f"Observer not found: {name}")


def main_display_bistatic_by_icao(
    visible_tracks: list[ObservedTrack],
    track_lookup: Mapping[str, TrackState],
    receiver: ObserverConfig,
    emitters: list[DtvEmitter],
    *,
    track_limit: int,
) -> dict[str, list[BistaticMeasurement]]:
    if track_limit <= 0 or not emitters:
        return {}
    closest_tracks = sorted(visible_tracks, key=lambda item: item.range_az_el.range_m)[:track_limit]
    results: dict[str, list[BistaticMeasurement]] = {}
    for observed_track in closest_tracks:
        track = track_lookup.get(observed_track.icao)
        if track is None or track.last_position is None:
            continue
        results[observed_track.icao] = top_bistatic_measurements(
            emitters=emitters,
            receiver=receiver,
            position=track.last_position,
            velocity=track.last_velocity,
            count=3,
        )
    return results


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


@dataclass(frozen=True, slots=True)
class TrackFocusSnapshot:
    icao: str
    track: TrackState
    observed_tracks: list[ObservedTrack]
    captured_at: datetime
    dropped: bool


def _track_focus_detail(snapshot: TrackFocusSnapshot, now: datetime, age_out_seconds: float) -> str:
    track = snapshot.track
    position = track.last_position
    velocity = track.last_velocity
    status = track_focus_status(snapshot, now, age_out_seconds)
    lla_text = (
        "-"
        if position is None
        else (
            f"{position.latitude_deg:.6f}, {position.longitude_deg:.6f}, "
            f"{position.altitude_ft:.0f} ft"
        )
    )
    gs_text = "-" if velocity is None else f"{velocity.ground_speed_kt:.0f}"
    age_s = (now - track.last_seen).total_seconds()
    return (
        f"Track focus: {status} | ICAO: {track.icao} | Callsign: {track.callsign or '-'} | "
        f"Msgs: {track.message_count} | LLA: {lla_text}\n"
        f"GS kt: {gs_text} | Age s: {age_s:.1f} | Esc returns to observer focus"
    )


def track_focus_status(snapshot: TrackFocusSnapshot, now: datetime, age_out_seconds: float) -> str:
    if snapshot.dropped:
        return "dropped"
    if (now - snapshot.track.last_seen).total_seconds() > age_out_seconds:
        return "aged-out"
    return "active"


def track_focus_row(
    observed_track: ObservedTrack,
    bistatic_measurements: list[BistaticMeasurement] | tuple[BistaticMeasurement, ...] = (),
) -> tuple[str, str, str, str, str, str, str, str, str, str, str, str, str]:
    cpa = observed_track.cpa
    return (
        observed_track.observer_name[:10],
        _format_optional_float(observed_track.range_az_el.range_m / 1000.0, precision=1),
        _format_optional_float(observed_track.range_rate_mps, precision=1),
        _format_optional_float(observed_track.range_az_el.azimuth_deg, precision=1),
        _format_optional_float(observed_track.doppler_hz, precision=1),
        "" if cpa is None else _format_optional_float(cpa.range_m / 1000.0, precision=1),
        "" if cpa is None else _format_optional_float(cpa.bearing_deg, precision=1),
        "" if cpa is None else _format_optional_float(cpa.time_s, precision=1),
        _format_bistatic_summary(bistatic_measurements, 0, include_bearing=False),
        _format_bistatic_summary(bistatic_measurements, 1, include_bearing=False),
        _format_bistatic_summary(bistatic_measurements, 2, include_bearing=False),
        _format_bistatic_summary(bistatic_measurements, 3, include_bearing=False),
        _format_bistatic_summary(bistatic_measurements, 4, include_bearing=False),
    )


def _format_bistatic_summary(
    measurements: list[BistaticMeasurement] | tuple[BistaticMeasurement, ...],
    index: int,
    *,
    include_bearing: bool,
) -> str:
    if index >= len(measurements):
        return ""
    measurement = measurements[index]
    emitter_label = measurement.emitter.call_sign or measurement.emitter.site_name or "DTV"
    bearing = f" @{measurement.bearing_to_emitter_deg:.0f}°" if include_bearing else ""
    doppler = (
        ""
        if measurement.bistatic_doppler_hz is None
        else f" {measurement.bistatic_doppler_hz:.0f}Hz"
    )
    return (
        f"{emitter_label[:12]}{bearing}: "
        f"{measurement.snr_db:.0f}dB "
        f"{measurement.bistatic_range_km:.0f}km"
        f"{doppler}"
    )


def bisnr_breakdown_table(
    observers: list[ObserverConfig],
    emitters: list[DtvEmitter],
    position: PositionReport,
    velocity: VelocityReport | None,
) -> str:
    """Render the bistatic SNR equation broken out by term, one row per observer.

    Uses the strongest DTV emitter for each observer (the same one behind its
    "BiSNR 1" column) so the breakdown explains a number already on screen.
    """

    table = PrettyTable()
    table.field_names = [
        "Observer",
        "Tower",
        "EIRP",
        "Gr",
        "20logλ",
        "RCS",
        "Spread",
        "TxLoss",
        "RxLoss",
        "Noise",
        "PolLoss",
        "SysLoss",
        "SNR",
    ]
    for field in table.field_names:
        table.align[field] = "l" if field in ("Observer", "Tower") else "r"

    for observer in observers:
        measurements = top_bistatic_measurements(
            emitters=emitters,
            receiver=observer,
            position=position,
            velocity=velocity,
            count=1,
        )
        if not measurements:
            table.add_row([observer.name[:16], *(["-"] * (len(table.field_names) - 1))])
            continue
        measurement = measurements[0]
        breakdown = measurement.snr_breakdown
        tower = measurement.emitter.call_sign or measurement.emitter.site_name or "DTV"
        table.add_row(
            [
                observer.name[:16],
                tower[:12],
                f"{breakdown.eirp_dbw:.1f}",
                f"{breakdown.receiver_gain_dbi:.1f}",
                f"{breakdown.wavelength_gain_db:.1f}",
                f"{breakdown.rcs_dbsm:.1f}",
                f"{breakdown.spreading_loss_db:.1f}",
                f"{breakdown.tx_range_loss_db:.1f}",
                f"{breakdown.rx_range_loss_db:.1f}",
                f"{breakdown.noise_floor_db:.1f}",
                f"{breakdown.polarization_loss_db:.1f}",
                f"{breakdown.system_loss_db:.1f}",
                f"{breakdown.snr_db:.1f}",
            ]
        )
    return str(table)


def bistatic_tower_log_table(
    receiver: ObserverConfig, measurements: list[BistaticMeasurement]
) -> str:
    rows = [
        (
            receiver.name[:10],
            measurement.emitter.call_sign or "-",
            (measurement.emitter.site_name or "-")[:24],
            f"{measurement.emitter.center_frequency_mhz:.0f}",
            f"{measurement.bearing_to_emitter_deg:.0f}",
        )
        for measurement in measurements
    ]
    return "DTV towers\n" + _pretty_table(
        ("Observer", "Call", "Site", "MHz", "Brg"),
        rows,
    )


def _pretty_table(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    widths = [
        max(len(header), *(len(row[index]) for row in rows)) for index, header in enumerate(headers)
    ]
    border = "+" + "+".join("-" * (width + 2) for width in widths) + "+"
    header_row = _pretty_table_row(headers, widths)
    body_rows = [_pretty_table_row(row, widths) for row in rows]
    return "\n".join([border, header_row, border, *body_rows, border])


def _pretty_table_row(values: tuple[str, ...], widths: list[int]) -> str:
    cells = [f" {value:<{width}} " for value, width in zip(values, widths, strict=True)]
    return "|" + "|".join(cells) + "|"


def _format_optional_float(value: float | None, *, precision: int) -> str:
    if value is None or not isfinite(value):
        return ""
    return f"{value:.{precision}f}"


def filter_display_tracks(
    *,
    observed_tracks: list[ObservedTrack],
    icao_filter: re.Pattern[str] | None,
    max_range_km: float | None,
    max_rows: int | None,
    track_lookup: Mapping[str, TrackState] | None = None,
    sort_column: str | None = None,
    now: datetime | None = None,
    hide_aged_tracks: bool = True,
    age_out_seconds: float = DEFAULT_AGE_OUT_SECONDS,
) -> DisplayFilterResult:
    filtered: list[ObservedTrack] = []
    for observed_track in observed_tracks:
        if hide_aged_tracks and _is_aged_track(observed_track, track_lookup, now, age_out_seconds):
            continue
        if max_range_km is not None and observed_track.range_az_el.range_m > max_range_km * 1000.0:
            continue
        if icao_filter is not None and icao_filter.search(observed_track.icao) is None:
            continue
        filtered.append(observed_track)

    if track_lookup is not None and sort_column is not None and now is not None:
        filtered = sort_display_tracks(filtered, track_lookup, sort_column, now)

    visible_tracks = filtered if max_rows is None else filtered[:max_rows]
    hidden_count = len(observed_tracks) - len(visible_tracks)
    return DisplayFilterResult(
        visible_tracks=visible_tracks,
        observer_track_count=len(observed_tracks),
        hidden_count=hidden_count,
    )


def _is_aged_track(
    observed_track: ObservedTrack,
    track_lookup: Mapping[str, TrackState] | None,
    now: datetime | None,
    age_out_seconds: float,
) -> bool:
    if track_lookup is None or now is None:
        return False
    track = track_lookup.get(observed_track.icao)
    if track is None:
        return True
    return (now - track.last_seen).total_seconds() > age_out_seconds


def sort_display_tracks(
    observed_tracks: list[ObservedTrack],
    track_lookup: Mapping[str, TrackState],
    sort_column: str,
    now: datetime,
) -> list[ObservedTrack]:
    reverse = SORT_DIRECTIONS.get(sort_column, False)
    if sort_column == "ICAO":
        sorted_tracks = sorted(observed_tracks, key=lambda item: item.icao, reverse=reverse)
    elif sort_column == "Callsign":
        sorted_tracks = sorted(
            observed_tracks,
            key=lambda item: _track_callsign(track_lookup, item),
            reverse=reverse,
        )
    elif sort_column == "Msgs":
        sorted_tracks = sorted(
            observed_tracks,
            key=lambda item: _track_message_count(track_lookup, item),
            reverse=reverse,
        )
    elif sort_column == "Alt ft":
        sorted_tracks = sorted(
            observed_tracks,
            key=lambda item: _track_altitude_ft(track_lookup, item),
            reverse=reverse,
        )
    elif sort_column == "Rng km":
        sorted_tracks = sorted(
            observed_tracks,
            key=lambda item: item.range_az_el.range_m,
            reverse=reverse,
        )
    elif sort_column == "Az deg":
        sorted_tracks = sorted(
            observed_tracks, key=lambda item: item.range_az_el.azimuth_deg, reverse=reverse
        )
    elif sort_column == "El deg":
        sorted_tracks = sorted(
            observed_tracks, key=lambda item: item.range_az_el.elevation_deg, reverse=reverse
        )
    elif sort_column == "RR m/s":
        sorted_tracks = sorted(observed_tracks, key=_observed_track_range_rate_mps, reverse=reverse)
    elif sort_column == "Dop Hz":
        sorted_tracks = sorted(observed_tracks, key=_observed_track_doppler_hz, reverse=reverse)
    elif sort_column == "GS kt":
        sorted_tracks = sorted(
            observed_tracks,
            key=lambda item: _track_ground_speed_kt(track_lookup, item),
            reverse=reverse,
        )
    elif sort_column == "Age s":
        sorted_tracks = sorted(
            observed_tracks,
            key=lambda item: _track_age_s(track_lookup, item, now),
            reverse=reverse,
        )
    else:
        sorted_tracks = observed_tracks
    return sorted_tracks


def _track_callsign(track_lookup: Mapping[str, TrackState], observed_track: ObservedTrack) -> str:
    track = track_lookup.get(observed_track.icao)
    return "" if track is None or track.callsign is None else track.callsign


def _track_message_count(
    track_lookup: Mapping[str, TrackState], observed_track: ObservedTrack
) -> int:
    track = track_lookup.get(observed_track.icao)
    return 0 if track is None else track.message_count


def _track_altitude_ft(
    track_lookup: Mapping[str, TrackState], observed_track: ObservedTrack
) -> float:
    track = track_lookup.get(observed_track.icao)
    if track is None or track.last_position is None:
        return float("-inf")
    return track.last_position.altitude_ft


def _track_ground_speed_kt(
    track_lookup: Mapping[str, TrackState], observed_track: ObservedTrack
) -> float:
    track = track_lookup.get(observed_track.icao)
    if track is None or track.last_velocity is None:
        return float("-inf")
    return track.last_velocity.ground_speed_kt


def _observed_track_range_rate_mps(observed_track: ObservedTrack) -> float:
    if observed_track.range_rate_mps is None:
        return float("inf")
    return observed_track.range_rate_mps


def _observed_track_doppler_hz(observed_track: ObservedTrack) -> float:
    if observed_track.doppler_hz is None:
        return float("inf")
    return observed_track.doppler_hz


def _track_age_s(
    track_lookup: Mapping[str, TrackState], observed_track: ObservedTrack, now: datetime
) -> float:
    track = track_lookup.get(observed_track.icao)
    if track is None:
        return float("inf")
    return (now - track.last_seen).total_seconds()


def _compile_icao_filter(pattern: str) -> re.Pattern[str] | None:
    if not pattern:
        return None
    return re.compile(pattern, re.IGNORECASE)


def refresh_interval_s(refresh_rate_hz: float) -> float:
    if refresh_rate_hz <= 0.0:
        return 0.0
    return 1.0 / refresh_rate_hz


def next_refresh_interval_s(current_interval_s: float) -> float:
    for interval_s in SCREEN_REFRESH_SECONDS_CHOICES:
        if current_interval_s < interval_s:
            return interval_s
    return SCREEN_REFRESH_SECONDS_CHOICES[0]


if __name__ == "__main__":
    main()
