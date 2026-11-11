#!/usr/bin/env python3
"""
gen_itch.py - Build a synthetic, spec-correct NASDAQ TotalView-ITCH 5.0 byte stream.

Outputs (all in --out-dir, default "data/"):
    stream.bin     The raw binary file: [2-byte big-endian length][message] repeated.
    stream.hex     The same bytes, one byte per line in hex, for Verilog's $readmemh.
    stream_len.vh  A tiny Verilog header: `define STREAM_LEN <number of bytes>
                   The testbench includes it so it knows how big its memory must be.

Why synthetic data?  Real NASDAQ sample files are several GB.  A small
generated file is fast to simulate, and because it is built with a fixed
random seed, every run produces exactly the same bytes (reproducible tests).

How messages are built
----------------------
Each message type is described as a Python `struct` format string listing the
fields *in spec order* (see docs/SPEC_NOTES.md).  `struct.pack` lays the
fields out back to back, so the offsets come out right automatically.
The reference parser (itch_ref.py) does NOT reuse these formats: it reads
fields by explicit byte offset instead.  Two independent descriptions of the
same layout means a typo in one of them shows up as a test failure.

struct cheat sheet (">" means big-endian, no padding):
    c  = 1 byte (a single character)      H = 2-byte unsigned int
    I  = 4-byte unsigned int              Q = 8-byte unsigned int
    8s = 8-byte string (bytes)
"""

import argparse
import os
import random
import struct

SEED = 42                     # fixed seed => identical output every run
NUM_RANDOM_MESSAGES = 2000    # messages in the random middle section

# Largest legal Price(4) per spec "Data Types": 200,000.0000 = 0x77359400
MAX_PRICE = 0x77359400
# Last nanosecond of the day (24h * 3600 s * 1e9 ns - 1)
MAX_TIMESTAMP = 24 * 3600 * 1_000_000_000 - 1

# ---------------------------------------------------------------------------
# Body formats: the fields AFTER the 11-byte common header
# (type 1, stock locate 2, tracking number 2, timestamp 6).
# Each entry: type letter -> (struct format, total message length from spec)
# ---------------------------------------------------------------------------
BODY_FORMATS = {
    # ---- v1 types (decoded) ----
    b"S": (">c", 12),                              # §1.1   event code
    b"R": (">8sccIcc2scccccIc", 39),               # §1.2.1 stock directory
    b"A": (">QcI8sI", 36),                         # §1.3.1 add order
    b"F": (">QcI8sI4s", 40),                       # §1.3.2 add order w/ MPID
    b"E": (">QIQ", 31),                            # §1.4.1 executed
    b"C": (">QIQcI", 36),                          # §1.4.2 executed w/ price
    b"X": (">QI", 23),                             # §1.4.3 cancel
    b"D": (">Q", 19),                              # §1.4.4 delete
    b"U": (">QQII", 35),                           # §1.4.5 replace
    # ---- other spec types (parsers must skip these) ----
    b"H": (">8scc4s", 25),                         # §1.2.2 trading action
    b"Y": (">8sc", 20),                            # §1.2.3 Reg SHO
    b"L": (">4s8sccc", 26),                        # §1.2.4 MP position
    b"V": (">QQQ", 35),                            # §1.2.5.1 MWCB decline
    b"W": (">c", 12),                              # §1.2.5.2 MWCB status
    b"K": (">8sIcI", 28),                          # §1.2.6 IPO quoting
    b"J": (">8sIIII", 35),                         # §1.2.7 LULD collar
    b"h": (">8scc", 21),                           # §1.2.8 operational halt
    b"P": (">QcI8sIQ", 44),                        # §1.5.1 trade
    b"Q": (">Q8sIQc", 40),                         # §1.5.2 cross trade
    b"B": (">Q", 19),                              # §1.5.3 broken trade
    b"I": (">QQc8sIIIcc", 50),                     # §1.6   NOII
    b"N": (">8sc", 20),                            # §1.7   RPII
    b"O": (">8scIIIQII", 48),                      # §1.8   DLCR
}

SKIP_TYPES = [b"H", b"Y", b"L", b"V", b"W", b"K", b"J", b"h",
              b"P", b"Q", b"B", b"I", b"N", b"O"]

# Types that do not exist in ITCH 5.0, used to test "unknown type" handling.
UNKNOWN_TYPES = [b"Z", b"T", b"G", b"\x00", b"\xff"]

SYMBOLS = [b"AAPL", b"MSFT", b"NVDA", b"AMZN", b"GOOGL", b"META", b"TSLA",
           b"INTC", b"AMD", b"QQQ", b"SPY", b"ZVZZT", b"BRK.A", b"F", b"T"]


def self_check():
    """Make sure every struct format adds up to the length in the spec."""
    for t, (fmt, total) in BODY_FORMATS.items():
        got = 11 + struct.calcsize(fmt)
        assert got == total, f"format for {t!r} gives {got} bytes, spec says {total}"


def pad8(sym: bytes) -> bytes:
    """Alpha fields are left-justified and right-padded with spaces."""
    return sym.ljust(8, b" ")


