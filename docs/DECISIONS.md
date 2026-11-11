# Design Decisions

This file records the judgment calls made while building the project.
Spec ambiguities are covered in [SPEC_NOTES.md](SPEC_NOTES.md#ambiguities-and-the-reading-chosen).

## Toolchain and environment

- **Windows-friendly Makefile.** The project was built on Windows 11 using GNU Make 4.4 (`ezwinports.make`),
  Icarus Verilog 12, and Python 3.12. Every recipe only calls `python`, `iverilog`, or `vvp`.
  It works the same under `cmd.exe` and a Unix shell. `make clean` runs `tools/clean.py`
  instead of `rm -rf`, because `rm -rf` doesn't exist on Windows.
- **Grouped targets (`&:`).** A single generator run produces three files. The `&:` syntax
  (GNU Make 4.3+) tells Make to run the generator once, not once per file.
- **Python uses only the standard library** (`struct`, `random`, `argparse`, `unittest`).
- **The spec PDF is not committed.** It is Nasdaq's copyrighted document, so SPEC_NOTES.md links to it instead.

## Test data (tools/gen_itch.py)

- **Seed 42, 2,000 random messages plus hand-made edge cases.** The full stream is about 68 KB and
  2,047 messages. It simulates in seconds and still contains hundreds of each common type.
- **A realistic day shape.** The stream starts with System Event `O`, then a Stock Directory spin,
  `S` and `Q`, then the random body, the edge cases, and finally `M`, `E`, `C`. Execute, cancel,
  delete, and replace messages refer to orders that are actually live. This isn't strictly required
  for parsing, but it makes the output read like real data.
- **Two independent descriptions of each layout.** The generator packs fields with `struct` format
  strings in spec order. The reference parser reads fields by explicit byte offset. If either one
  has a typo, the other disagrees. `self_check()` also asserts that every format string adds up to
  the spec's message length.
- **Skip-type messages are generated with correct layouts** for all 14 non-v1 types, not as random
  filler. The test then exercises realistic lengths from 12 to 50 bytes.
- **Edge cases in the stream:** a max-value Add Order (ref 2⁶⁴−1, shares 2³²−1, price 200,000.0000),
  a zero-price order, a 1-byte unknown message, a 300-byte unknown message (its length has a nonzero
  high byte), an `A` with a too-short length, an `A` with a too-long length, a zero-length record,
  20 back-to-back Order Deletes, and a final timestamp at the last nanosecond of the day.

## Output format (expected.txt / actual.txt)

```
<seq> t=<type hex> len=<length> SKIP
<seq> t=<type hex> len=<length> <letter> loc=<n> trk=<n> ts=<n> <name>=<value> ...
```

- **Every message produces a line, including skipped ones.** The comparison then checks the framer
  too: a lost or extra message shows up as a shifted `seq`.
- **Decimal for integers and `whole.ffff` for prices.** These are easy for a person to read, and
  Verilog's `$fwrite("%0d")` can produce them. The 4-digit fraction is printed digit by digit in the
  testbench, so the result doesn't depend on simulator support for `%04d`.
- **Alpha fields print one character per byte**, with space → `_` and non-printable → `.`. Lines
  therefore never contain extra spaces, and every token is a single `name=value` pair.
  `compare.py` relies on this to name the field that differs.
- **The type is printed in hex (`t=41`)** because unknown types can be non-printable bytes.

## Verilog design

- **Framer = 3-state FSM** (`LEN_HI → LEN_LO → BODY`) with a down-counter. It knows nothing about
  ITCH message types. It only splits the stream using the length prefix. That makes "skip unknown
  types" automatic: the framer passes every message through, and the decoder chooses not to decode it.
- **The decoder buffers the whole message (40 bytes), then extracts fields.** Grabbing each field as
  its bytes stream past would save flip-flops, but it spreads the offsets across many comparisons.
  With the buffer, every offset sits in one `case` statement that reads like the spec table.
  The cost is 320 flip-flops, which is tiny.
- **The buffer is 40 bytes, not 39.** The first version used 39 (the length of `R`). But `F` is 40
  bytes, and the comparison step caught the missing last MPID byte. This is the kind of bug the
  golden-model approach exists to catch.
- **Shared output ports.** Fields that mean the same thing in several types share one port. For
  example, `out_order_ref` is used by A/F/E/C/X/D and as the *original* ref in U. `out_shares` is
  shares, executed shares, or cancelled shares, depending on type. Fields a type doesn't have are
  driven to 0. This avoids about 40 nearly identical ports.
- **`be(offset, nbytes)` helper function.** It concatenates buffer bytes big-endian. It is only
  ever called with constants, so synthesis reduces it to wiring.
- **Only control signals are reset.** `out_valid` and the FSM state are reset. Data registers are
  simply overwritten for each message. This is standard practice and reduces reset fan-out.
- **No backpressure in v1.** The decoder can always keep up (one byte per clock in, at most one
  message per 3 clocks out), so `in_ready` is tied to 1. It is still a real port, so a later
  version can add backpressure without changing the interface.
- **Latency is 2 clocks** from the clock edge that accepts a message's last byte to `out_valid`:
  one register stage in the framer and one in the decoder.
- **Synthesizability is checked by a script.** `tools/check_rtl.py` (run by `make test`) fails if
  any file in `rtl/` contains a `$system_task`, an `initial` block, or a `#delay`.

## Verification

- **Three full-parser runs** in `make test`: back-to-back (`in_valid` always high), 30% random stalls
  (seed 1), and 75% random stalls (seed 99). All three must match `expected.txt` exactly.
  Stalls are random per cycle, so they land between length bytes, inside the body, and on
  message boundaries.
- **The framer is also tested alone** (phase 2 testbench). Its testbench checks that each message
  has exactly `msg_len` beats from `msg_first` to `msg_last`.
- **Protocol checks in the testbench:** `out_valid` is never high for 2 cycles in a row, and
  `in_ready` never drops.
- **The checkers are checked.** `test_ref.py` confirms that `compare.py` fails on a changed field
  and on a missing line. During development, changing one decoder offset (X shares 19 → 20) was
  confirmed to make `make test` fail with a clear message.
- **iverilog uses `-g2012`** so the testbenches can use `$fatal`, which gives `vvp` a nonzero exit
  code on failure. The RTL uses only Verilog-2001 constructs.

## Golden model (tools/itch_ref.py)

- **The reference parser is checked independently.** `tools/test_ref.py` builds an Add Order by
  hand, byte by byte from the spec table, and checks the decoded line. This catches a bug that the
  generator and parser might otherwise share.
- **A truncated file is an error in Python.** The Verilog can't "raise"; it just never finishes the
  last message. The generator never writes truncated files, so the two never meet in a test.
