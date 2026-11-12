#!/usr/bin/env python3
"""
fetch_real.py - Cut a small, repeatable slice out of a real NASDAQ ITCH 5.0 file.

NASDAQ publishes full trading days of ITCH 5.0 as gzipped files of 4-17 GB.
We do NOT download a whole file. Instead we:

  1. open an HTTP connection and read the gzip file a chunk at a time,
  2. decompress each chunk as it arrives (zlib can do this incrementally),
  3. walk the 2-byte length prefixes to find clean message boundaries,
  4. keep the two windows we want, then close the connection.

Only the first ~135 MB of the 4.7 GB file are ever downloaded.

Why two windows?  (Measured on 01302019.NASDAQ_ITCH50.gz; see docs/DECISIONS.md.)

  "start"  The first START_MSGS messages of the day. They hold the whole
           Stock Directory (8,713 R messages), the opening System Events,
           and the first pre-market orders. Because this window begins at
           the very start of the day, every order it references was added
           inside the window, so the order-reference sanity check works.
  "open"   OPEN_MSGS messages starting at the first message stamped 09:30:00
           or later. Before the market opens there are almost no executions:
           in the first 2 million messages there is not a single C
           (Order Executed With Price), and only 49 F. After 09:30 there are
           plenty of all of them.

Output, all in data/real/ (gitignored, never committed):

  seg_start.bin, seg_open.bin  each window on its own (for the sanity checks)
  stream.bin                   both windows back to back (for the parsers)
  stream.hex, stream_len.vh    the same bytes in the format tb_itch.v reads
                               (identical layout to tools/gen_itch.py output)
  manifest.json                where the bytes came from, sizes, SHA-256s

Re-running is cheap: if the files already exist and their SHA-256 matches
the pinned value below, nothing is downloaded. Use --force to re-fetch.

Usage:  python tools/fetch_real.py [--out-dir data/real] [--force]
"""

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.request
import zlib

# ---------------------------------------------------------------------------
# What to fetch. Changing any of these changes the slice, so the pinned
# hashes below only apply to these exact values.
# ---------------------------------------------------------------------------
URL = "https://emi.nasdaq.com/ITCH/Nasdaq%20ITCH/01302019.NASDAQ_ITCH50.gz"
START_MSGS = 300_000           # messages kept from the start of the day
OPEN_MSGS = 200_000            # messages kept from 09:30:00 onward
OPEN_NS = (9 * 3600 + 30 * 60) * 1_000_000_000   # 09:30:00 in ns since midnight

# Hard limit on how much we are willing to decompress while looking for
# 09:30. The open is ~10.5 million messages (~300 MB decompressed) into this
# file; the cap just makes sure a surprise can't fill the disk or run forever.
MAX_DECOMPRESSED = 1_500_000_000

# SHA-256 of the files this script produced the first time it was run
# (2026-11-11). If NASDAQ ever changed the file, or a download got corrupted,
# the hashes would not match and the script stops instead of silently testing
# against different data.
PINNED_SHA256 = {
    "seg_start.bin": "2a0120e60fa692766f8ad8250f4698d7c7bdf676063268829b960d5dd7b13957",
    "seg_open.bin": "cffbfabab834626396a37b4a73bee81b7a32de6074cdb59d69e545184fabd14c",
}

CHUNK = 1 << 20                # read 1 MB of compressed data at a time


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def stream_messages(url):
    """
    Yield (message_bytes_including_length_prefix, timestamp_ns) for every
    message in the remote gzip file, in order, downloading lazily.

    The caller stops iterating when it has enough; the `with` block then
    closes the HTTP connection, so the rest of the file is never downloaded.
    """
    # A plain, generic User-Agent; some servers reject Python's default.
    req = urllib.request.Request(url, headers={"User-Agent": "itch-sample-fetch/1.0"})
    # wbits=31 tells zlib "this is gzip format" (16 + 15-bit window).
    decomp = zlib.decompressobj(wbits=31)
    buf = bytearray()          # decompressed bytes not yet split into messages
    pos = 0                    # where the next unread message starts in buf
    total_out = 0              # decompressed bytes so far (for the cap)

    with urllib.request.urlopen(req, timeout=60) as resp:
        while True:
            chunk = resp.read(CHUNK)
            if not chunk:
                return                      # end of file
            out = decomp.decompress(chunk)
            # A .gz file may contain several gzip "members" back to back.
            # When one ends, start a fresh decompressor on the leftover bytes.
            while decomp.eof and decomp.unused_data:
                rest = decomp.unused_data
                decomp = zlib.decompressobj(wbits=31)
                out += decomp.decompress(rest)
            buf += out
            total_out += len(out)
            if total_out > MAX_DECOMPRESSED:
                raise RuntimeError(f"decompressed more than {MAX_DECOMPRESSED} bytes "
                                   "without finishing; giving up")

            # Peel off every COMPLETE message now in the buffer. A message
            # is complete when we have its 2 length bytes AND that many more.
            while pos + 2 <= len(buf):
                length = int.from_bytes(buf[pos:pos + 2], "big")
                end = pos + 2 + length
                if end > len(buf):
                    break                   # rest of this message is in a later chunk
                # Timestamp = bytes 5..10 of the message body (after the
                # 2-byte prefix): 6-byte big-endian nanoseconds since midnight.
                ts = int.from_bytes(buf[pos + 7:pos + 13], "big") if length >= 11 else None
                yield bytes(buf[pos:end]), ts
                pos = end

            # Throw away bytes we've already handed out so memory stays small.
            if pos > (16 << 20):
                del buf[:pos]
                pos = 0


