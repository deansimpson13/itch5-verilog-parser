#!/usr/bin/env python3
"""
synth_report.py - Synthesize the parser for real FPGAs and report measured numbers.

Run by `make synth`. Everything it writes goes to build/synth/.

  1. iCE40 (Lattice iCE40-HX8K, ct256 package), itch_top on its own:
       Yosys synth_ice40 -> LUT and flip-flop counts for just the parser.
  2. iCE40, itch_top inside synth/synth_top.v (the scan-chain wrapper):
       Yosys synth_ice40, then nextpnr-ice40 place-and-route with several
       random seeds -> Fmax (the highest clock frequency at which every
       signal still arrives in time). The wrapper exists because itch_top
       has ~650 output bits and the HX8K has 206 pins; see synth_top.v.
  3. Xilinx Artix-7, itch_top on its own:
       Yosys synth_xilinx -> LUT and flip-flop counts. This is
       POST-SYNTHESIS only (no place-and-route, so no Fmax). Real Artix-7
       timing needs Vivado, which is not installed here.

Checks that synthesis didn't quietly throw logic away:
  - Yosys `check -assert` fails the run on undriven wires, multiple drivers,
    or combinational loops.
  - No itch_top output bit may be a constant in the netlist, except in_ready
    (which is tied to 1 on purpose in v1).
  - itch_top must have at least FF_FLOOR flip-flops: 320 for the 40-byte
    message buffer + 642 for the registered outputs. If synthesis removed
    any of those, the count would drop below the floor.

Then it works out throughput (1 byte per clock) and compares it with 10 Gb
Ethernet line rate.

Tool location: uses `yosys` / `nextpnr-ice40` from PATH if they are there.
Otherwise it looks for the YosysHQ OSS CAD Suite in $OSS_CAD_SUITE or
~/oss-cad-suite, and adds its bin/ and lib/ folders to PATH ONLY for the
commands it runs. (The suite also ships its own iverilog/python, so putting
it on your PATH permanently could shadow the ones `make test` uses.)

Usage:  python tools/synth_report.py [--seeds 1,2,3,4,5] [--freq 100]
"""

import argparse
import json
import os
import re
import shutil
import statistics
import subprocess
import sys

OUT = os.path.join("build", "synth")
RTL = ["rtl/itch_framer.v", "rtl/itch_decoder.v", "rtl/itch_top.v"]
WRAPPER = "synth/synth_top.v"

FF_FLOOR = 320 + 642            # message buffer + registered outputs (see docstring)
WRAPPER_SCAN_BITS = 641         # width of CAP in synth_top.v

ICE40_DEVICE = ["--hx8k", "--package", "ct256"]
TEN_GBE_BITS_PER_SEC = 10e9

# ---------------------------------------------------------------------------
# Yosys scripts.
#
# Normally `synth_ice40` and `synth_xilinx` map logic into LUTs with the
# `abc9` pass. In this Yosys build (0.69, OSS CAD Suite 2026-10-01, Windows)
# abc9 crashes inside its new experimental `write_xaiger2` backend (an
# assertion about file positions, a Windows text/binary file issue). So the
# scripts run the standard flow up to the LUT-mapping step, do that one step
# with the classic `abc` pass (synth_ice40's default for years), and then
# continue the standard flow from the next step. Everything else is stock.
# See docs/DECISIONS.md, "Synthesis".
# ---------------------------------------------------------------------------
def ice40_script(top, files, stat_json, netlist_json):
    return "; ".join([
        f"read_verilog {' '.join(files)}",
        f"synth_ice40 -top {top} -run :map_luts",        # standard flow, part 1
        # --- replacement for the map_luts step (classic abc instead of abc9) ---
        "check -latchonly -assert",
        "techmap -map +/ice40/latches_map.v",
        "abc -dress -lut 4",                             # map logic into 4-input LUTs
        "ice40_wrapcarry -unwrap",
        "techmap -map +/ice40/ff_map.v",
        "clean",
        "opt_lut -tech ice40",
        # --- back to the standard flow ---
        f"synth_ice40 -top {top} -run map_cells:",
        "check -assert",                                 # fail on undriven/multi-driven nets
        f"tee -q -o {stat_json} stat -json",
        f"write_json {netlist_json}",
    ])


