# Walkthrough: One Add Order, Byte by Byte

This page follows one ITCH **Add Order** message through both parsers: first the Python golden
model, then the Verilog hardware, one clock cycle at a time. It assumes you know what a byte, a
hex number, and a flip-flop are, and not much else.

The message is the hand-built one used in `tools/test_ref.py` and `tb/tb_walkthrough.v`, so you
can rerun everything on this page yourself:

```
make walkthrough
```

---

## 1. The bytes

On the wire, the message is 38 bytes: a 2-byte length, then 36 bytes of message.

```
00 24 | 41 | 00 07 | 00 2A | 00 00 0B A4 3B 74 | 00 00 00 00 00 00 30 39 | 42 | 00 00 00 64 | 41 41 50 4C 20 20 20 20 | 00 1B 4C 14
len   | A  | locate| track | timestamp         | order reference number  | B  | shares      | stock "AAPL    "        | price
```

Here is each field, with offsets from the Add Order table in [SPEC_NOTES.md](SPEC_NOTES.md) (spec §1.3.1).
Offsets count from the message type byte, *not* from the length prefix.

| Offset | Bytes (hex) | Field | How to read it | Value |
|---|---|---|---|---|
| — | `00 24` | Length prefix | 0x0024 | **36** bytes follow |
| 0 | `41` | Message Type | ASCII 0x41 | **A** |
| 1 | `00 07` | Stock Locate | big-endian: 0x0007 | **7** |
| 3 | `00 2A` | Tracking Number | 0x002A | **42** |
| 5 | `00 00 0B A4 3B 74` | Timestamp | 0x0BA43B74 ns | **195,312,500** ns (0.195 s after midnight) |
| 11 | `00 … 30 39` | Order Reference | 0x3039 | **12345** |
| 19 | `42` | Buy/Sell | ASCII 0x42 | **B** (buy) |
| 20 | `00 00 00 64` | Shares | 0x64 | **100** |
| 24 | `41 41 50 4C 20 20 20 20` | Stock | ASCII, space-padded | **"AAPL    "** |
| 32 | `00 1B 4C 14` | Price | 0x1B4C14 = 1,788,948, 4 implied decimals | **$178.8948** |

**Big-endian** means the *first* byte is the most significant. That's why `00 07` is 7 and not
0x0700 = 1792.

---

## 2. Through the Python golden model (`tools/itch_ref.py`)

### Step 2a: framing (`iter_messages`)

```python
length = int.from_bytes(data[pos:pos + 2], "big")   # data[0:2] = 00 24 -> 36
pos += 2
yield data[pos:pos + length]                        # bytes 2..37 = the 36-byte message
pos += length                                       # now points at the next length prefix
```

Python can jump straight to any byte (`data[pos]`), so framing is just "read 2 bytes, slice N bytes".

### Step 2b: is it supported? (`format_message`)

```python
V1_LENGTHS[ord("A")] == 36  and  len(m) == 36   ->  decode it
```

Both the type **and** the length must match the spec. A 20-byte message that starts with `A`
would print `SKIP` instead (see SPEC_NOTES, ambiguity 1).

### Step 2c: pull out the fields by offset (`decode_fields`)

```python
u(m, 1, 2)    -> int.from_bytes(m[1:3], "big")   -> 7          # locate
u(m, 5, 6)    -> int.from_bytes(m[5:11], "big")  -> 195312500  # timestamp
u(m, 11, 8)   -> 12345                                         # order ref
alpha(m, 19, 1) -> "B"
u(m, 20, 4)   -> 100
alpha(m, 24, 8) -> "AAPL____"        # spaces printed as "_"
price(m, 32)  -> 1788948 -> "178.8948"
```

### Step 2d: the output line

```
1 t=41 len=36 A loc=7 trk=42 ts=195312500 ref=12345 side=B shares=100 stock=AAPL____ price=178.8948
```

The Verilog testbench has to produce **exactly** this line.

---

## 3. Through the Verilog (`rtl/`)