def fetch(url):
    """Download just enough of the file to build both windows."""
    start, opening = [], []
    seen = 0                   # messages read from the day so far
    open_index = None          # 1-based index (in the day) of the first 09:30 message
    t0 = time.time()
    for msg, ts in stream_messages(url):
        seen += 1
        if seen <= START_MSGS:
            start.append(msg)
        elif open_index is None and ts is not None and ts >= OPEN_NS:
            open_index = seen
            print(f"fetch_real: reached 09:30:00 at message {seen:,} of the day")
        if open_index is not None:
            opening.append(msg)
            if len(opening) == OPEN_MSGS:
                break                       # got everything: stop downloading
        if seen % 1_000_000 == 0:
            print(f"fetch_real: scanned {seen:,} messages ({time.time() - t0:.0f}s)", flush=True)
    if len(start) < START_MSGS or len(opening) < OPEN_MSGS:
        raise RuntimeError("file ended before both windows were filled")
    return b"".join(start), b"".join(opening), open_index


def write(path, data, text=False):
    mode = "w" if text else "wb"
    kw = {"newline": "\n"} if text else {}
    with open(path, mode, **kw) as f:
        f.write(data)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--out-dir", default="data/real")
    ap.add_argument("--force", action="store_true", help="re-download even if cached")
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)
    p = lambda name: os.path.join(args.out_dir, name)

    # ---- 1. Use the cached copy if it's there and still matches the pins ----
    outputs = ["seg_start.bin", "seg_open.bin", "stream.bin", "stream.hex",
               "stream_len.vh", "manifest.json"]
    cached = (not args.force and all(PINNED_SHA256.values())
              and all(os.path.exists(p(n)) for n in outputs))
    if cached:
        segs = {}
        for n in PINNED_SHA256:
            with open(p(n), "rb") as f:
                segs[n] = f.read()
        if all(sha256(segs[n]) == PINNED_SHA256[n] for n in segs):
            # Refresh the files' modification times. Make decides what to
            # rebuild by comparing times; without this, editing this script
            # would make every later `make realdata` redo the simulation.
            for n in outputs:
                os.utime(p(n))
            print(f"fetch_real: using cached slice in {args.out_dir}/ (hashes match)")
            return 0
        print("fetch_real: cached files don't match pinned hashes, re-downloading")

    # ---- 2. Stream the start of the remote file ----
    print(f"fetch_real: streaming {URL}")
    seg_start, seg_open, open_index = fetch(URL)

    # ---- 3. Check we got exactly the bytes we got last time ----
    got = {"seg_start.bin": sha256(seg_start), "seg_open.bin": sha256(seg_open)}
    for name, h in got.items():
        pin = PINNED_SHA256[name]
        if pin is None:
            print(f"fetch_real: NOTE no pinned hash for {name} yet; it is {h}")
        elif pin != h:
            print(f"fetch_real: FAIL {name} SHA-256 {h} != pinned {pin}")
            return 1

    # ---- 4. Write everything out ----
    stream = seg_start + seg_open
    write(p("seg_start.bin"), seg_start)
    write(p("seg_open.bin"), seg_open)
    write(p("stream.bin"), stream)
    # Same formats as tools/gen_itch.py, so tb_itch.v reads them unchanged.
    write(p("stream.hex"), "".join(f"{b:02x}\n" for b in stream), text=True)
    write(p("stream_len.vh"), "// Generated by tools/fetch_real.py - do not edit.\n"
                              f"`define STREAM_LEN {len(stream)}\n", text=True)
    manifest = {
        "url": URL,
        "start_msgs": START_MSGS,
        "open_msgs": OPEN_MSGS,
        "open_window_starts_at_day_message": open_index,
        "bytes": {"seg_start.bin": len(seg_start), "seg_open.bin": len(seg_open),
                  "stream.bin": len(stream)},
        "sha256": got,
    }
    write(p("manifest.json"), json.dumps(manifest, indent=2) + "\n", text=True)
    print(f"fetch_real: wrote {START_MSGS + OPEN_MSGS:,} messages, {len(stream):,} bytes "
          f"to {args.out_dir}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