def xilinx_script(top, files, stat_json, netlist_json):
    flow = f"synth_xilinx -family xc7 -top {top} -noiopad -flatten"
    return "; ".join([
        f"read_verilog {' '.join(files)}",
        f"{flow} -run :map_luts",
        # --- replacement for map_luts (same LUT cost table as the standard abc9 call) ---
        "opt_expr -mux_undef -noclkinv",
        "abc -luts 2:2,3,6:5,10,20",
        "clean",
        "xilinx_srl -fixed -minlen 3",
        "techmap -map +/xilinx/lut_map.v -map +/xilinx/cells_map.v -D LUT_WIDTH=6",
        "xilinx_dffopt",
        "opt_lut_ins -tech xilinx",
        # --- back to the standard flow ---
        f"{flow} -run finalize:check",
        "check -assert",
        f"tee -q -o {stat_json} stat -json",
        # Write only our module: the Xilinx cell-library models (FDRE, LUT6, ...)
        # are loaded as behavioural Verilog that the JSON writer won't accept.
        f"select {top}",
        f"write_json -selected {netlist_json}",
    ])


# ---------------------------------------------------------------------------
def find_tools(yosys, nextpnr):
    """Return (yosys, nextpnr, env) with PATH extended if the OSS CAD Suite is needed."""
    env = dict(os.environ)
    if shutil.which(yosys) and shutil.which(nextpnr):
        return yosys, nextpnr, env
    suite = os.environ.get("OSS_CAD_SUITE") or os.path.join(os.path.expanduser("~"), "oss-cad-suite")
    bindir = os.path.join(suite, "bin")
    if not os.path.isdir(bindir):
        sys.exit("synth_report: yosys / nextpnr-ice40 not found on PATH, and no OSS CAD Suite at "
                 f"{suite}.\nInstall it (see README, Prerequisites) or set OSS_CAD_SUITE.")
    env["PATH"] = os.pathsep.join([bindir, os.path.join(suite, "lib"), env.get("PATH", "")])
    found = lambda t: shutil.which(t, path=env["PATH"])
    if not (found(yosys) and found(nextpnr)):
        sys.exit(f"synth_report: {suite} does not contain {yosys} and {nextpnr}")
    print(f"synth_report: using tools from {bindir}")
    return found(yosys), found(nextpnr), env


def run(cmd, log, env):
    """Run a tool, saving everything it prints to `log`. Stop on failure."""
    with open(log, "w") as f:
        rc = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, env=env).returncode
    if rc != 0:
        with open(log) as f:
            tail = f.readlines()[-15:]
        sys.exit(f"synth_report: FAILED ({rc}): {' '.join(cmd[:2])} ...\n  see {log}\n"
                 + "".join("    " + ln for ln in tail))


def cell_counts(stat_json):
    with open(stat_json) as f:
        return json.load(f)["design"]["num_cells_by_type"]


def ice40_ffs(c):
    return sum(v for k, v in c.items() if k.startswith("SB_DFF"))


def xilinx_luts(c):
    return sum(v for k, v in c.items() if re.fullmatch(r"LUT[1-6]", k))


def xilinx_ffs(c):
    return sum(v for k, v in c.items() if k in ("FDRE", "FDSE", "FDCE", "FDPE"))


def constant_outputs(netlist_json, top):
    """Names of top-level output bits that synthesis turned into constants."""
    with open(netlist_json) as f:
        ports = json.load(f)["modules"][top]["ports"]
    bad = []
    for name, p in ports.items():
        if p["direction"] != "output" or name == "in_ready":
            continue
        # In Yosys JSON, a bit is a net number (int) or a constant ("0", "1", "x").
        bad += [f"{name}[{i}]" for i, b in enumerate(p["bits"]) if isinstance(b, str)]
    return bad


def parse_fmax(log):
    """nextpnr's achieved Fmax (MHz) for the clock domain, from its log."""
    with open(log) as f:
        text = f.read()
    m = re.findall(r"Max frequency for clock '[^']*clk[^']*': ([0-9.]+) MHz", text)
    if not m:
        sys.exit(f"synth_report: no Fmax found in {log}")
    return float(m[-1])        # the last report is the final, post-routing one


def critical_path(log):
    """Short summary of the post-route clk->clk critical path from a nextpnr log."""
    with open(log) as f:
        text = f.read()
    blocks = text.split("Critical path report for clock")
    if len(blocks) < 2:
        return []
    block = blocks[-1].split("Critical path report for cross-domain")[0]
    steps = re.findall(r"Info:\s+(clk-to-q|logic|routing|setup)\s+([0-9.]+)\s+([0-9.]+)\s+(.*)", block)
    split = re.findall(r"Info: ([0-9.]+) ns logic, ([0-9.]+) ns routing", text.split(blocks[-1])[0] + block)
    first, last = steps[0], steps[-1]
    out = [f"starts at {first[3].replace('Source ', '')}",
           f"ends at   {last[3].replace('Source ', '')}",
           f"{len([s for s in steps if s[0] == 'logic'])} logic steps, total {last[2]} ns"]
    if split:
        out.append(f"{split[-1][0]} ns in logic, {split[-1][1]} ns in routing (wires)")
    return out


