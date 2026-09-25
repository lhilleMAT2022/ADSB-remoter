"""Generate a reproducible manifest for the external SBS replay corpus."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from adsb_console.models import BaseStationMessage


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus-dir",
        type=Path,
        default=Path(
            os.environ.get(
                "ADSB_REPLAY_CORPUS_DIR",
                "../000_sbs_for_ELAD_cleanup/sbs_clean",
            )
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("tests/replay_corpus_manifest.json"),
    )
    args = parser.parse_args()
    files = sorted(args.corpus_dir.glob("*.csv.gz"))
    if not files:
        raise SystemExit(f"No .csv.gz files found in {args.corpus_dir}")
    manifest = {
        "schema_version": 1,
        "corpus_directory_environment_variable": "ADSB_REPLAY_CORPUS_DIR",
        "default_relative_corpus_directory": "../000_sbs_for_ELAD_cleanup/sbs_clean",
        "files": [_file_manifest(path) for path in files],
    }
    args.output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {args.output} for {len(files)} source files")


def _file_manifest(path: Path) -> dict[str, object]:
    first_timestamp: datetime | None = None
    last_timestamp: datetime | None = None
    malformed = 0
    messages = 0
    position_reports = 0
    velocity_reports = 0
    altitude_reports = 0
    icaos: set[str] = set()
    continuity: defaultdict[str, list[datetime]] = defaultdict(list)
    maneuver_candidates: defaultdict[str, int] = defaultdict(int)
    prior_velocity: dict[str, tuple[float, float, float | None]] = {}
    with gzip.open(path, "rt", encoding="utf-8", newline="") as stream:
        for raw_line in stream:
            try:
                message = BaseStationMessage.parse(raw_line)
            except ValueError:
                malformed += 1
                continue
            messages += 1
            if message.icao:
                icaos.add(message.icao)
            timestamp = message.generated_at
            if timestamp is not None:
                first_timestamp = (
                    timestamp if first_timestamp is None else min(first_timestamp, timestamp)
                )
                last_timestamp = (
                    timestamp if last_timestamp is None else max(last_timestamp, timestamp)
                )
            if message.position_report() is not None:
                position_reports += 1
                if message.icao and timestamp is not None:
                    continuity[message.icao].append(timestamp)
            if message.altitude_ft is not None:
                altitude_reports += 1
            velocity = message.velocity_report()
            if velocity is not None:
                velocity_reports += 1
                prior = prior_velocity.get(message.icao)
                current = (velocity.ground_speed_kt, velocity.track_deg, velocity.vertical_rate_fpm)
                if prior is not None and _velocity_changed(prior, current):
                    maneuver_candidates[message.icao] += 1
                prior_velocity[message.icao] = current
    candidates = {
        str(duration_s): sorted(
            icao
            for icao, timestamps in continuity.items()
            if timestamps
            and (max(timestamps) - min(timestamps)).total_seconds() >= duration_s
        )
        for duration_s in (10, 30, 60, 300, 600)
    }
    return {
        "relative_source_path": path.name,
        "byte_size": path.stat().st_size,
        "sha256": _sha256(path),
        "first_source_timestamp": _timestamp(first_timestamp),
        "last_source_timestamp": _timestamp(last_timestamp),
        "elapsed_source_duration_s": (
            None
            if first_timestamp is None or last_timestamp is None
            else (last_timestamp - first_timestamp).total_seconds()
        ),
        "line_message_count": messages,
        "distinct_icao_count": len(icaos),
        "position_report_count": position_reports,
        "velocity_report_count": velocity_reports,
        "altitude_report_count": altitude_reports,
        "candidate_tracks_by_continuity_s": candidates,
        "candidate_maneuver_counts": dict(sorted(maneuver_candidates.items())),
        "malformed_line_count": malformed,
    }


def _velocity_changed(
    first: tuple[float, float, float | None], second: tuple[float, float, float | None]
) -> bool:
    speed_changed = abs(first[0] - second[0]) >= 20.0
    heading_changed = abs((first[1] - second[1] + 180.0) % 360.0 - 180.0) >= 10.0
    vertical_changed = (
        first[2] is not None
        and second[2] is not None
        and abs(first[2] - second[2]) >= 500.0
    )
    return speed_changed or heading_changed or vertical_changed


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _timestamp(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat(timespec="milliseconds") + "Z"


if __name__ == "__main__":
    main()
