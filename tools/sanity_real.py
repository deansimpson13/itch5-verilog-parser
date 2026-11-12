#!/usr/bin/env python3
"""
sanity_real.py - Consistency checks on real ITCH data that don't depend on
any parser being "right".

cross_check.py asks "do two parsers agree?". This script asks a different
question: "do the decoded values make sense TOGETHER?" If a field were read
from the wrong bytes, these relationships would break even if every parser
made the same mistake:

  1. Order lifecycle. Every E/C/X/D/U refers to an order reference that an
     earlier A/F/U created and that hasn't been deleted yet. The order's
     stock locate must match, and executions/cancels can't take more shares
     than the order has left.
       - In the "start" window (which begins at the first message of the
         day) every reference should be found. (Measured: 0 misses.)
       - In the "open" window (which begins mid-day) many orders were added
         before the window started, so misses are expected. We REPORT the
         miss rate and don't fail on it.
  2. Time. Timestamps never go backwards within a window, and they're all
     before midnight (86,400 seconds).
  3. Symbols. Each A/F carries both a stock locate code and the stock
     symbol. The symbol must match the one the Stock Directory (R) message
     assigned to that locate code at the start of the day.
  4. Plausible ranges. Prices, share counts, and one-letter code fields
     must fall inside sensible ranges / the value lists from the spec.

The windows are checked separately (the second one starts mid-day, so time
jumps forward between them). The Stock Directory comes from the start
window, because NASDAQ sends all R messages before 04:00 and locate codes
don't change during the day.

Usage:  python tools/sanity_real.py data/real [--report build/real/sanity.txt]
Exit code 0 if no hard check fails, 1 otherwise.
"""

import argparse
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from itch_ref import iter_messages, decode_fields, u, V1_LENGTHS   # noqa: E402

DAY_NS = 86_400 * 1_000_000_000
MAX_PRICE = 200_000 * 10_000        # $200,000.0000, the largest Price(4) NASDAQ allows
MAX_SHARES = 10_000_000             # one order for more than 10M shares would be absurd

# Allowed values for one-letter code fields, copied from the value tables in
# the ITCH 5.0 spec (§1.1 for S, §1.2.1 pp. 6-8 for R, §1.3/§1.4 for the rest).
# "_" is how our output prints a space, which the spec uses for "not available".
ALLOWED = {
    "event": set("OSQMEC"),                 # S  System Event Code
    "side": set("BS"),                      # A/F Buy/Sell Indicator
    "printable": set("YN"),                 # C  Printable
    "mkt": set("QGSNAPZV_"),                # R  Market Category
    "fsi": set("DEQSGHJKCN_"),              # R  Financial Status Indicator
    "rlo": set("YN"),                       # R  Round Lots Only
    "auth": set("PT"),                      # R  Authenticity (Production / Test)
    "ssti": set("YN_"),                     # R  Short Sale Threshold Indicator
    "ipo": set("YN_"),                      # R  IPO Flag
    "luld": set("12_"),                     # R  LULD Reference Price Tier
    "etp": set("YN_"),                      # R  ETP Flag
    "inv": set("YN"),                       # R  Inverse Indicator
}


def price_int(s):
    """Our printed 'whole.ffff' back to an integer with 4 implied decimals."""
    whole, frac = s.split(".")
    return int(whole) * 10_000 + int(frac)


def decoded(m):
    """(letter, header dict + field dict) for a v1 message, or None for others."""
    if V1_LENGTHS.get(m[0]) != len(m):
        return None
    f = dict(decode_fields(m))
    f["loc"], f["ts"] = u(m, 1, 2), u(m, 5, 6)
    return chr(m[0]), f


def read_directory(data):
    """locate code -> stock symbol, from every R message in the data."""
    directory = {}
    for m in iter_messages(data):
        d = decoded(m)
        if d and d[0] == "R":
            directory[d[1]["loc"]] = d[1]["stock"]
    return directory