The hardware can't jump around in the data the way Python can. It sees **one byte per clock
cycle**, in order, and has to remember what it needs. Two modules split the job:

```
in_data ──► itch_framer ──(msg_valid, msg_data, msg_first, msg_last)──► itch_decoder ──► out_*
            "where are the                                              "which byte is
             message edges?"                                             which field?"
```

### How to read the trace below

`make walkthrough` prints one row per clock cycle. Each row shows the signals **just before** the
rising clock edge, i.e. the values the flip-flops are about to capture. Anything a flip-flop
captures on edge *N* shows up in row *N+1*.

| Column | Signal | Meaning |
|---|---|---|
| `iv in` | `in_valid`, `in_data` | the byte being offered to the framer |
| `framer` | `u_framer.state` | `LEN_HI`, `LEN_LO`, or `BODY` |
| `rem` | `u_framer.remaining` | body bytes still expected |
| `mv md f l` | `msg_valid msg_data msg_first msg_last` | framer output to the decoder |
| `pos` | `u_decoder.pos` | where the decoder will store `msg_data` in its buffer |
| `done` | `u_decoder.done` | "the whole message is in the buffer" |
| `out_valid` | | the one-cycle "decoded fields are ready" pulse |

### The trace (real simulator output)

```
cyc  | iv in  | framer   rem  | mv md  f l |   pos  done | out_valid
-----+--------+---------------+------------+-------------+----------
   1 | 1  00  | LEN_HI     0 | 0  00  0 0 |     0  0 | 0
   2 | 1  24  | LEN_LO     0 | 0  00  0 0 |     0  0 | 0
   3 | 1  41  | BODY      36 | 0  00  0 0 |     0  0 | 0
   4 | 1  00  | BODY      35 | 1  41  1 0 |     0  0 | 0
   5 | 1  07  | BODY      34 | 1  00  0 0 |     1  0 | 0
   6 | 1  00  | BODY      33 | 1  07  0 0 |     2  0 | 0
   7 | 1  2a  | BODY      32 | 1  00  0 0 |     3  0 | 0
  ...  (rows 8-35: the same pattern continues; run `make walkthrough` for every row)
  36 | 1  1b  | BODY       3 | 1  00  0 0 |    32  0 | 0
  37 | 1  4c  | BODY       2 | 1  1b  0 0 |    33  0 | 0
  38 | 1  14  | BODY       1 | 1  4c  0 0 |    34  0 | 0
  39 | 0  00  | LEN_HI     0 | 1  14  0 1 |    35  0 | 0
  40 | 0  00  | LEN_HI     0 | 0  14  0 0 |    36  1 | 0
  41 | 0  00  | LEN_HI     0 | 0  14  0 0 |    36  0 | 1

Decoded: type=A len=36 supported=1 loc=7 trk=42 ts=195312500
         ref=12345 side=B shares=100 stock="AAPL    " price=1788948 (= 178.8948)
```

### Cycle by cycle

**Cycle 1: high length byte.** The framer is in `LEN_HI` and `in_data = 00`. On the clock edge it
saves `len_hi <= 00` and moves to `LEN_LO`. Nothing goes to the decoder yet.

**Cycle 2: low length byte.** The framer is in `LEN_LO` and `in_data = 24`. It glues the two bytes
together: `{len_hi, in_data} = 0x0024 = 36`. That isn't zero, so on the edge it sets
`msg_len <= 36`, `remaining <= 36`, `at_first <= 1`, and moves to `BODY`.

**Cycle 3: byte 0, the type `41` ('A').** The framer is in `BODY` with `remaining = 36`. On the edge
it copies the byte to its output (`msg_data <= 41`), raises `msg_valid`, and, because `at_first`
was 1, raises `msg_first` too. `remaining` drops to 35.

**Cycle 4: the decoder sees byte 0.** The framer's outputs are registered, so the `41` appears on
`msg_data` one cycle after it arrived. The decoder sees `msg_first = 1`, so `pos = 0` regardless
of its old offset. On the edge it writes `mbuf[0] <= 41` and sets `offset <= 1`. In the same
cycle the framer is already taking in the *next* byte (`00`, the first locate byte). This overlap
is called **pipelining**: each module works on a different byte at the same time.

