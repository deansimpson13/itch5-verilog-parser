# ITCH 5.0 Spec Notes

**Source:** *Nasdaq TotalView-ITCH 5.0* specification (PDF), downloaded from
<https://www.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/NQTVITCHspecification.pdf>.
The newest entry in its revision log (Appendix A) is **April 28, 2023**.
Every table below was copied from that PDF. Section numbers and page numbers refer to that document.

The PDF itself is not committed to this repo. It is Nasdaq's document, so download it from the link above.

---

## General data rules ("Data Types", p. 4)

| Rule | What the spec says | What this project does |
|---|---|---|
| Integers | "All integer fields are big endian (network byte order) binary encoded numbers. Unless otherwise noted, they are unsigned." | All integers are decoded big-endian and unsigned. |
| Alpha | "ASCII fields which are left justified and padded on the right with spaces." | Alpha fields are kept as raw bytes. In the text output a space prints as `_`. |
| Price (4) | Integer with 4 implied decimal places. "The maximum value of price (4) … is 200,000.0000 (decimal, 77359400 hex)." | Printed as `whole.ffff`, for example `123.4500`. |
| Timestamp | "nanoseconds since midnight", 6 bytes long in every message table. | Decoded as a 48-bit unsigned integer. |

## Message framing (not in the ITCH spec)

The ITCH spec describes message *payloads* only. It says messages are "typically delivered using a higher
level protocol" (SoupBinTCP / MoldUDP64, "Architecture", p. 3). The length prefix comes from the
project brief and from the format of Nasdaq's historical sample files: **each message is preceded by a
2-byte big-endian length**, and that length counts the payload only (message type byte included,
prefix not included).

## Common header (every message, offsets 0–10)

Every table in the spec starts with the same four fields:

| Field | Offset | Length | Type |
|---|---|---|---|
| Message Type | 0 | 1 | Alpha |
| Stock Locate | 1 | 2 | Integer |
| Tracking Number | 3 | 2 | Integer |
| Timestamp | 5 | 6 | Integer (ns since midnight) |

---

## v1 message types: fully decoded

### S: System Event (§1.1, p. 4) — total length 12
| Field | Offset | Length | Type |
|---|---|---|---|
| (header) | 0 | 11 | |
| Event Code | 11 | 1 | Alpha (`O S Q M E C`, §1.1 p. 5) |

### R: Stock Directory (§1.2.1, pp. 5–8) — total length 39
| Field | Offset | Length | Type |
|---|---|---|---|
| (header) | 0 | 11 | |
| Stock | 11 | 8 | Alpha |
| Market Category | 19 | 1 | Alpha |
| Financial Status Indicator | 20 | 1 | Alpha |
| Round Lot Size | 21 | 4 | Integer |
| Round Lots Only | 25 | 1 | Alpha |
| Issue Classification | 26 | 1 | Alpha |
| Issue Sub-Type | 27 | 2 | Alpha |
| Authenticity | 29 | 1 | Alpha |
| Short Sale Threshold Indicator | 30 | 1 | Alpha |
| IPO Flag | 31 | 1 | Alpha |
| LULD Reference Price Tier | 32 | 1 | Alpha |
| ETP Flag | 33 | 1 | Alpha |
| ETP Leverage Factor | 34 | 4 | Integer |
| Inverse Indicator | 38 | 1 | Alpha |

### A: Add Order, No MPID (§1.3.1, p. 13) — total length 36
| Field | Offset | Length | Type |
|---|---|---|---|
| (header) | 0 | 11 | |
| Order Reference Number | 11 | 8 | Integer |
| Buy/Sell Indicator | 19 | 1 | Alpha (`B`/`S`) |
| Shares | 20 | 4 | Integer |
| Stock | 24 | 8 | Alpha |
| Price | 32 | 4 | Price (4) |

### F: Add Order with MPID (§1.3.2, p. 13) — total length 40
Same as **A** for offsets 0–35, plus:

| Field | Offset | Length | Type |
|---|---|---|---|
| Attribution (MPID) | 36 | 4 | Alpha |

### E: Order Executed (§1.4.1, p. 14) — total length 31
| Field | Offset | Length | Type |
|---|---|---|---|
| (header) | 0 | 11 | |
| Order Reference Number | 11 | 8 | Integer |
| Executed Shares | 19 | 4 | Integer |
| Match Number | 23 | 8 | Integer |

### C: Order Executed With Price (§1.4.2, pp. 14–15) — total length 36
| Field | Offset | Length | Type |
|---|---|---|---|
| (header) | 0 | 11 | |
| Order Reference Number | 11 | 8 | Integer |
| Executed Shares | 19 | 4 | Integer |
| Match Number | 23 | 8 | Integer |
| Printable | 31 | 1 | Alpha (`Y`/`N`) |
| Execution Price | 32 | 4 | Price (4) |

