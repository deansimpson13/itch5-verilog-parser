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

- **The reference parser is checked against hand-built bytes.** `tools/test_ref.py` builds an Add
  Order by hand, byte by byte from the spec table, and checks the decoded line. This catches a bug
  that the generator and parser might otherwise share. It is still our own reading of the spec,
  though. The truly independent check is the outside parser (see "Real-data validation" below).
- **A truncated file is an error in Python.** The Verilog can't "raise"; it just never finishes the
  last message. The generator never writes truncated files, so the two never meet in a test.

## Real-data validation (phase 6)

Why: the generator, golden model, and Verilog all came from the same notes in SPEC_NOTES.md. A
field misread there would be misread identically in all three, and `make test` would still pass.
Real NASDAQ bytes plus a parser we didn't write close that gap.

### The data (tools/fetch_real.py)

- **File:** `01302019.NASDAQ_ITCH50.gz` from NASDAQ's sample directory
  (`https://emi.nasdaq.com/ITCH/Nasdaq%20ITCH/`), a full trading day (30 Jan 2019, 4.7 GB gzipped).
  It's an older, fixed file that won't change, and it has a published `.md5sum`.
- **Streamed, not downloaded.** The script reads the gzip over HTTP 1 MB at a time, decompresses
  incrementally with `zlib.decompressobj(wbits=31)`, walks the 2-byte length prefixes to split
  messages, and closes the connection once it has what it needs. About 135 MB of the 4.7 GB are
  downloaded, which takes about 13 seconds.
- **Two windows, not "the first 100,000 messages".** A measurement before writing the script showed
  the first 100,000 messages would test almost nothing:
  - Messages 1 to about 220,000 contain **no orders at all**: S, then 8,713 R (one per symbol),
    then H, Y, and L. Orders start at 04:00.
  - In the first 2 million messages there are **0 C** (Order Executed With Price), 49 F, and
    1,851 E. Executions mostly happen after the 09:30 open.

  So the slice is the day's **first 300,000 messages** (every R, the opening S events, and the first
  ~80,000 pre-market order messages) followed by **200,000 messages starting at the first timestamp
  ≥ 09:30:00** (message 10,532,164 of the day). That gives 500,000 messages in 16 MB. Every v1 type
  appears, the rarest being S (3) and C (138).
- **Pinned by SHA-256.** The script checks both windows against hashes recorded on the first run
  (and confirmed with a second, independent fetch). A changed or corrupted file stops the run
  instead of silently testing different data. Cached copies in `data/real/` are reused if the
  hashes match.
- **Not committed.** `data/` is gitignored. `make clean` keeps `data/real/` so you don't
  re-download after every clean; `make distclean` removes it.

### The outside parser (tools/cross_check.py)

- **MeatPy 0.5.0** (https://github.com/vgreg/MeatPy, BSD-3), pinned in
  `tools/requirements-realdata.txt`. Why this one:
  - It's written and maintained by finance academics (V. Grégoire, HEC Montréal;
    C. Martineau, U. of Toronto), has been used for research on exactly this kind of NASDAQ data,
    and supports ITCH 2.0 through 5.0.
  - It's pure Python and on PyPI, so it installs on Windows with no compiler. A Rust or C++ parser
    would add a toolchain to `make realdata`.
  - **It decodes differently from us.** It uses `struct` format strings (e.g. `"!HHHIQcI8sI"` for
    `A`) where we use explicit byte offsets, splits the 6-byte timestamp into a 2-byte + 4-byte
    unpack, and does its own framing. So a shared mistake is unlikely.
  - Considered and not used: `itchfeed` (PyPI). It's less widely used, and one outside parser is
    enough for this check.
- **Installed in a private venv** (`build/venv`, created by `tools/venv_run.py`), so `make test`
  still uses only the standard library.
- **What's compared:** for the 9 v1 types, every field we print (header + type fields), as text.
  For the other types, type byte and length. On MeatPy's side, the field values are formatted by
  code in cross_check.py, not by itch_ref.py, so only the printing convention (price as
  `whole.ffff`, space as `_`) is shared. That convention is a choice, not a spec reading.
- **Result: 0 mismatches** in 2,689,914 fields. Nothing disagreed, so no spec dispute needed resolving.
- **The cross-check was checked.** A scratch copy of itch_ref.py with X's shares offset changed
  19 → 18 gives `FAIL: 13,925 mismatching fields`, names `X shares`, and exits 1.

  An interesting near-miss: changing it 19 → **20** was *not* caught. X is 23 bytes, so reading 4
  bytes from 20 gets only the last 3 bytes of the field, and every real share count is below 2²⁴,
  so those 3 bytes give the same number. Real data can hide a bug that only shows on large values.
  The synthetic stream's max-value edge cases (shares = 2³²−1) are what catch it. **The two kinds of
  test cover each other's gaps,** which is why `make test` keeps the synthetic data.

