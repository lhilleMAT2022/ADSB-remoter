"""UDP capture and planner-emulator validator for ADS-B passive-radar cues."""

from __future__ import annotations

import argparse
import json
import socket
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


@dataclass(slots=True)
class SemanticSummary:
    datagrams_received: int = 0
    valid_messages: int = 0
    invalid_messages: int = 0
    message_counts: Counter[str] = field(default_factory=Counter)
    unique_tracks: set[str] = field(default_factory=set)
    revisions_by_reason: Counter[str] = field(default_factory=Counter)
    withdrawals: int = 0
    duplicate_message_ids: int = 0
    sequence_gaps: int = 0
    out_of_order_sequences: int = 0
    stale_revisions: int = 0
    maximum_datagram_bytes: int = 0
    total_datagram_bytes: int = 0
    track_cue_sizes: list[int] = field(default_factory=list)
    opportunities_per_cue: list[int] = field(default_factory=list)
    snapshot_track_cue_counts: Counter[str] = field(default_factory=Counter)
    snapshot_expected_counts: dict[str, int] = field(default_factory=dict)
    snapshot_published_counts: dict[str, int] = field(default_factory=dict)
    schema_failures: list[str] = field(default_factory=list)
    _message_ids: set[str] = field(default_factory=set)
    _last_sequence_by_source: dict[str, int] = field(default_factory=dict)
    _latest_revision_by_track: dict[str, int] = field(default_factory=dict)

    def consume(self, raw: bytes, payload: dict[str, Any], errors: list[str]) -> None:
        self.datagrams_received += 1
        self.maximum_datagram_bytes = max(self.maximum_datagram_bytes, len(raw))
        self.total_datagram_bytes += len(raw)
        if errors:
            self.invalid_messages += 1
            self.schema_failures.extend(errors)
            return
        self.valid_messages += 1
        message_type = str(payload["message_type"])
        self.message_counts[message_type] += 1
        message_id = str(payload["message_id"])
        if message_id in self._message_ids:
            self.duplicate_message_ids += 1
        self._message_ids.add(message_id)
        source = str(payload["source_instance_id"])
        sequence = int(payload["sequence_number"])
        prior_sequence = self._last_sequence_by_source.get(source)
        if prior_sequence is not None and sequence <= prior_sequence:
            self.out_of_order_sequences += 1
        elif prior_sequence is not None and sequence > prior_sequence + 1:
            self.sequence_gaps += sequence - prior_sequence - 1
        self._last_sequence_by_source[source] = max(sequence, prior_sequence or sequence)
        if message_type == "track_cue":
            self.track_cue_sizes.append(len(raw))
            opportunities = payload.get("opportunities")
            if isinstance(opportunities, list):
                self.opportunities_per_cue.append(len(opportunities))
            snapshot_id = payload.get("snapshot_id")
            if isinstance(snapshot_id, str):
                self.snapshot_track_cue_counts[snapshot_id] += 1
            track = payload["track"]
            prediction = payload["prediction"]
            if isinstance(track, dict) and isinstance(prediction, dict):
                track_id = str(track["track_id"])
                revision = int(prediction["revision"])
                self.unique_tracks.add(track_id)
                prior_revision = self._latest_revision_by_track.get(track_id)
                if prior_revision is not None and revision < prior_revision:
                    self.stale_revisions += 1
                self._latest_revision_by_track[track_id] = max(revision, prior_revision or revision)
                self.revisions_by_reason[str(prediction["update_reason"])] += 1
        elif message_type == "track_cue_withdrawal":
            self.withdrawals += 1
        elif message_type == "cue_snapshot_begin":
            self.snapshot_expected_counts[str(payload["snapshot_id"])] = int(
                payload["expected_track_count"]
            )
        elif message_type == "cue_snapshot_end":
            self.snapshot_published_counts[str(payload["snapshot_id"])] = int(
                payload["published_track_count"]
            )

    def as_dict(self) -> dict[str, object]:
        return {
            "datagrams_received": self.datagrams_received,
            "valid_messages": self.valid_messages,
            "invalid_messages": self.invalid_messages,
            "message_counts": dict(self.message_counts),
            "unique_tracks": len(self.unique_tracks),
            "revisions_by_reason": dict(self.revisions_by_reason),
            "withdrawals": self.withdrawals,
            "duplicate_message_ids": self.duplicate_message_ids,
            "sequence_gaps": self.sequence_gaps,
            "out_of_order_sequences": self.out_of_order_sequences,
            "stale_revisions": self.stale_revisions,
            "maximum_datagram_bytes": self.maximum_datagram_bytes,
            "mean_datagram_bytes": (
                0.0
                if self.datagrams_received == 0
                else self.total_datagram_bytes / self.datagrams_received
            ),
            "schema_failure_count": len(self.schema_failures),
            "track_cue_size_bytes": _statistics(self.track_cue_sizes),
            "opportunities_per_track_cue": _statistics(self.opportunities_per_cue),
            "snapshot_track_cue_consistency": {
                snapshot_id: {
                    "expected_track_count": self.snapshot_expected_counts.get(snapshot_id),
                    "published_track_count": published_count,
                    "received_track_cue_count": self.snapshot_track_cue_counts[snapshot_id],
                    "matches_published": (
                        published_count == self.snapshot_track_cue_counts[snapshot_id]
                    ),
                }
                for snapshot_id, published_count in self.snapshot_published_counts.items()
            },
        }