def main():
    ap = argparse.ArgumentParser(description="Synthesize itch_top and report measured results")
    ap.add_argument("--yosys", default="yosys")
    ap.add_argument("--nextpnr", default="nextpnr-ice40")
    ap.add_argument("--seeds", default="1,2,3,4,5",
                    help="nextpnr placement seeds; Fmax changes a little with each")
    ap.add_argument("--freq", type=float, default=100.0,
                    help="target clock for nextpnr (MHz); only steers effort, Fmax is what's achieved")
    args = ap.parse_args()
    seeds = [int(s) for s in args.seeds.split(",")]
    yosys, nextpnr, env = find_tools(args.yosys, args.nextpnr)
    os.makedirs(OUT, exist_ok=True)
    p = lambda name: os.path.join(OUT, name)
    failures = []

    # ---- 1. iCE40, itch_top alone: resource counts of the parser itself ----
    print("synth_report: [1/3] Yosys synth_ice40, itch_top alone")
    run([yosys, "-l", p("ice40_itch_top.log"), "-q", "-p",
         ice40_script("itch_top", RTL, p("ice40_itch_top_stat.json"), p("ice40_itch_top.json"))],
        p("ice40_itch_top.out"), env)
    ice = cell_counts(p("ice40_itch_top_stat.json"))
    ice_ff, ice_lut = ice40_ffs(ice), ice.get("SB_LUT4", 0)
    consts = constant_outputs(p("ice40_itch_top.json"), "itch_top")
    if consts:
        failures.append(f"iCE40 netlist has constant outputs: {consts[:8]}")
    if ice_ff < FF_FLOOR:
        failures.append(f"iCE40 itch_top has {ice_ff} flip-flops, below the floor of {FF_FLOOR}")

    # ---- 2. iCE40, wrapped: place and route for Fmax ----
    print("synth_report: [2/3] Yosys synth_ice40 + nextpnr-ice40, synth_top wrapper "
          f"(seeds {args.seeds})")
    run([yosys, "-l", p("ice40_synth_top.log"), "-q", "-p",
         ice40_script("synth_top", RTL + [WRAPPER], p("ice40_synth_top_stat.json"),
                      p("ice40_synth_top.json"))],
        p("ice40_synth_top.out"), env)
    wrap = cell_counts(p("ice40_synth_top_stat.json"))
    if ice40_ffs(wrap) < FF_FLOOR + WRAPPER_SCAN_BITS:
        failures.append(f"wrapped design has {ice40_ffs(wrap)} flip-flops, below "
                        f"{FF_FLOOR + WRAPPER_SCAN_BITS}")
    runs = []
    for seed in seeds:
        log = p(f"nextpnr_seed{seed}.log")
        run([nextpnr, *ICE40_DEVICE, "--json", p("ice40_synth_top.json"),
             "--pcf-allow-unconstrained",        # no board: let nextpnr pick the pins
             "--freq", str(args.freq), "--timing-allow-fail", "--seed", str(seed),
             "--report", p(f"nextpnr_seed{seed}_report.json")], log, env)
        with open(p(f"nextpnr_seed{seed}_report.json")) as f:
            util = json.load(f)["utilization"]
        runs.append({"seed": seed, "fmax": parse_fmax(log),
                     "lc": util["ICESTORM_LC"]["used"], "lc_avail": util["ICESTORM_LC"]["available"],
                     "io": util["SB_IO"]["used"], "io_avail": util["SB_IO"]["available"]})
        print(f"    seed {seed}: Fmax {runs[-1]['fmax']:.2f} MHz")
    fmaxes = [r["fmax"] for r in runs]
    fmax_med, fmax_min = statistics.median(fmaxes), min(fmaxes)
    worst = min(runs, key=lambda r: r["fmax"])
    crit = critical_path(p(f"nextpnr_seed{worst['seed']}.log"))

    # ---- 3. Artix-7, itch_top alone: post-synthesis resource counts ----
    print("synth_report: [3/3] Yosys synth_xilinx (Artix-7), itch_top alone")
    run([yosys, "-l", p("xilinx_itch_top.log"), "-q", "-p",
         xilinx_script("itch_top", RTL, p("xilinx_itch_top_stat.json"), p("xilinx_itch_top.json"))],
        p("xilinx_itch_top.out"), env)
    xc = cell_counts(p("xilinx_itch_top_stat.json"))
    xc_ff, xc_lut = xilinx_ffs(xc), xilinx_luts(xc)
    consts = constant_outputs(p("xilinx_itch_top.json"), "itch_top")
    if consts:
        failures.append(f"Artix-7 netlist has constant outputs: {consts[:8]}")
    if xc_ff < FF_FLOOR:
        failures.append(f"Artix-7 itch_top has {xc_ff} flip-flops, below the floor of {FF_FLOOR}")

    vivado = shutil.which("vivado")

    # ---- Throughput: 1 byte per clock ----
    # bits/sec = Fmax (clocks/sec) x 1 byte/clock x 8 bits/byte
    gbps = lambda mhz: mhz * 1e6 * 8 / 1e9
    need_mhz = TEN_GBE_BITS_PER_SEC / 8 / 1e6        # clock needed for 10 Gb/s at 1 B/clk

    # ---- Report ----
    L = []
    L.append("SYNTHESIS RESULTS (measured; raw tool output in build/synth/)")
    L.append("")
    L.append("Lattice iCE40-HX8K (ct256), Yosys synth_ice40 + nextpnr-ice40")
    L.append(f"  itch_top alone (Yosys):   {ice_lut:,} LUT4   {ice_ff:,} flip-flops   "
             f"{ice.get('SB_CARRY', 0)} carry cells")
    L.append(f"  with scan-chain wrapper:  {wrap.get('SB_LUT4', 0):,} LUT4   {ice40_ffs(wrap):,} flip-flops"
             f"   (wrapper adds {WRAPPER_SCAN_BITS} FFs + a 2-to-1 mux per bit)")
    L.append(f"  after place-and-route:    {runs[0]['lc']:,} / {runs[0]['lc_avail']:,} logic cells "
             f"({100 * runs[0]['lc'] / runs[0]['lc_avail']:.0f}%), "
             f"{runs[0]['io']} / {runs[0]['io_avail']} I/O pins")
    L.append(f"  Fmax over {len(seeds)} seeds:     " +
             ", ".join(f"{r['fmax']:.2f}" for r in runs) + " MHz")
    L.append(f"                            median {fmax_med:.2f} MHz, worst {fmax_min:.2f} MHz")
    L.append(f"  critical path (worst seed, {worst['seed']}):")
    L += [f"    {c}" for c in crit]
    L.append("")
    L.append("Xilinx Artix-7 (xc7), Yosys synth_xilinx: POST-SYNTHESIS ONLY, not placed or routed")
    L.append(f"  itch_top alone:           {xc_lut:,} LUTs   {xc_ff:,} flip-flops   "
             f"{xc.get('MUXF7', 0)} MUXF7, {xc.get('MUXF8', 0)} MUXF8, {xc.get('CARRY4', 0)} CARRY4")
    L.append("  " + ("Vivado found on PATH, but this script does not run it yet."
                     if vivado else "Vivado not installed, so no Artix-7 Fmax."))
    L.append("")
    L.append("Throughput at 1 byte per clock (iCE40 worst-seed Fmax)")
    L.append(f"  {fmax_min:.2f} MHz x 1 byte = {fmax_min:.2f} MB/s = {gbps(fmax_min):.3f} Gb/s")
    L.append(f"  10 Gb Ethernet line rate = 10 Gb/s = 1,250 MB/s, which needs {need_mhz:,.0f} MHz at 1 byte/clock")
    L.append(f"  -> keeps up with 10GbE? NO. It reaches {100 * gbps(fmax_min) / 10:.1f}% of line rate.")
    L.append("")
    L.append("Checks against logic being optimized away")
    L.append(f"  Yosys `check -assert` (undriven / multi-driven / loops): pass")
    L.append(f"  no constant output bits (except in_ready, tied to 1 by design): "
             f"{'pass' if not any('constant' in f for f in failures) else 'FAIL'}")
    L.append(f"  itch_top flip-flops >= {FF_FLOOR} (320 buffer + 642 output regs): "
             f"iCE40 {ice_ff}, Artix-7 {xc_ff}: "
             f"{'pass' if ice_ff >= FF_FLOOR and xc_ff >= FF_FLOOR else 'FAIL'}")
    for f in failures:
        L.append(f"  FAIL: {f}")

    text = "\n".join(L)
    print()
    print(text)
    with open(p("summary.txt"), "w", newline="\n") as f:
        f.write(text + "\n")
    summary = {
        "ice40": {"device": "iCE40-HX8K ct256", "itch_top_lut4": ice_lut, "itch_top_ff": ice_ff,
                  "wrapper_lut4": wrap.get("SB_LUT4", 0), "wrapper_ff": ice40_ffs(wrap),
                  "pnr_runs": runs, "fmax_median_mhz": fmax_med, "fmax_worst_mhz": fmax_min},
        "artix7_post_synthesis": {"itch_top_lut": xc_lut, "itch_top_ff": xc_ff, "cells": xc},
        "throughput": {"gbps_at_worst_fmax": gbps(fmax_min), "ten_gbe_mhz_needed": need_mhz,
                       "fraction_of_10gbe": gbps(fmax_min) / 10},
        "failures": failures,
    }
    with open(p("summary.json"), "w", newline="\n") as f:
        json.dump(summary, f, indent=2)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