### Sanity checks (tools/sanity_real.py)

These don't ask two parsers to agree. They check that the decoded values make sense together,
which a misread field would break:

- **Order lifecycle:** each E/C/X/D/U must find a live order added earlier by A/F/U, on the same
  stock locate, with enough shares left. U retires the old ref and adds the new one; E/C/X that
  reach zero shares retire the order. Results:
  - **Start window:** 45,824 lookups, **0 misses.** (It begins with the day's first message, so
    every order is visible.)
  - **Open window:** 50.8% misses, which is expected because those orders were added before 09:30.
    Misses are reported, not failed, as asked.
- **Time:** timestamps never decrease within a window, and are all before midnight. The two windows
  are checked separately, since time jumps from 04:02 to 09:30 between them.
- **Symbols:** every A/F stock symbol matches the R message for its locate code (8,713 codes).
- **Ranges:** 0 < price ≤ $200,000.0000 (the Price(4) maximum); shares ≤ 10,000,000; code fields
  (event, side, printable, and the R flags) must be values listed in the spec, which I re-checked
  against the PDF (§1.2.1 pp. 6–8). The largest price seen was $199,999.99, a market-maker
  "stub quote", which is a placeholder price far from the market but legal.

### The Verilog on real data

- **The same testbench**, `tb/tb_itch.v`, compiled a second time into `build/real/` with
  `-I data/real`, so `STREAM_LEN` comes from the real slice's `stream_len.vh` and sizes the memory to
  16,071,175 bytes. A new `+HEX=<file>` plusarg points `$readmemh` at the real hex file. The default
  is unchanged, so `make test` behaves exactly as before.
- **$readmemh at 16 MB is fine in Icarus.** Measured on the back-to-back run: 622 MB peak memory
  and 102 seconds. Two runs (back-to-back, and 30% stalls) instead of `make test`'s three keep `make realdata`
  to about 5 minutes. The 75% run adds little on real data that the synthetic test doesn't already cover.
- **Two testbench hardening fixes:**
  - The timeout was `cycles < 100 * STREAM_LEN`, a 32-bit product that would overflow above about
    21 MB. It's now `cycles / 100 < STREAM_LEN`.
  - The testbench now `$fatal`s if the hex file is shorter than `STREAM_LEN` (a stale
    `stream_len.vh`), instead of feeding X's into the parser.

## Synthesis (phase 7)

### Tools

- **YosysHQ OSS CAD Suite, build 2026-10-01** (Yosys 0.69+173, nextpnr 0.11.1-40), Windows x64,
  extracted to `~/oss-cad-suite`. A dated build about six weeks old was chosen over that day's
  nightly. Archive SHA-256:
  `8447532f74203898b7eb3c8730bd397c06a47b0d1df6dfadb1aef02811c1901a`.
- **Not added to PATH.** The suite bundles its own `iverilog`, `vvp`, and `python`, which would
  shadow the ones `make test` uses. `tools/synth_report.py` finds the suite (PATH, then
  `$OSS_CAD_SUITE`, then `~/oss-cad-suite`) and adds its `bin/` and `lib/` only for the commands it runs.
- **Vivado is not installed**, so there is no Artix-7 place-and-route or Fmax, only Yosys
  `synth_xilinx` post-synthesis counts, labelled as such everywhere.
- **Windows Smart App Control** blocks the suite's unsigned `Qt6Gui.dll` when `nextpnr-ice40`
  starts. Qt is only used for nextpnr's optional `--gui` mode. Command-line place-and-route
  works normally (all runs here succeeded).

### The abc9 workaround

- In this Yosys build, the standard `synth_ice40` and `synth_xilinx` scripts crash on Windows in
  their LUT-mapping step. `abc9` now writes its temporary file with the new, experimental
  `write_xaiger2` backend, which fails an internal assertion
  (`data_start == f->tellp()`, `backends/aiger2/aiger.cc:1078`). It looks like a Windows
  text-vs-binary file problem in the tool, not anything in our Verilog.
- **Workaround:** run the stock script up to `map_luts` (`-run :map_luts`), do that one step with
  the classic `abc` pass (`abc -dress -lut 4` for iCE40, which was `synth_ice40`'s default for
  years; `abc -luts 2:2,3,6:5,10,20` for Xilinx, the same LUT cost table the stock script uses),
  then finish with the stock script (`-run map_cells:` / `-run finalize:check`). Every other step
  is unchanged.