class Generator:
    def __init__(self, seed):
        self.rng = random.Random(seed)
        self.ts = 3 * 3600 * 1_000_000_000      # start the day at 03:00:00
        self.tracking = 0
        self.next_order_ref = 1
        self.next_match = 1
        self.live_orders = {}   # order_ref -> (locate, shares, price)
        self.locate_of = {sym: i + 1 for i, sym in enumerate(SYMBOLS)}

    # -- helpers -------------------------------------------------------------
    def header(self, mtype: bytes, locate: int, ts=None) -> bytes:
        """11-byte common header: type, stock locate, tracking number, timestamp."""
        if ts is None:
            self.ts += self.rng.randint(1, 50_000)        # time only moves forward
        else:
            self.ts = ts                                  # caller picked an exact time
        self.tracking = (self.tracking + 1) & 0xFFFF
        return mtype + struct.pack(">HH", locate, self.tracking) + self.ts.to_bytes(6, "big")

    def rand_price(self):
        return self.rng.randint(1, 5000) * 100            # $0.01 .. $50.00 in 1/10000 units

    def letter(self, choices: str) -> bytes:
        return self.rng.choice(choices).encode()

    def sym(self):
        s = self.rng.choice(SYMBOLS)
        return s, self.locate_of[s]

    def frame(self, msg: bytes) -> bytes:
        """Put the 2-byte big-endian length prefix in front of a message."""
        return struct.pack(">H", len(msg)) + msg

    # -- v1 message builders -------------------------------------------------
    def msg_S(self, code, ts=None):
        return self.header(b"S", 0, ts) + struct.pack(">c", code)

    def msg_R(self, sym):
        body = struct.pack(
            BODY_FORMATS[b"R"][0],
            pad8(sym),
            self.letter("QGSNAPZV "),       # market category
            self.letter("DEQSGHJKCN "),     # financial status
            self.rng.choice([1, 10, 100]),  # round lot size
            self.letter("YN"),              # round lots only
            self.letter("ACEFILMORSUW"),    # issue classification
            self.rng.choice([b"Z ", b"A ", b"CS", b"ET", b"PF"]),  # sub-type (2 chars)
            self.letter("PT"),              # authenticity
            self.letter("YN "),             # short sale threshold
            self.letter("YN "),             # IPO flag
            self.letter("12 "),             # LULD tier
            self.letter("YN "),             # ETP flag
            self.rng.choice([0, 1, 2, 3]),  # ETP leverage factor
            self.letter("YN"),              # inverse
        )
        return self.header(b"R", self.locate_of[sym]) + body

    def msg_A(self, mpid=None, ref=None, shares=None, price=None, side=None, sym=None):
        if sym is None:
            sym, loc = self.sym()
        else:
            loc = self.locate_of.get(sym, 1)
        if ref is None:                  # normal case: take the next free number
            ref = self.next_order_ref
            self.next_order_ref += 1
        shares = self.rng.randint(1, 5000) if shares is None else shares
        price = self.rand_price() if price is None else price
        side = self.letter("BS") if side is None else side
        self.live_orders[ref] = (loc, shares, price)
        if mpid is None:
            return self.header(b"A", loc) + struct.pack(">QcI8sI", ref, side, shares, pad8(sym), price)
        return self.header(b"F", loc) + struct.pack(">QcI8sI4s", ref, side, shares, pad8(sym), price, mpid)

    def pick_order(self):
        """Pick a live order to modify, or None if the book is empty."""
        if not self.live_orders:
            return None
        ref = self.rng.choice(list(self.live_orders))
        return ref, self.live_orders[ref]

    def msg_E(self, ref, loc, shares):
        m = self.next_match; self.next_match += 1
        return self.header(b"E", loc) + struct.pack(">QIQ", ref, shares, m)

    def msg_C(self, ref, loc, shares, price):
        m = self.next_match; self.next_match += 1
        return self.header(b"C", loc) + struct.pack(">QIQcI", ref, shares, m, self.letter("YN"), price)

    def msg_X(self, ref, loc, shares):
        return self.header(b"X", loc) + struct.pack(">QI", ref, shares)

    def msg_D(self, ref, loc):
        self.live_orders.pop(ref, None)
        return self.header(b"D", loc) + struct.pack(">Q", ref)

    def msg_U(self, ref, loc):
        new_ref = self.next_order_ref; self.next_order_ref += 1
        shares, price = self.rng.randint(1, 5000), self.rand_price()
        self.live_orders.pop(ref, None)
        self.live_orders[new_ref] = (loc, shares, price)
        return self.header(b"U", loc) + struct.pack(">QQII", ref, new_ref, shares, price)

    # -- skip-type builder: correct length + layout, random-ish contents ------
    def msg_skip(self, t):
        fmt, _ = BODY_FORMATS[t]
        values = []
        # Walk the format string and invent a value for each field.
        i = 1  # skip the leading ">"
        while i < len(fmt):
            n = ""
            while fmt[i].isdigit():
                n += fmt[i]; i += 1
            code = fmt[i]; i += 1
            if code == "c":
                values.append(self.letter("ABCDEFGHNOPQSTY12 "))
            elif code == "s":
                values.append(pad8(self.rng.choice(SYMBOLS))[: int(n)])
            elif code == "I":
                values.append(self.rng.randint(0, MAX_PRICE))
            elif code == "Q":
                values.append(self.rng.getrandbits(40))
        _, loc = self.sym()
        return self.header(t, loc) + struct.pack(fmt, *values)

    # -- one random message from the weighted mix ----------------------------
    def random_message(self):
        r = self.rng.random()
        picked = self.pick_order()
        if r < 0.25 or picked is None:
            return self.msg_A()
        if r < 0.30:
            return self.msg_A(mpid=self.rng.choice([b"GSCO", b"MSCO", b"NSDQ", b"CDRG"]))
        ref, (loc, shares, price) = picked
        if r < 0.38:
            return self.msg_E(ref, loc, self.rng.randint(1, shares))
        if r < 0.42:
            return self.msg_C(ref, loc, self.rng.randint(1, shares), self.rand_price())
        if r < 0.47:
            return self.msg_X(ref, loc, self.rng.randint(1, shares))
        if r < 0.57:
            return self.msg_D(ref, loc)
        if r < 0.63:
            return self.msg_U(ref, loc)
        if r < 0.99:
            return self.msg_skip(self.rng.choice(SKIP_TYPES))
        # ~1%: a message type that does not exist in ITCH 5.0, random length 1..60
        t = self.rng.choice(UNKNOWN_TYPES)
        return t + bytes(self.rng.getrandbits(8) for _ in range(self.rng.randint(0, 59)))

    # -- hand-made edge cases -------------------------------------------------
    def edge_cases(self):
        """Return a list of already-FRAMED byte strings that poke at corner cases."""
        out = []
        # 1. Extreme field values in an Add Order (all 0xFF ref, max price, max shares).
        out.append(self.frame(self.msg_A(ref=0xFFFF_FFFF_FFFF_FFFF, shares=0xFFFF_FFFF,
                                         price=MAX_PRICE, side=b"S", sym=b"ZVZZT")))
        # 2. Zero price / small values.
        out.append(self.frame(self.msg_A(shares=1, price=0, side=b"B", sym=b"F")))
        # 3. Unknown type with only the type byte (length 1).
        out.append(self.frame(b"Z"))
        # 4. Unknown type longer than 255 bytes, so the length's high byte is non-zero.
        out.append(self.frame(b"T" + bytes(range(256)) + b"\x00" * 43))   # 300 bytes total
        # 5. A supported type ("A") whose length prefix is wrong (truncated to 20 bytes).
        #    Both parsers must treat it as SKIP, not decode garbage.
        out.append(self.frame(self.msg_A()[:20]))
        # 6. A supported type with extra trailing bytes (prefix says 40, A is 36).
        out.append(self.frame(self.msg_A() + b"\xAA\xBB\xCC\xDD"))
        # 7. A zero-length record (just "00 00"); both parsers ignore it.
        out.append(b"\x00\x00")
        # 8. A run of 20 back-to-back tiny messages (Order Delete = 19 bytes).
        for _ in range(20):
            p = self.pick_order()
            if p:
                ref, (loc, _, _) = p
                out.append(self.frame(self.msg_D(ref, loc)))
        return out

    def build_stream(self, n_random):
        frames = []
        frames.append(self.frame(self.msg_S(b"O")))             # start of messages
        for sym in SYMBOLS:                                     # stock directory spin
            frames.append(self.frame(self.msg_R(sym)))
        frames.append(self.frame(self.msg_S(b"S")))             # start of system hours
        frames.append(self.frame(self.msg_S(b"Q")))             # start of market hours
        for _ in range(n_random):
            frames.append(self.frame(self.random_message()))
        frames.extend(self.edge_cases())
        frames.append(self.frame(self.msg_S(b"M")))             # end of market hours
        frames.append(self.frame(self.msg_S(b"E")))             # end of system hours
        # End of messages, stamped at the last nanosecond of the day (edge case:
        # the largest timestamp a real feed can carry).
        frames.append(self.frame(self.msg_S(b"C", ts=MAX_TIMESTAMP)))
        return b"".join(frames)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--out-dir", default="data")
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--count", type=int, default=NUM_RANDOM_MESSAGES)
    args = ap.parse_args()

    self_check()
    data = Generator(args.seed).build_stream(args.count)

    os.makedirs(args.out_dir, exist_ok=True)
    with open(os.path.join(args.out_dir, "stream.bin"), "wb") as f:
        f.write(data)
    # newline="\n" keeps the files identical on Windows and Linux.
    with open(os.path.join(args.out_dir, "stream.hex"), "w", newline="\n") as f:
        f.write("".join(f"{b:02x}\n" for b in data))
    with open(os.path.join(args.out_dir, "stream_len.vh"), "w", newline="\n") as f:
        f.write("// Generated by tools/gen_itch.py - do not edit.\n")
        f.write(f"`define STREAM_LEN {len(data)}\n")
    print(f"gen_itch: wrote {len(data)} bytes to {args.out_dir}/stream.bin (+ .hex, _len.vh)")


if __name__ == "__main__":
    main()