### X: Order Cancel (§1.4.3, p. 15) — total length 23
| Field | Offset | Length | Type |
|---|---|---|---|
| (header) | 0 | 11 | |
| Order Reference Number | 11 | 8 | Integer |
| Cancelled Shares | 19 | 4 | Integer |

### D: Order Delete (§1.4.4, p. 15) — total length 19
| Field | Offset | Length | Type |
|---|---|---|---|
| (header) | 0 | 11 | |
| Order Reference Number | 11 | 8 | Integer |

### U: Order Replace (§1.4.5, pp. 15–16) — total length 35
| Field | Offset | Length | Type |
|---|---|---|---|
| (header) | 0 | 11 | |
| Original Order Reference Number | 11 | 8 | Integer |
| New Order Reference Number | 19 | 8 | Integer |
| Shares | 27 | 4 | Integer |
| Price | 31 | 4 | Price (4) |

---

## Other types: skipped using the length prefix

The parsers skip these types. The generator still creates them with the correct length and layout,
so the skip path gets exercised with real-looking data.

| Type | Name | Section | Total length | Body layout after the 11-byte header |
|---|---|---|---|---|
| H | Stock Trading Action | §1.2.2 p. 8 | 25 | Stock 8, Trading State 1, Reserved 1, Reason 4 |
| Y | Reg SHO Restriction | §1.2.3 p. 9 | 20 | Stock 8, Reg SHO Action 1 |
| L | Market Participant Position | §1.2.4 pp. 9–10 | 26 | MPID 4, Stock 8, Primary MM 1, MM Mode 1, Participant State 1 |
| V | MWCB Decline Level | §1.2.5.1 p. 10 | 35 | Level 1/2/3: 8 each, Price (8) |
| W | MWCB Status | §1.2.5.2 p. 11 | 12 | Breached Level 1 |
| K | IPO Quoting Period Update | §1.2.6 p. 11 | 28 | Stock 8, Release Time 4, Qualifier 1, IPO Price 4 |
| J | LULD Auction Collar | §1.2.7 p. 12 | 35 | Stock 8, Ref Price 4, Upper 4, Lower 4, Extension 4 |
| h | Operational Halt | §1.2.8 p. 12 | 21 | Stock 8, Market Code 1, Halt Action 1 |
| P | Trade (Non-Cross) | §1.5.1 pp. 16–17 | 44 | Order Ref 8, B/S 1, Shares 4, Stock 8, Price 4, Match 8 |
| Q | Cross Trade | §1.5.2 p. 17 | 40 | Shares 8, Stock 8, Cross Price 4, Match 8, Cross Type 1 |
| B | Broken Trade | §1.5.3 p. 18 | 19 | Match Number 8 |
| I | NOII | §1.6 pp. 18–19 | 50 | Paired 8, Imbalance 8, Direction 1, Stock 8, Far 4, Near 4, Ref 4, Cross Type 1, Price Var 1 |
| N | Retail Interest (RPII) | §1.7 pp. 19–20 | 20 | Stock 8, Interest Flag 1 |
| O | DLCR Price Discovery | §1.8 p. 20 | 48 | Stock 8, Eligibility 1, Min 4, Max 4, Near Exec Price 4, Near Exec Time 8, Lower Collar 4, Upper Collar 4 |

---

## Ambiguities and the reading chosen

1. **Length prefix vs. spec length.** The spec gives a fixed length for each message type. The prefix
   might disagree with it, for example in corrupted data. *Chosen reading:* a message counts as
   "supported" only if its type is one of the v1 types **and** its length prefix equals the spec length
   exactly. Otherwise it is reported as `SKIP` and its bytes are skipped using the prefix. This never
   reads fields past the end of a short message, and it never misreads the extra bytes of a long one.
2. **Length prefix of 0.** Neither the spec nor the brief defines this. *Chosen reading:* treat it as
   an empty record. Consume the 2 prefix bytes and emit nothing. Neither parser counts it as a message.
3. **ETP Leverage Factor (§1.2.1 p. 7).** The July 2022 revision log mentions "non-integer leverage
   factors", but the table still says 4-byte Integer. *Chosen reading:* 4-byte unsigned integer, as the
   table says.
4. **Stock Locate in System Event** is documented as "Always 0". *Chosen reading:* decode and print it
   anyway rather than assuming it. The generator writes 0.
5. **Signedness.** No v1 field is documented as signed, so all fields are decoded as unsigned
   ("Unless otherwise noted, they are unsigned").
6. **The "O" message type (DLCR, §1.8)** was added in 2023, and some older parsers don't know it.
   It is in the skip list above, so it is handled.
7. **Typos in the PDF** that don't affect any offsets: the E/C table titles say "Add Order – Order
   Executed", and the TOC lists Appendix E on page 30 after Appendix D on page 34. Neither changes
   a field layout.
