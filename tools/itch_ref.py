#!/usr/bin/env python3
"""
itch_ref.py - Reference ("golden model") parser for NASDAQ TotalView-ITCH 5.0.

Reads a length-prefixed ITCH binary file and writes two text files:

  expected.txt          One line per message with every decoded field.
                        The Verilog testbench writes actual.txt in the
                        exact same format, and compare.py diffs them.
  frames_expected.txt   One line per message with just its sequence number,
                        type byte and length.  Used to check the framer
                        on its own (phase 2).

Design choice: fields are read by EXPLICIT BYTE OFFSET, copied from the
tables in docs/SPEC_NOTES.md, rather than by reusing the generator's struct
formats.  The Verilog decoder works the same way (by offset), so this file
is the most direct software model of the hardware.

Output line format (see docs/DECISIONS.md, "Output format"):

  <seq> t=<type hex> len=<length> SKIP
  <seq> t=<type hex> len=<length> <letter> loc=.. trk=.. ts=.. <type fields>

  - seq counts messages from 1 (zero-length records are not counted)
  - integers are printed in decimal
  - prices are printed as whole.ffff (4 implied decimals)
  - alpha (text) fields are printed character by character, with
    space -> "_" and any non-printable byte -> "."
"""

import argparse
import os
import sys

# Total message length from the spec for each fully-decoded (v1) type.
# A message is decoded only if its length prefix matches this exactly.
V1_LENGTHS = {
    ord("S"): 12, ord("R"): 39, ord("A"): 36, ord("F"): 40, ord("E"): 31,
    ord("C"): 36, ord("X"): 23, ord("D"): 19, ord("U"): 35,
}


# ---------------------------------------------------------------------------
# Small field readers.  `m` is the message bytes (starting at the type byte).
# ---------------------------------------------------------------------------
def u(m, off, n):
    """Unsigned big-endian integer of n bytes starting at offset off."""
    return int.from_bytes(m[off:off + n], "big")


def alpha(m, off, n):
    """Text field -> printable string (space -> '_', non-printable -> '.')."""
    out = ""
    for b in m[off:off + n]:
        if b == 0x20:
            out += "_"
        elif 0x21 <= b <= 0x7E:
            out += chr(b)
        else:
            out += "."
    return out


def price(m, off):
    """4-byte Price(4) -> 'whole.ffff' (e.g. 1234500 -> '123.4500')."""
    p = u(m, off, 4)
    return f"{p // 10000}.{p % 10000:04d}"


# ---------------------------------------------------------------------------
# Per-type field lists.  Offsets come straight from docs/SPEC_NOTES.md.
# ---------------------------------------------------------------------------
def decode_fields(m):
    t = chr(m[0])
    if t == "S":                                   # §1.1
        return [("event", alpha(m, 11, 1))]
    if t == "R":                                   # §1.2.1
        return [
            ("stock", alpha(m, 11, 8)),
            ("mkt", alpha(m, 19, 1)),
            ("fsi", alpha(m, 20, 1)),
            ("lot", u(m, 21, 4)),
            ("rlo", alpha(m, 25, 1)),
            ("cls", alpha(m, 26, 1)),
            ("sub", alpha(m, 27, 2)),
            ("auth", alpha(m, 29, 1)),
            ("ssti", alpha(m, 30, 1)),
            ("ipo", alpha(m, 31, 1)),
            ("luld", alpha(m, 32, 1)),
            ("etp", alpha(m, 33, 1)),
            ("lev", u(m, 34, 4)),
            ("inv", alpha(m, 38, 1)),
        ]
    if t in ("A", "F"):                            # §1.3.1 / §1.3.2
        f = [
            ("ref", u(m, 11, 8)),
            ("side", alpha(m, 19, 1)),
            ("shares", u(m, 20, 4)),
            ("stock", alpha(m, 24, 8)),
            ("price", price(m, 32)),
        ]
        if t == "F":
            f.append(("mpid", alpha(m, 36, 4)))
        return f
    if t == "E":                                   # §1.4.1
        return [("ref", u(m, 11, 8)), ("shares", u(m, 19, 4)), ("match", u(m, 23, 8))]
    if t == "C":                                   # §1.4.2
        return [
            ("ref", u(m, 11, 8)),
            ("shares", u(m, 19, 4)),
            ("match", u(m, 23, 8)),
            ("printable", alpha(m, 31, 1)),
            ("price", price(m, 32)),
        ]
    if t == "X":                                   # §1.4.3
        return [("ref", u(m, 11, 8)), ("shares", u(m, 19, 4))]
    if t == "D":                                   # §1.4.4
        return [("ref", u(m, 11, 8))]
    if t == "U":                                   # §1.4.5
        return [
            ("oref", u(m, 11, 8)),
            ("nref", u(m, 19, 8)),
            ("shares", u(m, 27, 4)),
            ("price", price(m, 31)),
        ]
    raise ValueError(f"decode_fields called on unsupported type {t!r}")


def format_message(seq, m):
    """Build the one-line text form of message `m` (see module docstring)."""
    t, n = m[0], len(m)
    line = f"{seq} t={t:02x} len={n}"
    if V1_LENGTHS.get(t) != n:
        return line + " SKIP"
    # Common header, offsets 0..10 in every message.
    line += f" {chr(t)} loc={u(m, 1, 2)} trk={u(m, 3, 2)} ts={u(m, 5, 6)}"
    for name, value in decode_fields(m):
        line += f" {name}={value}"
    return line


def iter_messages(data):
    """Yield each message (without its length prefix) from a framed byte stream."""
    pos = 0
    while pos < len(data):
        if pos + 2 > len(data):
            raise ValueError(f"truncated length prefix at byte {pos}")
        length = int.from_bytes(data[pos:pos + 2], "big")
        pos += 2
        if pos + length > len(data):
            raise ValueError(f"message at byte {pos} claims {length} bytes, file ends first")
        if length > 0:                 # zero-length record: nothing to report
            yield data[pos:pos + length]
        pos += length


def main():
    ap = argparse.ArgumentParser(description="ITCH 5.0 reference parser")
    ap.add_argument("binfile")
    ap.add_argument("--out-dir", default="build")
    args = ap.parse_args()

    with open(args.binfile, "rb") as f:
        data = f.read()

    os.makedirs(args.out_dir, exist_ok=True)
    counts = {}
    seq = 0
    with open(os.path.join(args.out_dir, "expected.txt"), "w", newline="\n") as exp, \
         open(os.path.join(args.out_dir, "frames_expected.txt"), "w", newline="\n") as frm:
        for m in iter_messages(data):
            seq += 1
            exp.write(format_message(seq, m) + "\n")
            frm.write(f"{seq} t={m[0]:02x} len={len(m)}\n")
            key = chr(m[0]) if V1_LENGTHS.get(m[0]) == len(m) else "skipped"
            counts[key] = counts.get(key, 0) + 1

    summary = " ".join(f"{k}={counts[k]}" for k in sorted(counts))
    print(f"itch_ref: {seq} messages -> {args.out_dir}/expected.txt ({summary})")


if __name__ == "__main__":
    sys.exit(main())
