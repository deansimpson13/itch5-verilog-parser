# Makefile for the ITCH 5.0 Verilog parser project.
#
#   make data         generate the synthetic ITCH stream + Python golden output
#   make sim          build and run the Verilog simulations
#   make test         run every check and print PASS/FAIL  (default target)
#   make clean        delete generated files (build/ and data/, but keeps data/real/)
#   make distclean    clean + delete the cached real NASDAQ slice in data/real/
#
#   make realdata     check against REAL NASDAQ data (needs internet; see below)
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

.PHONY: all data sim test clean distclean pytest lint framer_test decoder_test stall_test \
        walkthrough wave realdata real_fetch real_crosscheck real_sanity real_sim_test

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

# ---- Real NASDAQ data (phase 6) ----------------------------------------------
# NOT part of `make test`: it needs internet access (the first time) to
#   1. stream a slice of a real NASDAQ ITCH 5.0 file into data/real/
#      (tools/fetch_real.py; cached, so later runs don't download again),
#   2. install the outside parser MeatPy into build/venv
#      (tools/venv_run.py; pinned in tools/requirements-realdata.txt).
# Then it runs three independent checks on those real bytes:
#   real_crosscheck  MeatPy vs our Python model, every field of every v1 message
#   real_sanity      consistency checks that don't trust any parser
#   real_sim_test    the Verilog vs our Python model, same as `make test`
REAL       = data/real
REAL_BUILD = build/real
REAL_FILES = $(REAL)/stream.bin $(REAL)/stream.hex $(REAL)/stream_len.vh \
             $(REAL)/seg_start.bin $(REAL)/seg_open.bin

# One fetch makes all five files (grouped target, like `data` above).
$(REAL_FILES) &: tools/fetch_real.py
	$(PY) tools/fetch_real.py --out-dir $(REAL)

real_fetch:
	$(PY) tools/fetch_real.py --out-dir $(REAL)

# Python golden model on the real slice -> build/real/expected.txt
$(REAL_BUILD)/expected.txt: $(REAL)/stream.bin tools/itch_ref.py
	$(PY) tools/itch_ref.py $(REAL)/stream.bin --out-dir $(REAL_BUILD)

real_crosscheck: $(REAL)/stream.bin
	$(PY) tools/venv_run.py tools/cross_check.py $(REAL)/stream.bin \
	    --report $(REAL_BUILD)/cross_check.txt

real_sanity: $(REAL)/seg_start.bin $(REAL)/seg_open.bin
	$(PY) tools/sanity_real.py $(REAL) --report $(REAL_BUILD)/sanity.txt

# The SAME testbench as `make test`, compiled a second time with -I data/real
# so `STREAM_LEN (from data/real/stream_len.vh) sizes its memory for the
# ~16 MB real stream, and told where the hex file is with +HEX.
$(REAL_BUILD)/tb_itch.vvp: tb/tb_itch.v $(RTL) $(REAL)/stream_len.vh $(REAL_BUILD)/expected.txt
	$(IVERILOG) -g2012 -Wall -I $(REAL) -o $@ tb/tb_itch.v $(RTL)

$(REAL_BUILD)/actual.txt: $(REAL_BUILD)/tb_itch.vvp $(REAL)/stream.hex
	$(VVP) -n $(REAL_BUILD)/tb_itch.vvp +HEX=$(REAL)/stream.hex +STALL=0 +OUT=$@

$(REAL_BUILD)/actual_stall.txt: $(REAL_BUILD)/tb_itch.vvp $(REAL)/stream.hex
	$(VVP) -n $(REAL_BUILD)/tb_itch.vvp +HEX=$(REAL)/stream.hex \
	    +STALL=$(STALL) +SEED=$(SEED) +OUT=$@

real_sim_test: $(REAL_BUILD)/actual.txt $(REAL_BUILD)/actual_stall.txt
	$(PY) tools/compare.py $(REAL_BUILD)/expected.txt $(REAL_BUILD)/actual.txt
	$(PY) tools/compare.py $(REAL_BUILD)/expected.txt $(REAL_BUILD)/actual_stall.txt

realdata: real_crosscheck real_sanity real_sim_test
	@echo ==================================================
	@echo   REAL DATA CHECKS PASSED - reports in build/real/
	@echo ==================================================

# ---- Cleanup -----------------------------------------------------------------
clean:
	$(PY) tools/clean.py

distclean:
	$(PY) tools/clean.py --all
