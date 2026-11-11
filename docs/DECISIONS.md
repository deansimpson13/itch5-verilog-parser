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

## Golden model (tools/itch_ref.py)

- **The reference parser is checked independently.** `tools/test_ref.py` builds an Add Order by
  hand, byte by byte from the spec table, and checks the decoded line. This catches a bug that the
  generator and parser might otherwise share.
- **A truncated file is an error in Python.** The Verilog can't "raise"; it just never finishes the
  last message. The generator never writes truncated files, so the two never meet in a test.
