#!/usr/bin/env python3
"""clean.py - Delete generated files.

    python tools/clean.py          (make clean)      build/ and data/, but KEEP data/real/
    python tools/clean.py --all    (make distclean)  everything, including data/real/

data/real/ holds the slice of real NASDAQ data from tools/fetch_real.py.
Re-fetching it means streaming ~135 MB from nasdaq.com, so plain `make clean`
leaves it alone.

Written in Python so it works the same on Windows (where `rm -rf` does not
exist) and on Linux/macOS.
"""
import os
import shutil
import sys

everything = "--all" in sys.argv

shutil.rmtree("build", ignore_errors=True)
print("clean: removed build/")

if everything or not os.path.isdir(os.path.join("data", "real")):
    shutil.rmtree("data", ignore_errors=True)
    print("clean: removed data/")
else:
    # Remove everything in data/ except the real-data cache.
    for name in os.listdir("data"):
        path = os.path.join("data", name)
        if name == "real":
            continue
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        else:
            os.remove(path)
    print("clean: removed data/ (kept data/real/; `make distclean` removes it too)")
