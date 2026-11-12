#!/usr/bin/env python3
"""check_rtl.py - Make sure design files (rtl/*.v) contain no simulation-only code.

System tasks like $display, $fopen, $random only work in a simulator; real
hardware cannot "print".  They belong in the testbench (tb/), never in rtl/.
This script fails if any `$name` (other than the compile-time $clog2)
appears in rtl/ outside of comments.
Also rejects `initial` blocks and `#delay`s, which are not synthesizable
on most FPGA/ASIC flows.
"""
import glob
import re
import sys

# $clog2 is the one allowed `$` function: it's a constant the compiler works
# out while reading the code (e.g. $clog2(41) = 6), so it never becomes
# hardware. Every synthesis tool supports it (Verilog-2005).
BAD = [
    (re.compile(r"\$(?!clog2\b)[a-zA-Z_]\w*"), "system task/function ($...)"),
    (re.compile(r"\binitial\b"), "initial block"),
    (re.compile(r"#\s*\d"), "#delay"),
]


def strip_comments(text):
    text = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def main():
    problems = 0
    files = sorted(glob.glob("rtl/*.v"))
    for path in files:
        with open(path) as f:
            code = strip_comments(f.read())
        for lineno, line in enumerate(code.splitlines(), 1):
            for pat, what in BAD:
                if pat.search(line):
                    print(f"{path}:{lineno}: {what}: {line.strip()}")
                    problems += 1
    if problems:
        print(f"FAIL: {problems} non-synthesizable construct(s) in rtl/")
        return 1
    print(f"PASS: rtl/ is free of simulation-only constructs ({len(files)} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
