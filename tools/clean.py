#!/usr/bin/env python3
"""clean.py - Delete generated folders (build/ and data/).

Used by `make clean`.  Written in Python so it works the same on Windows
(where `rm -rf` does not exist) and on Linux/macOS.
"""
import shutil

for d in ("build", "data"):
    shutil.rmtree(d, ignore_errors=True)
    print(f"clean: removed {d}/")
