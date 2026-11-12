#!/usr/bin/env python3
"""
cross_check.py - Compare our Python golden model with an OUTSIDE ITCH parser.

Why: tools/gen_itch.py, tools/itch_ref.py and rtl/ were all written from the
same notes (docs/SPEC_NOTES.md). If a field offset was misread there, all
three agree with each other and `make test` still passes. This script runs a
parser we did NOT write - MeatPy (see docs/DECISIONS.md for why that one) -
over the same real NASDAQ bytes, and checks that both parsers decode every
field of every v1 message (S R A F E C X D U) the same way.

The chain of trust is then:
    MeatPy  ==  itch_ref.py        (this script, every field)
    itch_ref.py  ==  Verilog       (tools/compare.py, as in `make test`)

How the two sides are kept independent:
  * Framing: our side splits messages with itch_ref.iter_messages();
    MeatPy uses its own reader (ITCH50MessageReader), which reads the length
    prefixes itself.
  * Decoding: our side uses itch_ref.format_message() (fields by byte
    offset). MeatPy decodes with Python struct format strings. Nothing on
    the MeatPy side below calls any code from itch_ref.py.
  * Only the PRINTING convention is shared (price as whole.ffff, space as
    "_"), because we compare text. That convention is a choice, not a spec
    reading, so sharing it can't hide a misread field.

Must be run with the Python from build/venv (where MeatPy is installed);
`make realdata` takes care of that.

Usage:  python tools/cross_check.py data/real/stream.bin [--report build/real/cross_check.txt]
Exit code 0 if every field matches, 1 otherwise.
"""

import argparse
import collections
import os
import sys

# Our golden model lives next to this file.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import itch_ref                                                  # noqa: E402

from meatpy.itch50 import ITCH50MessageReader                    # noqa: E402

V1 = "SRAFECXDU"
MAX_EXAMPLES = 20      # how many mismatching messages to print in detail


# ---------------------------------------------------------------------------
# Turn a MeatPy message object into the same "name=value" fields that
# itch_ref.format_message() prints.  MeatPy's attribute names are on the
# right; our names are on the left.
# ---------------------------------------------------------------------------
def text(b):
    """Bytes -> our printing convention (space -> '_', non-printable -> '.')."""
    return "".join("_" if c == 0x20 else chr(c) if 0x21 <= c <= 0x7E else "." for c in b)


def px(p):
    """Integer price with 4 implied decimals -> 'whole.ffff'."""
    return f"{p // 10000}.{p % 10000:04d}"


# For each v1 type: list of (our field name, function of the MeatPy message).
MEATPY_FIELDS = {
    "S": [("event", lambda m: text(m.code))],
    "R": [("stock", lambda m: text(m.stock)),
          ("mkt", lambda m: text(m.category)),
          ("fsi", lambda m: text(m.status)),
          ("lot", lambda m: str(m.lotsize)),
          ("rlo", lambda m: text(m.lotsonly)),
          ("cls", lambda m: text(m.issue_class)),
          ("sub", lambda m: text(m.issue_sub)),
          ("auth", lambda m: text(m.authenticity)),
          ("ssti", lambda m: text(m.shortsale_thresh)),
          ("ipo", lambda m: text(m.ipo_flag)),
          ("luld", lambda m: text(m.luld_ref)),
          ("etp", lambda m: text(m.etp_flag)),
          ("lev", lambda m: str(m.etp_leverage)),
          ("inv", lambda m: text(m.inverse_ind))],
    "A": [("ref", lambda m: str(m.order_ref)),
          ("side", lambda m: text(m.bsindicator)),
          ("shares", lambda m: str(m.shares)),
          ("stock", lambda m: text(m.stock)),
          ("price", lambda m: px(m.price))],
    "E": [("ref", lambda m: str(m.order_ref)),
          ("shares", lambda m: str(m.shares)),
          ("match", lambda m: str(m.match))],
    "C": [("ref", lambda m: str(m.order_ref)),
          ("shares", lambda m: str(m.shares)),
          ("match", lambda m: str(m.match)),
          ("printable", lambda m: text(m.printable)),
          ("price", lambda m: px(m.execution_price))],
    "X": [("ref", lambda m: str(m.order_ref)),
          ("shares", lambda m: str(m.canceled_shares))],
    "D": [("ref", lambda m: str(m.order_ref))],
    "U": [("oref", lambda m: str(m.original_ref)),
          ("nref", lambda m: str(m.new_ref)),
          ("shares", lambda m: str(m.shares)),
          ("price", lambda m: px(m.price))],
}
# F is A plus the attribution (MPID) field.
MEATPY_FIELDS["F"] = MEATPY_FIELDS["A"] + [("mpid", lambda m: text(m.attribution))]


