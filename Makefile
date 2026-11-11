# Makefile for the ITCH 5.0 Verilog parser project.
#
#   make data   generate the synthetic ITCH stream + Python golden output
#   make sim    build and run the Verilog simulations
#   make test   run every check and print PASS/FAIL
#   make clean  delete everything generated (build/ and data/)
#
# Recipes only call python / iverilog / vvp, so the same Makefile works in
# Windows cmd.exe and in a Linux/macOS shell.

PY       ?= python
IVERILOG ?= iverilog
VVP      ?= vvp

.PHONY: all data sim test clean pytest

all: test

# ---- Phase 1: Python generator + golden model ------------------------------
data/stream.bin data/stream.hex data/stream_len.vh &: tools/gen_itch.py
	$(PY) tools/gen_itch.py --out-dir data

build/expected.txt build/frames_expected.txt &: data/stream.bin tools/itch_ref.py
	$(PY) tools/itch_ref.py data/stream.bin --out-dir build

data: data/stream.hex build/expected.txt

pytest:
	$(PY) tools/test_ref.py

test: pytest data

clean:
	$(PY) tools/clean.py
