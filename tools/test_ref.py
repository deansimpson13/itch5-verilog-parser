#!/usr/bin/env python3
"""
test_ref.py - Unit tests for the golden model (itch_ref.py) and compare.py.

The golden model is what the Verilog gets checked against, so it needs its
own checks.  These tests use HAND-WRITTEN bytes (not the generator) so a
bug shared by the generator and the parser cannot hide.

Run:  python tools/test_ref.py      (also run by `make test`)
"""
import contextlib
import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import compare   # noqa: E402
import itch_ref  # noqa: E402

# An Add Order built byte by byte from the table in SPEC_NOTES.md (section 1.3.1).
ADD_ORDER = bytes(
    [0x41]                                    # 0  Message Type 'A'
    + [0x00, 0x07]                            # 1  Stock Locate = 7
    + [0x00, 0x2A]                            # 3  Tracking Number = 42
    + [0x00, 0x00, 0x0B, 0xA4, 0x3B, 0x74]    # 5  Timestamp = 195_312_500 ns
    + [0, 0, 0, 0, 0, 0, 0x30, 0x39]          # 11 Order Ref = 12345
    + [0x42]                                  # 19 Buy/Sell = 'B'
    + [0x00, 0x00, 0x00, 0x64]                # 20 Shares = 100
    + list(b"AAPL    ")                       # 24 Stock = "AAPL" + 4 spaces
    + [0x00, 0x1B, 0x4C, 0x14]                # 32 Price = 1_788_948 -> 178.8948
)


class TestReferenceParser(unittest.TestCase):
    def test_add_order_is_36_bytes(self):
        self.assertEqual(len(ADD_ORDER), 36)

    def test_add_order_fields(self):
        self.assertEqual(
            itch_ref.format_message(1, ADD_ORDER),
            "1 t=41 len=36 A loc=7 trk=42 ts=195312500 ref=12345 side=B "
            "shares=100 stock=AAPL____ price=178.8948",
        )

    def test_wrong_length_is_skipped(self):
        # Same type byte, but the frame is shorter than the spec length.
        self.assertTrue(itch_ref.format_message(5, ADD_ORDER[:20]).endswith("SKIP"))
        self.assertTrue(itch_ref.format_message(5, ADD_ORDER + b"\x00").endswith("SKIP"))

    def test_unknown_type_is_skipped(self):
        self.assertEqual(itch_ref.format_message(3, b"Z\x01\x02"), "3 t=5a len=3 SKIP")

    def test_framing_and_zero_length(self):
        stream = (len(ADD_ORDER).to_bytes(2, "big") + ADD_ORDER
                  + b"\x00\x00"                        # zero-length record
                  + b"\x00\x01Z")                      # 1-byte unknown message
        msgs = list(itch_ref.iter_messages(stream))
        self.assertEqual(msgs, [ADD_ORDER, b"Z"])

    def test_truncated_file_raises(self):
        with self.assertRaises(ValueError):
            list(itch_ref.iter_messages(b"\x00\x24AB"))

    def test_price_formatting(self):
        self.assertEqual(itch_ref.price(b"\x00" * 32 + (0x77359400).to_bytes(4, "big"), 32),
                         "200000.0000")   # spec max price
        self.assertEqual(itch_ref.price(b"\x00" * 36, 32), "0.0000")

    def test_alpha_mapping(self):
        self.assertEqual(itch_ref.alpha(b"A B\x00\x7f~", 0, 6), "A_B..~")


class TestCompare(unittest.TestCase):
    """A checker that never fails proves nothing, so check that compare.py CAN fail."""

    def run_compare(self, exp_text, act_text):
        with tempfile.TemporaryDirectory() as d:
            e, a = os.path.join(d, "e.txt"), os.path.join(d, "a.txt")
            with open(e, "w") as f:
                f.write(exp_text)
            with open(a, "w") as f:
                f.write(act_text)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = compare.main(["compare.py", e, a])
            return rc, out.getvalue()

    def test_identical_passes(self):
        rc, _ = self.run_compare("1 t=44 len=19 D ref=5\n", "1 t=44 len=19 D ref=5\n")
        self.assertEqual(rc, 0)

    def test_field_mismatch_fails_and_names_field(self):
        rc, out = self.run_compare("1 t=44 len=19 D ref=5\n", "1 t=44 len=19 D ref=6\n")
        self.assertEqual(rc, 1)
        self.assertIn("ref=5", out)
        self.assertIn("ref=6", out)

    def test_missing_line_fails(self):
        rc, _ = self.run_compare("1 t=5a len=1 SKIP\n2 t=5a len=1 SKIP\n", "1 t=5a len=1 SKIP\n")
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main(verbosity=1)