def meatpy_fields(m):
    """All comparable fields of a MeatPy message, as an ordered list of (name, text)."""
    letter = type(m).type.decode()
    out = [("type", letter), ("len", str(type(m).message_size))]
    if letter in V1:
        out += [("loc", str(m.stock_locate)), ("trk", str(m.tracking_number)),
                ("ts", str(m.timestamp))]
        out += [(name, get(m)) for name, get in MEATPY_FIELDS[letter]]
    return out


def our_fields(line):
    """Parse one itch_ref output line back into an ordered list of (name, text)."""
    tok = line.split()
    # tok[0]=seq, tok[1]="t=41", tok[2]="len=36", tok[3]="A" or "SKIP", then name=value...
    letter = chr(int(tok[1][2:], 16))
    out = [("type", letter), ("len", tok[2][4:])]
    if tok[3] != "SKIP":
        out += [tuple(t.split("=", 1)) for t in tok[4:]]
    return out


def main():
    ap = argparse.ArgumentParser(description="Cross-check itch_ref.py against MeatPy")
    ap.add_argument("binfile")
    ap.add_argument("--report", help="also write the report to this file")
    args = ap.parse_args()

    report = []
    def say(s=""):
        print(s)
        report.append(s)

    with open(args.binfile, "rb") as f:
        data = f.read()

    # Our side: framing + decoding with the golden model.
    ours = (itch_ref.format_message(i + 1, m)
            for i, m in enumerate(itch_ref.iter_messages(data)))
    # Outside side: MeatPy's own reader does its own framing and decoding.
    theirs = ITCH50MessageReader().read_file(args.binfile)

    counts = collections.Counter()           # per type letter
    field_checks = collections.Counter()     # per type letter: fields compared
    bad_fields = collections.Counter()       # (type, field) -> mismatches
    examples = []
    n = 0
    sentinel = object()
    for our_line, their_msg in zip_longest_strict(ours, theirs, sentinel):
        n += 1
        if our_line is sentinel or their_msg is sentinel:
            say(f"FAIL: message counts differ (one parser stopped at message {n})")
            bad_fields[("?", "count")] += 1
            break
        a = our_fields(our_line)
        b = meatpy_fields(their_msg)
        letter = b[0][1]
        counts[letter] += 1
        if letter in V1:
            field_checks[letter] += len(b)
        # Compare position by position, so a missing or extra field also counts.
        diffs = []
        for i in range(max(len(a), len(b))):
            fa = a[i] if i < len(a) else ("<missing>", "")
            fb = b[i] if i < len(b) else ("<missing>", "")
            if fa != fb:
                diffs.append((fb[0], fa[1], fb[1]))
                bad_fields[(letter, fb[0])] += 1
        if diffs and len(examples) < MAX_EXAMPLES:
            examples.append((n, our_line, diffs))

    # ---- report ----
    say(f"cross_check: {n:,} messages in {args.binfile}")
    say(f"  outside parser: MeatPy (meatpy.itch50.ITCH50MessageReader)")
    say("")
    say("  type  messages  fields compared")
    for letter in sorted(counts, key=lambda k: (k not in V1, V1.find(k), k)):
        kind = f"{field_checks[letter]:>15,}" if letter in V1 else "   type+len only"
        say(f"  {letter:>4}  {counts[letter]:>8,}  {kind}")
    v1_msgs = sum(counts[k] for k in V1)
    say(f"  v1 total: {v1_msgs:,} messages, {sum(field_checks.values()):,} fields")
    say("")
    if not bad_fields:
        say("PASS: every field of every v1 message matches MeatPy; "
            "every other message matches in type and length")
        rc = 0
    else:
        say(f"FAIL: {sum(bad_fields.values()):,} mismatching fields")
        for (letter, field), k in sorted(bad_fields.items()):
            say(f"  {letter} {field}: {k:,} mismatches")
        for seq, line, diffs in examples:
            say(f"  message {seq}: {line}")
            for field, ours_v, theirs_v in diffs:
                say(f"      {field}: itch_ref={ours_v!r}  MeatPy={theirs_v!r}")
        rc = 1

    if args.report:
        os.makedirs(os.path.dirname(args.report) or ".", exist_ok=True)
        with open(args.report, "w", newline="\n") as f:
            f.write("\n".join(report) + "\n")
    return rc


def zip_longest_strict(a, b, fill):
    """Like itertools.zip_longest, but stops right after the first uneven pair."""
    a, b = iter(a), iter(b)
    while True:
        x, y = next(a, fill), next(b, fill)
        if x is fill and y is fill:
            return
        yield x, y
        if x is fill or y is fill:
            return


if __name__ == "__main__":
    sys.exit(main())