**Cycles 5–38: the rest of the body.** Each cycle, the framer passes one byte on and counts
`remaining` down, and the decoder stores the previous byte at `mbuf[pos]` and counts `pos` up.
The `pos` column climbs 1, 2, 3, … in step with the message offsets in the table in section 1.
For example, in cycle 27 the decoder stores `64` (shares = 100) at `mbuf[23]`, the last byte of
the Shares field (offsets 20–23).

**Cycle 38: last input byte.** The framer receives `14` with `remaining = 1`. That means this is
the last byte, so on the edge it raises `msg_last` and goes back to `LEN_HI`, ready for the next
message's length prefix. `in_valid` drops afterwards because there is no more input.

**Cycle 39: the decoder stores the last byte.** It sees `msg_last = 1` with `pos = 35`. On the
edge it writes `mbuf[35] <= 14` and sets `done <= 1`. Now all 36 bytes are in `mbuf[0..35]`.

**Cycle 40: extract.** `done = 1`. The decoder checks the type and length:
`spec_len("A") = 36` equals `len_q = 36`, so `supported = 1`. On the edge, every field is copied
out of the buffer by offset, just as Python did:

```verilog
out_locate    <= be(1, 2);    // {mbuf[1], mbuf[2]}            = 0x0007
out_timestamp <= be(5, 6);    // {mbuf[5] ... mbuf[10]}        = 195312500
out_order_ref <= be(11, 8);   // {mbuf[11] ... mbuf[18]}       = 12345
out_side      <= be(19, 1);   // mbuf[19]                      = 'B'
out_shares    <= be(20, 4);   // {mbuf[20] ... mbuf[23]}       = 100
out_stock     <= be(24, 8);   // {mbuf[24] ... mbuf[31]}       = "AAPL    "
out_price     <= be(32, 4);   // {mbuf[32] ... mbuf[35]}       = 1788948
out_valid     <= 1;
```

`be(off, n)` just places `n` bytes side by side, first byte on the left. That *is* big-endian.
In hardware it's only wires; no arithmetic happens.

**Cycle 41: `out_valid = 1` for exactly one cycle.** The decoded fields sit on the `out_*` ports.
The testbench (`tb/tb_itch.v`) sees `out_valid` and prints them in the same format as Python:

```
1 t=41 len=36 A loc=7 trk=42 ts=195312500 ref=12345 side=B shares=100 stock=AAPL____ price=178.8948
```

The hardware stores the raw integer 1,788,948. Turning it into `178.8948` (divide by 10,000) only
happens in the testbench's print routine. Hardware usually keeps prices as integers, because
fixed-point math is cheap and exact.

### Timing summary

| Event | Clock edge |
|---|---|
| Last byte accepted by the framer | end of cycle 38 |
| Last byte stored in decoder buffer | end of cycle 39 |
| Fields extracted, `out_valid` set | end of cycle 40 |

**Latency: 2 clock edges** after the last byte is accepted. **Throughput: 1 byte per clock.** The
framer is back in `LEN_HI` at cycle 39, so a following message can start right away while this
one is still being decoded.

---

## 4. What changes when `in_valid` drops (stalls)

In the stall tests, the testbench randomly holds `in_valid` low. On those cycles `beat = 0`, so
the framer's `case` statement doesn't run: state, `remaining`, and `len_hi` all keep their values,
and `msg_valid` stays 0. The decoder likewise doesn't store anything or move `offset`. The message
arrives more slowly, but every byte still lands at the same `pos`, so the decoded fields are
identical. That is exactly what `make test` checks with 30% and 75% stall rates.

## 5. See it as a waveform

```
make wave
gtkwave build/wave.vcd
```

In GTKWave, open `tb_itch → dut → u_framer` and `u_decoder`. Add `state`, `remaining`,
`msg_valid`, `msg_data`, `pos`, `done`, and `out_valid`. The first Add Order in the generated
stream is message 19.
