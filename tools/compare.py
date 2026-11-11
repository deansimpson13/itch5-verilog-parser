#!/usr/bin/env python3
"""
compare.py - Diff the golden model's output against the Verilog output.

Usage:  python tools/compare.py build/expected.txt build/actual.txt [--only 41,46]

Prints PASS and exits 0 if the files match line for line.
--only keeps just the lines whose type byte (t=..) is in the list, which is
handy while only some message types are implemented.
Otherwise it prints the FIRST mismatch with a few lines of context, says which
field differs, and exits 1 (nonzero exit makes `make` stop and report failure).
"""

import sys

CONTEXT = 3   # how many matching lines to show before the mismatch


def read_lines(path):
    with open(path, "r", encoding="ascii", errors="replace") as f:
        return [line.rstrip("\r\n") for line in f]


def field_diff(exp, act):
    """Explain which space-separated token differs between two lines."""
    e, a = exp.split(), act.split()
    for i in range(max(len(e), len(a))):
        ev = e[i] if i < len(e) else "<missing>"
        av = a[i] if i < len(a) else "<missing>"
        if ev != av:
            return f"  first differing field (#{i}): expected {ev!r}, got {av!r}"
    return ""


def main(argv):
    only = None
    if "--only" in argv:
        i = argv.index("--only")
        only = {"t=" + code.lower() for code in argv[i + 1].split(",")}
        argv = argv[:i] + argv[i + 2:]
    if len(argv) != 3:
        print(__doc__)
        return 2
    exp_path, act_path = argv[1], argv[2]
    exp, act = read_lines(exp_path), read_lines(act_path)
    if only:
        exp = [ln for ln in exp if ln.split()[1] in only]
        act = [ln for ln in act if ln.split()[1] in only]
        if not exp:
            print(f"FAIL: no expected lines with type in {sorted(only)}")
            return 1

    for i in range(min(len(exp), len(act))):
        if exp[i] != act[i]:
            print(f"FAIL: mismatch at line {i + 1} ({exp_path} vs {act_path})")
            for j in range(max(0, i - CONTEXT), i):
                print(f"    ok   {j + 1:6d}: {exp[j]}")
            print(f"  > exp  {i + 1:6d}: {exp[i]}")
            print(f"  > act  {i + 1:6d}: {act[i]}")
            print(field_diff(exp[i], act[i]))
            return 1

    if len(exp) != len(act):
        print(f"FAIL: line count differs: expected {len(exp)}, actual {len(act)}")
        n = min(len(exp), len(act))
        longer, name = (exp, "expected") if len(exp) > len(act) else (act, "actual")
        print(f"  first extra {name} line ({n + 1}): {longer[n]}")
        return 1

    print(f"PASS: {len(exp)} lines match ({act_path})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
