"""Build a CT cue compression dictionary (ICD_Messages.md section 1.3), reproducibly.

The dictionary is a preset dictionary for raw deflate: typical message text that each
datagram can refer back to. It is built from real CT 2.0.0 messages, generated here by
replaying recorded SBS captures through the production prediction and serialization code
with fixed ids and clocks, so the same inputs always give the same bytes. Every corpus
message is validated against the 2.0.0 schemas first.

    python3 tools/build_cue_dictionary.py --id 1            # write schemas/dictionaries/
    python3 tools/build_cue_dictionary.py --id 1 --check    # rebuild and compare, write nothing

A released dictionary is immutable: build a new id for any change (through a CR).
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
import uuid
import zlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from adsb_console.bistatic import load_dtv_emitters, parse_dtv_bands
from adsb_console.config import load_observers_or_default
from adsb_console.cue import MAXIMUM_DICTIONARY_BYTES, SCHEMA_VERSION, CueSerializer
from adsb_console.cue_config import load_cue_runtime_config
from adsb_console.prediction import (
    PredictionRevisionManager,
    PredictionUpdateReason,
    build_track_prediction,
)
from adsb_console.tracker import BaseStationTracker

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CAPTURES = sorted((ROOT / "tests" / "fixtures" / "sbs_clean").glob("*.csv.gz"))
DEFAULT_OBSERVERS = ROOT / "deploy" / "pi-observers.ini"
DEFAULT_CONFIG = ROOT / "deploy" / "pi-cue-config.json"
DEFAULT_EMITTERS = ROOT / "20_DTV_direct_path_input.csv"
NAMESPACE = uuid.UUID("5f0c1d2e-8a4b-4c6d-9e7f-0a1b2c3d4e5f")


def corpus_messages(captures: list[Path]) -> list[dict[str, object]]:
    runtime = load_cue_runtime_config(DEFAULT_CONFIG)
    config = runtime.prediction
    observers = load_observers_or_default(DEFAULT_OBSERVERS)
    emitters = load_dtv_emitters(DEFAULT_EMITTERS, parse_dtv_bands("uhf"))
    serializer = CueSerializer(
        maximum_opportunities=runtime.udp_output.maximum_opportunities_per_cue,
        assumed_rcs_dbsm=config.assumed_rcs_dbsm,
        detection_threshold_db=config.detection_threshold_db,
    )
    source = f"adsb-console-{uuid.uuid5(NAMESPACE, 'source')}"
    messages: list[dict[str, object]] = []
    sequence = 0

    def next_ids(label: str) -> tuple[int, str]:
        nonlocal sequence
        sequence += 1
        return sequence, str(uuid.uuid5(NAMESPACE, f"{label}/{sequence}"))

    revisions = PredictionRevisionManager()
    for capture in captures:
        tracker = BaseStationTracker(stale_track_seconds=None)
        with gzip.open(capture, "rt", encoding="utf-8", errors="replace") as lines:
            for line in lines:
                tracker.update_line(line.rstrip("\r\n"))
        for track in sorted(tracker.tracks.values(), key=lambda item: item.icao):
            if track.last_position is None or track.last_velocity is None:
                continue
            prediction = build_track_prediction(
                track=track,
                reference_origin=observers[0],
                observers=observers,
                emitters=emitters,
                config=config,
                token=revisions.request(f"adsb:{track.icao}"),
                created_utc=track.last_seen,
                update_reason=PredictionUpdateReason.INITIAL_TRACK,
            )
            if prediction.state is None:
                continue
            number, message_id = next_ids(f"{capture.name}/{track.icao}")
            messages.append(serializer.track_cue(
                prediction,
                source_instance_id=source,
                sequence_number=number,
                message_id=message_id,
                generated_utc=track.last_seen + timedelta(milliseconds=250),
            ))
    if not messages:
        raise SystemExit("No track cues produced from the captures")
    when = max(_generated(m) for m in messages)
    number, message_id = next_ids("heartbeat")
    heartbeat = serializer.heartbeat(
        source_instance_id=source, sequence_number=number, message_id=message_id,
        generated_utc=when, active_tracks=12, cue_eligible_tracks=5, active_observers=1,
        enabled_emitters=len(emitters), udp_destination="239.192.10.1:31986",
        last_full_snapshot_utc=when - timedelta(seconds=30), status="running",
    )
    snapshot = f"snapshot:{uuid.uuid5(NAMESPACE, 'snapshot')}"
    number, message_id = next_ids("begin")
    begin = serializer.snapshot_boundary(
        begin=True, snapshot_id=snapshot, source_instance_id=source, sequence_number=number,
        message_id=message_id, generated_utc=when, expected_track_count=5,
    )
    number, message_id = next_ids("end")
    end = serializer.snapshot_boundary(
        begin=False, snapshot_id=snapshot, source_instance_id=source, sequence_number=number,
        message_id=message_id, generated_utc=when, published_track_count=5, failed_track_count=0,
    )
    number, message_id = next_ids("withdrawal")
    withdrawal = serializer.withdrawal(
        track_id="adsb:A1B2C3", icao="A1B2C3", revision=12, reason="track_purged",
        source_instance_id=source, sequence_number=number, message_id=message_id,
        generated_utc=when,
    )
    return [withdrawal, end, begin, heartbeat, *messages]


def _generated(message: dict[str, object]) -> datetime:
    value = message["generated_utc_ms"]
    assert isinstance(value, int)
    return datetime.fromtimestamp(value / 1000, UTC)


def validate(messages: list[dict[str, object]]) -> None:
    validators = {}
    for path in (ROOT / "schemas").glob(f"*-{SCHEMA_VERSION}.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        message_type = schema.get("properties", {}).get("message_type", {}).get("const")
        if message_type:
            validators[message_type] = Draft202012Validator(schema, format_checker=FormatChecker())
    for message in messages:
        errors = list(validators[str(message["message_type"])].iter_errors(message))
        if errors:
            raise SystemExit(
                f"Corpus message fails its {SCHEMA_VERSION} schema: {errors[0].message}"
            )


def assemble(messages: list[dict[str, object]]) -> bytes:
    """Rare message types first, then track cues (most of the traffic) nearest the end, where
    deflate references are cheapest. Cues with opportunities go last; keep the final 32 KiB."""
    def encode(m: dict[str, object]) -> bytes:
        return json.dumps(m, allow_nan=False, separators=(",", ":"), sort_keys=True).encode()
    others = [m for m in messages if m["message_type"] != "track_cue"]
    cues = [m for m in messages if m["message_type"] == "track_cue"]
    cues.sort(key=lambda m: (len(m["opportunities"]), str(m["message_id"])))  # type: ignore[arg-type]
    return b"".join(encode(m) for m in [*others, *cues])[-MAXIMUM_DICTIONARY_BYTES:]


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--id", type=int, required=True, help="dictionary id, 1-255")
    parser.add_argument("--check", action="store_true", help="rebuild and compare only")
    parser.add_argument("captures", nargs="*", type=Path, default=DEFAULT_CAPTURES)
    args = parser.parse_args()
    messages = corpus_messages(list(args.captures))
    validate(messages)
    dictionary = assemble(messages)
    digest = hashlib.sha256(dictionary).hexdigest()
    target = ROOT / "schemas" / "dictionaries" / f"cue-dictionary-{args.id}.bin"
    cues = [m for m in messages if m["message_type"] == "track_cue"]
    with_opportunities = [m for m in cues if m["opportunities"]]
    compressed = []
    for m in with_opportunities[:50]:
        raw = json.dumps(m, separators=(",", ":"), sort_keys=True).encode()
        c = zlib.compressobj(9, zlib.DEFLATED, -15, 9, zlib.Z_DEFAULT_STRATEGY, dictionary)
        compressed.append((len(raw), 2 + len(c.compress(raw) + c.flush())))
    print(f"corpus: {len(cues)} track cues ({len(with_opportunities)} with opportunities), "
          f"{len(messages) - len(cues)} other messages, all valid against {SCHEMA_VERSION}")
    print(f"dictionary {target.name}: {len(dictionary)} bytes, sha256 {digest}")
    if compressed:
        print("in-corpus sanity check (optimistic): median track_cue "
              f"{sorted(r for r, _ in compressed)[len(compressed) // 2]} B -> "
              f"{sorted(c for _, c in compressed)[len(compressed) // 2]} B framed")
    if args.check:
        released = target.with_suffix(".sha256").read_text(encoding="utf-8").split()[0]
        if released != digest:
            sys.exit(f"MISMATCH: released {released}, rebuilt {digest}")
        print("matches the released dictionary")
        return
    if target.exists():
        sys.exit(f"{target.name} already exists; released dictionaries are immutable")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(dictionary)
    target.with_suffix(".sha256").write_text(f"{digest}  {target.name}\n", encoding="utf-8")
    record = {
        "dictionary": target.name,
        "sha256": digest,
        "bytes": len(dictionary),
        "schema_version": SCHEMA_VERSION,
        "built_by": "tools/build_cue_dictionary.py",
        "captures": [
            {"file": str(p.relative_to(ROOT)), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
            for p in args.captures
        ],
        "observers": str(DEFAULT_OBSERVERS.relative_to(ROOT)),
        "prediction_config": str(DEFAULT_CONFIG.relative_to(ROOT)),
        "emitters": f"{DEFAULT_EMITTERS.relative_to(ROOT)} (uhf)",
        "corpus_track_cues": len(cues),
    }
    target.with_suffix(".build.json").write_text(
        json.dumps(record, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {target.relative_to(ROOT)} (+ .sha256, .build.json)")


if __name__ == "__main__":
    main()