- **Effect:** classic `abc` doesn't model delays the way `abc9` does, so LUT counts and Fmax may
  differ somewhat from a Linux run of the stock flow. The numbers are still real measurements of
  a real, routed netlist. If a later Yosys build fixes the crash, the scripts in
  `tools/synth_report.py` can go back to plain `synth_ice40` / `synth_xilinx`.

### What gets synthesized

- **`itch_top` on its own** (both targets): the parser's true size. Its ports become netlist
  ports, so synthesis can't remove any output logic. Xilinx uses `-noiopad` (no I/O buffers,
  since the parser would be one block inside a bigger design) and `-flatten` (one total instead
  of per-module counts; `synth_ice40` flattens by default).
- **`synth/synth_top.v` for place-and-route:** `itch_top` has 642 output bits, and the HX8K has
  206 I/O pins. The wrapper loads all 641 outputs other than `out_valid` into a shift register
  (`CAP`) on each `out_valid`, and shifts it out on one pin, `scan_out`. Every output bit can reach
  a pin, so nothing is removable. A scan chain was chosen over XOR-ing outputs together, because
  two identical bits would cancel under XOR and could be deleted legally. Cost: 641 flip-flops
  and 641 two-input muxes, which is why the "alone" row is reported separately. The measured
  wrapper flip-flop count is exactly 1,050 + 641 = 1,691, which confirms the chain width.
- **No pin constraints file.** There's no target board, so `--pcf-allow-unconstrained` lets
  nextpnr choose the pins. Fmax is reported for the clock-to-clock paths only. Input/output
  timing depends on the board, so nextpnr doesn't constrain it here.
- **iCE40-HX8K, ct256 package.** The largest common iCE40 HX part. 25% of its logic cells are used.

### Fmax

- **5 placement seeds** (`SYNTH_SEEDS ?= 1,2,3,4,5`): 91.95, 88.64, 88.09, 83.42, 81.28 MHz.
  nextpnr's random placement changes wire lengths, so one seed can mislead. The README gives the
  range, and **throughput uses the worst seed** (81.28 MHz).
- **`--freq 100 --timing-allow-fail`:** the target only steers how hard the router tries. The
  reported Fmax is what was achieved. Without `--timing-allow-fail`, nextpnr exits with an error
  whenever it misses the target, which would turn a measurement into a failure.
- **Critical path, the same on all 5 seeds** (`rtl/itch_decoder.v:95-106`): the start is either
  the framer's `msg_first` (seeds 1, 4, 5) or the decoder's `offset` register (seeds 2, 3). These
  are the two inputs of `pos = msg_first ? 0 : offset`. From there: `pos < BUF_BYTES`, a 16-bit
  compare built as a 12-cell carry chain → write-enable decode for the 40 buffer bytes → long
  routes to an `mbuf` flip-flop's clock-enable. 18 logic steps; 4.05–4.18 ns logic and
  6.70–8.13 ns routing, depending on the seed. As asked, it isn't redesigned yet. Candidate fixes
  for later: a 6-bit saturating `offset`, so the compare is short, or a one-hot write pointer.
- **10GbE:** at 1 byte/clock, 10 Gb/s needs 1,250 MHz. Measured, 81.28 MHz = 0.65 Gb/s = 6.5% of
  line rate. Real 10GbE designs use a 64-bit (8 bytes/clock) datapath at 156.25 MHz.

### Checks that nothing was optimized away

- **Yosys `check -assert`** after synthesis: fails on undriven nets, multiple drivers, or loops.
- **No constant output bits** in the `itch_top` netlist (except `in_ready`, 1 by design). Checked
  from the Yosys JSON netlist, where a constant bit appears as `"0"`/`"1"` instead of a net
  number. **This is the sensitive check.** A scratch copy of the decoder with `out_side` tied to 0
  was caught (`out_side[0..7]` reported). Yosys deleted those 8 flip-flops (1,050 → 1,042).
- **Flip-flop floor of 962** (320 buffer + 642 registered outputs). **This is the coarse check.**
  It catches large losses, such as a buffer or a whole field group disappearing, but not the
  8-bit case above (1,042 is still over 962). A hand count from the RTL gives about 1,049 (framer
  54 + decoder 995), and Yosys reports 1,050. The floor is deliberately not set to that exact
  count, because a different Yosys version could legitimately merge a register or two.
