"""Decode CT cue datagrams given one per line as base64, printing one JSON message per line.

For watching the (compressed) cue stream with socat, which writes one base64 line per
datagram when each datagram gets its own child process (UDP4-RECVFROM + fork):

    socat -u UDP4-RECVFROM:31986,reuseaddr,ip-add-membership=239.192.10.1:192.168.10.41,fork \\
        SYSTEM:'base64 -w0; echo' | python3 tools/cue_decode.py | jq -c .

Plain and compressed datagrams (ICD_Messages.md section 1.3) are both accepted. Lines
that don't decode are reported on stderr and skipped.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import sys
from pathlib import Path

from adsb_console.cue import DICTIONARY_DIR, decode_datagram, load_dictionaries


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--dictionaries-dir", default=DICTIONARY_DIR, type=Path)
    args = parser.parse_args()
    dictionaries = load_dictionaries(args.dictionaries_dir)
    for raw_line in sys.stdin:
        text = raw_line.strip()
        if not text:
            continue
        try:
            message = decode_datagram(base64.b64decode(text, validate=True), dictionaries)
            sys.stdout.write(message.decode("utf-8") + "\n")
            sys.stdout.flush()
        except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
            print(f"cue_decode: skipped a datagram: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