class Checker:
    """Runs every check over one window and remembers the results."""

    def __init__(self, name, directory):
        self.name = name
        self.directory = directory
        self.hard = collections.Counter()     # failures: must be 0
        self.soft = collections.Counter()     # just reported
        self.examples = {}                    # first example of each failure kind
        self.live = {}                        # order ref -> [locate, shares left]
        self.n = 0
        self.refs_checked = 0
        self.last_ts = -1
        self.first_ts = None
        self.max_seen = collections.Counter() # largest shares/price seen, for the report

    def fail(self, kind, detail):
        self.hard[kind] += 1
        self.examples.setdefault(kind, detail)

    # ---- order-reference helpers ----
    def lookup(self, ref, loc, what):
        """Find a live order; check its locate. Returns the entry or None (a miss)."""
        self.refs_checked += 1
        entry = self.live.get(ref)
        if entry is None:
            self.soft["unknown order ref"] += 1
            return None
        if entry[0] != loc:
            self.fail("order's stock locate changed", f"{what}: ref {ref} added with "
                      f"locate {entry[0]}, now locate {loc}")
        return entry

    def take(self, entry, ref, shares, what):
        """An execution or cancel removes `shares` from a live order."""
        if shares < 1:
            self.fail("zero shares executed/cancelled", f"{what} ref {ref}")
        if entry is None:
            return
        if shares > entry[1]:
            self.fail("more shares than the order had left",
                      f"{what} ref {ref}: {shares} > {entry[1]}")
        entry[1] -= shares
        if entry[1] <= 0:
            del self.live[ref]                # fully executed / cancelled

    def add(self, ref, loc, shares, what):
        if ref in self.live:
            self.fail("order ref added twice", f"{what} ref {ref}")
        self.live[ref] = [loc, shares]

    # ---- the per-message checks ----
    def check(self, m):
        self.n += 1
        if len(m) >= 11:
            ts = u(m, 5, 6)
            if self.first_ts is None:
                self.first_ts = ts
            if ts < self.last_ts:
                self.fail("timestamp went backwards",
                          f"message {self.n}: {ts} after {self.last_ts}")
            if ts >= DAY_NS:
                self.fail("timestamp past midnight", f"message {self.n}: {ts}")
            self.last_ts = max(self.last_ts, ts)

        d = decoded(m)
        if d is None:
            return
        t, f = d
        tag = f"msg {self.n} {t}"

        # Code fields must be values the spec defines.
        for field, allowed in ALLOWED.items():
            if field in f and not set(f[field]) <= allowed:
                self.fail(f"bad {field} code", f"{tag}: {field}={f[field]}")

        if t in "AFU":
            p = price_int(f["price"])
            self.max_seen["price"] = max(self.max_seen["price"], p)
            if not 0 < p <= MAX_PRICE:
                self.fail("price out of range", f"{tag}: price={f['price']}")
        if t in "AFECXU":
            self.max_seen["shares"] = max(self.max_seen["shares"], f["shares"])
            if f["shares"] > MAX_SHARES:
                self.fail("shares out of range", f"{tag}: shares={f['shares']}")
        if t == "C":
            p = price_int(f["price"])
            if not 0 < p <= MAX_PRICE:
                self.fail("price out of range", f"{tag}: price={f['price']}")

        if t in "AF":
            if f["shares"] < 1:
                self.fail("order for zero shares", tag)
            sym = self.directory.get(f["loc"])
            if sym is None:
                self.fail("locate code not in Stock Directory", f"{tag}: loc={f['loc']}")
            elif sym != f["stock"]:
                self.fail("symbol doesn't match Stock Directory",
                          f"{tag}: loc={f['loc']} is {sym}, message says {f['stock']}")
            self.add(f["ref"], f["loc"], f["shares"], tag)
        elif t in "EC":
            self.take(self.lookup(f["ref"], f["loc"], tag), f["ref"], f["shares"], tag)
        elif t == "X":
            self.take(self.lookup(f["ref"], f["loc"], tag), f["ref"], f["shares"], tag)
        elif t == "D":
            if self.lookup(f["ref"], f["loc"], tag) is not None:
                del self.live[f["ref"]]
        elif t == "U":
            # Replace = delete the old order, add a new one (new ref, shares, price)
            # on the same stock.
            if self.lookup(f["oref"], f["loc"], tag) is not None:
                del self.live[f["oref"]]
            self.add(f["nref"], f["loc"], f["shares"], tag)


def report(c, say, expect_all_refs):
    hrs = lambda ns: f"{ns // 3_600_000_000_000:02d}:{ns // 60_000_000_000 % 60:02d}:" \
                     f"{ns / 1e9 % 60:06.3f}"
    say(f"[{c.name}] {c.n:,} messages, {hrs(c.first_ts)} to {hrs(c.last_ts)}")
    miss = c.soft["unknown order ref"]
    rate = 100.0 * miss / c.refs_checked if c.refs_checked else 0.0
    note = "(window starts with the day's first message, so 0 expected)" if expect_all_refs else \
           "(window starts mid-day: orders added earlier are unknown, so misses are expected)"
    say(f"  order refs looked up: {c.refs_checked:,}, not found: {miss:,} = {rate:.2f}% {note}")
    say(f"  orders still open at end of window: {len(c.live):,}")
    say(f"  largest shares seen: {c.max_seen['shares']:,}; largest price: "
        f"{c.max_seen['price'] // 10000}.{c.max_seen['price'] % 10000:04d}")
    if c.hard:
        for kind, k in sorted(c.hard.items()):
            say(f"  FAIL {kind}: {k:,}  (first: {c.examples[kind]})")
    else:
        say("  all hard checks pass: time order, symbol/locate, order lifecycle, ranges, codes")


def main():
    ap = argparse.ArgumentParser(description="Sanity checks on real ITCH data")
    ap.add_argument("datadir", help="folder with seg_start.bin and seg_open.bin")
    ap.add_argument("--report")
    args = ap.parse_args()

    lines = []
    def say(s=""):
        print(s)
        lines.append(s)

    seg = {}
    for name in ("start", "open"):
        with open(os.path.join(args.datadir, f"seg_{name}.bin"), "rb") as f:
            seg[name] = f.read()

    directory = read_directory(seg["start"])
    say(f"sanity_real: Stock Directory has {len(directory):,} locate codes "
        f"(from R messages in the start window)")
    rc = 0
    for name in ("start", "open"):
        c = Checker(name, directory)
        for m in iter_messages(seg[name]):
            c.check(m)
        report(c, say, expect_all_refs=(name == "start"))
        if c.hard:
            rc = 1
    say("PASS: sanity checks" if rc == 0 else "FAIL: sanity checks")

    if args.report:
        os.makedirs(os.path.dirname(args.report) or ".", exist_ok=True)
        with open(args.report, "w", newline="\n") as f:
            f.write("\n".join(lines) + "\n")
    return rc


if __name__ == "__main__":
    sys.exit(main())
