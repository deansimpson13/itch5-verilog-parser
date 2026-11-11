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

# -g2012 lets the testbenches use $fatal (makes vvp exit nonzero on failure).
# The RTL itself only uses plain Verilog-2001 constructs.
IVFLAGS  = -g2012 -Wall -I data

# Random-stall test settings (override like: make test STALL=50 SEED=7)
STALL ?= 30
SEED  ?= 1

RTL_FRAMER = rtl/itch_framer.v

.PHONY: all data sim test clean pytest framer_test

all: test

# ---- Phase 1: Python generator + golden model ------------------------------
data/stream.bin data/stream.hex data/stream_len.vh &: tools/gen_itch.py
	$(PY) tools/gen_itch.py --out-dir data

build/expected.txt build/frames_expected.txt &: data/stream.bin tools/itch_ref.py
	$(PY) tools/itch_ref.py data/stream.bin --out-dir build

data: data/stream.hex build/expected.txt

pytest:
	$(PY) tools/test_ref.py

# ---- Phase 2: framer on its own ---------------------------------------------
build/tb_framer.vvp: tb/tb_framer.v $(RTL_FRAMER) data/stream_len.vh build/expected.txt
	$(IVERILOG) $(IVFLAGS) -o $@ tb/tb_framer.v $(RTL_FRAMER)

framer_test: build/tb_framer.vvp build/frames_expected.txt
	$(VVP) -n build/tb_framer.vvp +STALL=0 +OUT=build/frames_actual.txt
	$(PY) tools/compare.py build/frames_expected.txt build/frames_actual.txt
	$(VVP) -n build/tb_framer.vvp +STALL=$(STALL) +SEED=$(SEED) +OUT=build/frames_actual_stall.txt
	$(PY) tools/compare.py build/frames_expected.txt build/frames_actual_stall.txt

sim: build/tb_framer.vvp

test: pytest data framer_test

clean:
	$(PY) tools/clean.py
