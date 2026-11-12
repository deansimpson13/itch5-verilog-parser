# ITCH 5.0 Parser in Verilog, Verified Against a Python Golden Model

A hardware parser for **NASDAQ TotalView-ITCH 5.0** market data, written in synthesizable Verilog.
Every message it decodes is checked field by field against a Python reference model (the "golden
model"). The golden model and the Verilog were written from the same spec notes, so they could
share a misreading. The independent check is an outside open-source parser,
[MeatPy](https://github.com/vgreg/MeatPy), run on **real NASDAQ data**.

ITCH is the binary feed NASDAQ uses to publish every order added to, executed on, or removed from
its book. Trading firms often parse it in FPGAs, because hardware can decode a message in a few
nanoseconds. This project builds that kind of parser at small scale and focuses on proving it's
correct.

```
                       ┌──────────────────────┐
                       │  tools/gen_itch.py   │  seeded synthetic ITCH stream
                       └──────────┬───────────┘
                    stream.bin    │    stream.hex
               ┌──────────────────┴──────────────────┐
               ▼                                     ▼
   ┌───────────────────────┐            ┌──────────────────────────┐
   │ tools/itch_ref.py     │            │ tb/tb_itch.v ($readmemh) │
   │ Python golden model   │            │  → rtl/itch_framer.v     │
   │                       │            │  → rtl/itch_decoder.v    │
   └──────────┬────────────┘            └────────────┬─────────────┘
         expected.txt                           actual.txt
               └─────────────► tools/compare.py ◄────┘
                                PASS / FAIL
```

## Results

`make test` from a clean checkout:

| Check | Result |
|---|---|
| Python golden-model unit tests (hand-built bytes) | 11 / 11 pass |
| RTL synthesizability lint (no `$display`, `initial`, `#delay` in `rtl/`) | pass |
| Framer only: message boundaries and types, back-to-back + 30% stalls | 2,047 / 2,047 messages, both runs |
| Full parser, back-to-back input (`in_valid` always high) | 2,047 / 2,047 lines identical |
| Full parser, 30% random `in_valid` stalls | 2,047 / 2,047 lines identical |
| Full parser, 75% random `in_valid` stalls | 2,047 / 2,047 lines identical |

Each run covers **2,047 messages (68,022 bytes)**: **1,328 fully decoded** and **719 correctly
skipped**. Across the three full-parser runs that's 6,141 messages checked field by field.

**Decoded (v1):** S System Event · R Stock Directory · A Add Order · F Add Order w/ MPID ·
E Order Executed · C Executed w/ Price · X Order Cancel · D Order Delete · U Order Replace

**Skipped cleanly using the length prefix:** the other 14 ITCH 5.0 types (H Y L V W K J h P Q B I N O),
unknown type bytes, and v1 types whose length prefix doesn't match the spec.

**Edge cases in the stream:** max-value fields (order ref 2⁶⁴−1, shares 2³²−1, price $200,000.0000),
zero price, a 1-byte message, a 300-byte message, `A` messages with too-short and too-long lengths,
a zero-length record, 20 back-to-back minimum-size messages, and a timestamp at the day's last nanosecond.

### Real NASDAQ data

`make realdata` (needs internet the first time) streams a slice of NASDAQ's published sample file
`01302019.NASDAQ_ITCH50.gz` (30 Jan 2019) without downloading the whole 4.7 GB. The slice is two
windows: the day's first 300,000 messages, and 200,000 messages from the 09:30 market open.
That's **500,000 messages (16,071,175 bytes)**.

| Check | Result |
|---|---|
| Outside parser [MeatPy](https://github.com/vgreg/MeatPy) 0.5.0 vs our Python model, every field of every v1 message | **282,066 messages, 2,689,914 fields, 0 mismatches** |
| Same, for the 217,934 non-v1 messages (H Y L P Q I) | type and length match for all |
| Verilog vs Python model, back-to-back input | 500,000 / 500,000 lines identical |
| Verilog vs Python model, 30% random `in_valid` stalls | 500,000 / 500,000 lines identical |
| Every E/C/X/D/U refers to a live order on the same stock, with enough shares left: start window | 45,824 lookups, **0 misses** |
| Same, open window (orders added before 09:30 are unknown, so misses are expected) | 46,273 lookups, 23,494 not found (50.8%), no other errors |
| Timestamps never go backwards (each window) | pass |
| Every A/F symbol matches its Stock Directory (R) entry (8,713 symbols) | pass |
| Prices, share counts, and code fields in range / allowed by the spec | pass |

Messages by type in the slice: S 3 · R 8,713 · A 95,177 · F 86,076 · E 785 · C 138 · X 13,925 ·
D 58,211 · U 19,038. Reports are written to `build/real/`.

## How to run

### Prerequisites

| Tool | Windows (winget) | Linux (apt) | macOS (brew) |
|---|---|---|---|
| Python 3 | `winget install Python.Python.3.12` | `python3` | `python` |
| Icarus Verilog (+GTKWave) | `winget install Icarus.Verilog` | `iverilog gtkwave` | `icarus-verilog` + `gtkwave` |
| GNU Make ≥ 4.3 | `winget install ezwinports.make` | `make` | `brew install make`, then run `gmake` (the built-in make is too old) |

On Windows, the Icarus installer puts its tools in `C:\iverilog\bin` and `C:\iverilog\gtkwave\bin`.
Add both folders to your PATH. If your Python is called `python3`, run `make test PY=python3`.

### Commands

```
make test         # everything: generate data, run all sims, compare -> ALL TESTS PASSED
make data         # just the synthetic stream + Python expected output
make sim          # just build/run the Verilog simulations
make clean        # delete build/ and data/ (keeps the cached real slice in data/real/)
make distclean    # clean + delete data/real/

make realdata     # real NASDAQ data: fetch slice, MeatPy cross-check, sanity checks,
                  # Verilog sim. Needs internet the first time; ~5 minutes; not part of `make test`

make walkthrough  # cycle-by-cycle trace of one Add Order (see docs/WALKTHROUGH.md)
make wave         # build/wave.vcd, then: gtkwave build/wave.vcd
make test STALL=50 SEED=7    # try a different stall rate / seed
```

When something fails, `compare.py` prints the first differing line with context and names the field:

```
FAIL: mismatch at line 36 (build/expected.txt vs build/actual.txt)
  > exp      36: 36 t=58 len=23 X loc=13 trk=34 ts=10800000902487 ref=12 shares=876
  > act      36: 36 t=58 len=23 X loc=13 trk=34 ts=10800000902487 ref=12 shares=224304
  first differing field (#8): expected 'shares=876', got 'shares=224304'
```

(That failure was produced on purpose, by shifting one field offset in the decoder by one byte,
to show the test really catches mistakes.)

## Hardware interface

```
itch_top
  in:  clk, rst (sync, active-high)
       in_valid, in_data[7:0]     → one byte per clock when in_valid && in_ready
  out: in_ready                   (always 1 in v1)
       out_valid                  one-clock pulse per message
       out_type[7:0], out_len[15:0], out_supported
       out_locate, out_tracking, out_timestamp[47:0]
       out_order_ref, out_new_order_ref, out_side, out_shares, out_price, out_stock,
       out_mpid, out_match, out_printable, out_event, + Stock Directory fields
```

- **Throughput:** 1 byte/clock with no gaps between messages.
- **Latency:** 2 clocks from the last byte of a message to `out_valid`.
- **Size:** a 40-byte message buffer (320 flip-flops) plus the output registers.

## Repo layout

```
Makefile                 data / sim / test / clean (+ walkthrough, wave)
rtl/                     synthesizable design
  itch_framer.v            length-prefix FSM: finds message boundaries
  itch_decoder.v           byte buffer + offset-based field extraction
  itch_top.v               framer -> decoder
tb/                      testbenches (simulation only)
  tb_framer.v              phase 2: framer alone, boundary/beat checks
  tb_itch.v                full parser, random stalls, writes actual.txt
  tb_walkthrough.v         one Add Order, cycle-by-cycle table
tools/
  gen_itch.py              synthetic ITCH generator (seed 42) -> data/
  itch_ref.py              Python golden model -> build/expected.txt
  compare.py               diff with context, nonzero exit on mismatch
  test_ref.py              unit tests for itch_ref.py and compare.py
  check_rtl.py             rejects non-synthesizable code in rtl/
  clean.py                 cross-platform `make clean` / `make distclean`
  fetch_real.py            streams a pinned slice of a real NASDAQ file -> data/real/
  cross_check.py           outside parser (MeatPy) vs itch_ref.py, every field
  sanity_real.py           parser-independent consistency checks on real data
  venv_run.py              runs a script inside build/venv (where MeatPy is installed)
  requirements-realdata.txt  pinned MeatPy version (only for `make realdata`)
docs/
  SPEC_NOTES.md            field offset tables from the ITCH 5.0 PDF, with section citations
  DECISIONS.md             design choices and why
  WALKTHROUGH.md           one Add Order through Python and Verilog, for beginners
```

`data/` and `build/` are generated and not committed. That includes the real NASDAQ slice in
`data/real/`, which is NASDAQ's data.

## How it was built

Each phase was committed only after its tests passed (see `git log`):

1. Python generator, golden model, and expected output
2. Verilog framer, checked against Python message boundaries
3. Decoder for Add Order (`A`) only
4. The remaining v1 types. The comparison caught a real bug here: the buffer was one byte too
   small for the 40-byte `F` message.
5. Edge cases, stall tests, protocol checks, docs
6. Real NASDAQ data: an outside parser (MeatPy) cross-check, parser-independent sanity checks,
   and the Verilog run on 500,000 real messages. No mismatches were found.

## References

- [Nasdaq TotalView-ITCH 5.0 specification (PDF)](https://www.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/NQTVITCHspecification.pdf)
- [docs/SPEC_NOTES.md](docs/SPEC_NOTES.md) lists the exact tables used and how ambiguities were resolved.
