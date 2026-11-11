# Makefile for the ITCH 5.0 Verilog parser project.
#
#   make data         generate the synthetic ITCH stream + Python golden output
#   make sim          build and run the Verilog simulations
#   make test         run every check and print PASS/FAIL  (default target)
#   make clean        delete everything generated (build/ and data/)
#
#   make walkthrough  cycle-by-cycle trace of one Add Order (see docs/WALKTHROUGH.md)
#   make wave         write build/wave.vcd for GTKWave (first 2,000 cycles)
#
# Recipes only call python / iverilog / vvp, so the same Makefile works in
# Windows cmd.exe and in a Linux/macOS shell.

PY       ?= python
IVERILOG ?= iverilog
VVP      ?= vvp

# -g2012 lets the testbenches use $fatal (makes vvp exit nonzero on failure).
# The RTL itself only uses plain Verilog-2001 constructs.
IVFLAGS  = -g2012 -Wall -I data

# Random-stall settings (override like: make test STALL=50 SEED=7)
STALL  ?= 30
SEED   ?= 1
STALL2 ?= 75
SEED2  ?= 99

RTL_FRAMER = rtl/itch_framer.v
RTL        = rtl/itch_framer.v rtl/itch_decoder.v rtl/itch_top.v

.PHONY: all data sim test clean pytest lint framer_test decoder_test stall_test \
        walkthrough wave

all: test

# ---- Test data + golden model (phase 1) --------------------------------------
# One generator run makes all three files ("&:" = grouped target, Make 4.3+).
data/stream.bin data/stream.hex data/stream_len.vh &: tools/gen_itch.py
	$(PY) tools/gen_itch.py --out-dir data

build/expected.txt build/frames_expected.txt &: data/stream.bin tools/itch_ref.py
	$(PY) tools/itch_ref.py data/stream.bin --out-dir build

data: data/stream.hex build/expected.txt

# Unit tests for the golden model and the compare tool.
pytest:
	$(PY) tools/test_ref.py

# RTL must not contain simulation-only code ($display, initial, #delay, ...).
lint:
	$(PY) tools/check_rtl.py

# ---- Framer on its own (phase 2) --------------------------------------------
# (build/expected.txt is listed so build/ exists before iverilog writes there.)
build/tb_framer.vvp: tb/tb_framer.v $(RTL_FRAMER) data/stream_len.vh build/expected.txt
	$(IVERILOG) $(IVFLAGS) -o $@ tb/tb_framer.v $(RTL_FRAMER)

framer_test: build/tb_framer.vvp build/frames_expected.txt
	$(VVP) -n build/tb_framer.vvp +STALL=0 +OUT=build/frames_actual.txt
	$(PY) tools/compare.py build/frames_expected.txt build/frames_actual.txt
	$(VVP) -n build/tb_framer.vvp +STALL=$(STALL) +SEED=$(SEED) +OUT=build/frames_actual_stall.txt
	$(PY) tools/compare.py build/frames_expected.txt build/frames_actual_stall.txt

# ---- Full parser (phases 3-5) -----------------------------------------------
build/tb_itch.vvp: tb/tb_itch.v $(RTL) data/stream_len.vh build/expected.txt
	$(IVERILOG) $(IVFLAGS) -o $@ tb/tb_itch.v $(RTL)

# Back-to-back: in_valid held high every cycle, so messages arrive with no gaps.
build/actual.txt: build/tb_itch.vvp data/stream.hex
	$(VVP) -n build/tb_itch.vvp +STALL=0 +OUT=build/actual.txt

# Random stalls: in_valid dropped on STALL% / STALL2% of cycles.
build/actual_stall.txt: build/tb_itch.vvp data/stream.hex
	$(VVP) -n build/tb_itch.vvp +STALL=$(STALL) +SEED=$(SEED) +OUT=build/actual_stall.txt

build/actual_stall2.txt: build/tb_itch.vvp data/stream.hex
	$(VVP) -n build/tb_itch.vvp +STALL=$(STALL2) +SEED=$(SEED2) +OUT=build/actual_stall2.txt

sim: build/tb_framer.vvp build/actual.txt build/actual_stall.txt build/actual_stall2.txt

decoder_test: build/actual.txt
	$(PY) tools/compare.py build/expected.txt build/actual.txt

stall_test: build/actual_stall.txt build/actual_stall2.txt
	$(PY) tools/compare.py build/expected.txt build/actual_stall.txt
	$(PY) tools/compare.py build/expected.txt build/actual_stall2.txt

test: pytest data lint framer_test decoder_test stall_test
	@echo ==================================================
	@echo   ALL TESTS PASSED
	@echo ==================================================

# ---- Learning aids -----------------------------------------------------------
build/tb_walkthrough.vvp: tb/tb_walkthrough.v $(RTL) build/expected.txt
	$(IVERILOG) $(IVFLAGS) -o $@ tb/tb_walkthrough.v $(RTL)

walkthrough: build/tb_walkthrough.vvp
	$(VVP) -n build/tb_walkthrough.vvp

wave: build/tb_itch.vvp data/stream.hex
	$(VVP) -n build/tb_itch.vvp +DUMP +OUT=build/actual_wave.txt
	@echo Open the waveform with:  gtkwave build/wave.vcd

clean:
	$(PY) tools/clean.py