def _statistics(values: list[int]) -> dict[str, float | int | None]:
    if not values:
        return {"minimum": None, "median": None, "maximum": None}
    return {
        "minimum": min(values),
        "median": median(values),
        "maximum": max(values),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", default="127.0.0.1:31001")
    parser.add_argument("--multicast-group")
    parser.add_argument("--multicast-interface", default="0.0.0.0")
    parser.add_argument("--duration-s", default=60.0, type=float)
    parser.add_argument("--jsonl", default=Path("cue_capture.jsonl"), type=Path)
    parser.add_argument("--summary", default=Path("cue_capture_summary.json"), type=Path)
    parser.add_argument("--schemas-dir", default=Path("schemas"), type=Path)
    parser.add_argument(
        "--receive-buffer-bytes",
        default=8 * 1024 * 1024,
        type=int,
        help="Socket receive buffer; snapshot bursts overflow the kernel default.",
    )
    args = parser.parse_args()
    host, port_text = args.bind.rsplit(":", 1)
    registry = _schema_registry(args.schemas_dir)
    summary = SemanticSummary()
    deadline = time.monotonic() + args.duration_s
    udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, args.receive_buffer_bytes)
    udp_socket.bind((host, int(port_text)))
    if args.multicast_group:
        membership = socket.inet_aton(args.multicast_group) + socket.inet_aton(
            args.multicast_interface
        )
        udp_socket.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
    udp_socket.settimeout(min(args.duration_s, 0.5))
    with args.jsonl.open("w", encoding="utf-8") as output:
        while time.monotonic() < deadline:
            try:
                raw, _address = udp_socket.recvfrom(65_507)
            except TimeoutError:
                continue
            received_utc = time.time()
            try:
                payload = json.loads(raw.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("JSON root is not an object")
                errors = _validate(payload, registry)
            except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
                payload = {"invalid_payload": True}
                errors = [str(exc)]
            summary.consume(raw, payload, errors)
            output.write(
                json.dumps(
                    {
                        "received_unix_s": received_utc,
                        "payload": payload,
                        "validation_errors": errors,
                    },
                    sort_keys=True,
                )
                + "\n"
            )
    udp_socket.close()
    args.summary.write_text(json.dumps(summary.as_dict(), indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary.as_dict(), indent=2, sort_keys=True))


def _schema_registry(directory: Path) -> dict[str, Draft202012Validator]:
    result: dict[str, Draft202012Validator] = {}
    for path in directory.glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for message_type in _message_types_from_schema(payload):
            result[message_type] = Draft202012Validator(payload, format_checker=FormatChecker())
    return result


def _message_types_from_schema(schema: dict[str, Any]) -> tuple[str, ...]:
    value = schema.get("properties", {}).get("message_type", {})
    if isinstance(value, dict) and isinstance(value.get("const"), str):
        return (value["const"],)
    if isinstance(value, dict) and isinstance(value.get("enum"), list):
        return tuple(item for item in value["enum"] if isinstance(item, str))
    return ()


def _validate(payload: dict[str, Any], registry: dict[str, Draft202012Validator]) -> list[str]:
    message_type = payload.get("message_type")
    validator = registry.get(message_type)
    if validator is None:
        return [f"No schema available for message type {message_type!r}"]
    return [error.message for error in validator.iter_errors(payload)]


if __name__ == "__main__":
    main()
