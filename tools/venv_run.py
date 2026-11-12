#!/usr/bin/env python3
"""
venv_run.py - Run a script with the Python inside build/venv.

Used by `make realdata` to run tools/cross_check.py, which needs MeatPy (the
outside ITCH parser). MeatPy is installed into a private virtual environment
in build/venv, at the versions pinned in tools/requirements-realdata.txt, so
it never touches your global Python and `make test` stays dependency-free.

The first run creates the venv and installs the packages (needs internet).
Later runs reuse it. If the requirements file changes, it reinstalls.

Why a Python helper instead of calling build/venv/Scripts/python.exe from
the Makefile: the venv's python lives at a different path on Windows
(Scripts\\python.exe) and Linux/macOS (bin/python), and cmd.exe can't run a
program path written with forward slashes. Starting from plain `python`
works everywhere, the same as every other Makefile recipe.

Usage:  python tools/venv_run.py <script.py> [args...]
"""

import os
import subprocess
import sys
import venv

VENV = os.path.join("build", "venv")
REQUIREMENTS = os.path.join("tools", "requirements-realdata.txt")
STAMP = os.path.join(VENV, "installed-requirements.txt")   # what's installed now


def venv_python():
    if os.name == "nt":
        return os.path.join(VENV, "Scripts", "python.exe")
    return os.path.join(VENV, "bin", "python")


def ensure_venv():
    with open(REQUIREMENTS) as f:
        wanted = f.read()
    if os.path.exists(STAMP) and os.path.exists(venv_python()):
        with open(STAMP) as f:
            if f.read() == wanted:
                return                              # already set up
    if not os.path.exists(venv_python()):
        print(f"venv_run: creating {VENV}")
        venv.create(VENV, with_pip=True)
    print(f"venv_run: installing {REQUIREMENTS} into {VENV}")
    subprocess.run([venv_python(), "-m", "pip", "install", "--disable-pip-version-check",
                    "-q", "-r", REQUIREMENTS], check=True)
    with open(STAMP, "w") as f:
        f.write(wanted)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    ensure_venv()
    # Run the script and pass its exit code back to make.
    return subprocess.run([venv_python()] + sys.argv[1:]).returncode


if __name__ == "__main__":
    sys.exit(main())
