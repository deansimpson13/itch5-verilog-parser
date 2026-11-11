#!/usr/bin/env python3
"""check_rtl.py - Make sure design files (rtl/*.v) contain no simulation-only code.

System tasks like $display, $fopen, $random only work in a simulator; real
hardware cannot "print".  They belong in the testbench (tb/), never in rtl/.
This script fails if any `$name` appears in rtl/ outside of comments.
Also rejects `initial` blocks and `#delay`s, which are not synthesizable
on most FPGA/ASIC flows.
"""
import glob
import re
import sys

BAD = [
    (re.compile(r"\$[a-zA-Z_]\w*"), "system task/function ($...)"),
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
