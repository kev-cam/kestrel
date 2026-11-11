"""Transistor-level SPICE netlist emitter for charge-pump PLL.

Generates subcircuits for each PLL block using MOSFET models from the
target process (SKY130 or GF180).  The sizing comes from the design
engine's analytical computation.

Each subcircuit is a structurally correct transistor-level implementation:
  - VCO: Maneatis differential ring oscillator with symmetric load
  - PFD: NAND-based tri-state phase-frequency detector
  - Charge pump: cascode current mirrors with UP/DN switches
  - Loop filter: passive R-C1-C2
  - Divider: chain of toggle flip-flops (static, 2^n)
  - Top: full PLL with all blocks wired together
"""

import os
import sys
import math
import shutil
from kestrel.process import get_process_params, format_eng
from ..engine import PLLDesign

# Bundled process model files live with the simulation decks (sim/models).
_SIM_MODELS_DIR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", "sim", "models"))


def emit_spice(design: PLLDesign, output_dir: str) -> list:
    """Generate SPICE netlist files for a complete PLL.

    Returns list of generated file paths.
    """
    os.makedirs(output_dir, exist_ok=True)
    files = []
    files.append(_write_vco(design, output_dir))
    files.append(_write_pfd(design, output_dir))
    files.append(_write_charge_pump(design, output_dir))
    files.append(_write_loop_filter(design, output_dir))
    files.append(_write_divider(design, output_dir))
    files.append(_write_pll_top(design, output_dir))
    files.append(_write_testbench(design, output_dir))
    files.append(_write_vco_testbench(design, output_dir))
    files += _copy_model_files(design, output_dir)
    return files


def _bundled_models(proc: dict) -> list:
    """Absolute paths of the process's bundled model files, or [] if any is missing."""
    names = proc.get("model_files", [])
    paths = [os.path.join(_SIM_MODELS_DIR, n) for n in names]
    return paths if names and all(os.path.isfile(p) for p in paths) else []


def _model_section(proc: dict) -> str:
    """Testbench header: real .INCLUDEs when models are bundled, else a PDK placeholder."""
    stars = "*" * 70
    if _bundled_models(proc):
        inc = "".join(f'.INCLUDE "models/{n}"\n' for n in proc["model_files"])
        return (f"{stars}\n"
                f"* Process models — bundled Xyce-native cards, copied to ./models/\n"
                f"* (run from this directory; or swap in the PDK's own library:\n"
                f"*  .LIB \"<path_to_pdk>/models/{proc['model_lib']}\" {proc['corner']})\n"
                f"{stars}\n{inc}")
    return (f"{stars}\n"
            f"* Process models — user must provide the correct path\n"
            f"{stars}\n"
            f"* .LIB \"<path_to_pdk>/models/{proc['model_lib']}\" {proc['corner']}\n")


def _copy_model_files(design: PLLDesign, output_dir: str) -> list:
    """Copy the process's bundled model files into <output_dir>/models/."""
    proc = get_process_params(design.spec.process)
    srcs = _bundled_models(proc)
    if proc.get("model_files") and not srcs:
        print(f"  warning: bundled {design.spec.process} model files not found under "
              f"{_SIM_MODELS_DIR}; testbench left with a PDK placeholder", file=sys.stderr)
        return []
    if not srcs:
        return []
    dst_dir = os.path.join(output_dir, "models")
    os.makedirs(dst_dir, exist_ok=True)
    out = []
    for src in srcs:
        dst = os.path.join(dst_dir, os.path.basename(src))
        shutil.copyfile(src, dst)
        out.append(dst)
    return out


def _um(val: float) -> str:
    """Format a dimension in micrometers."""
    return f"{val*1e6:.3f}u"


def _mosfet(name: str, d: str, g: str, s: str, b: str,
            model: str, w: str, l: str, prefix: str = "M") -> str:
    """Format a MOSFET instance line with the correct prefix.

    M prefix for direct .MODEL cards (sky130, gf180).
    X prefix for .SUBCKT wrappers (sg13g2).
    """
    return f"{prefix}{name} {d} {g} {s} {b} {model} W={w} L={l}"


def _eng(val: float) -> str:
    """Format a value with SPICE suffix."""
    if val == 0:
        return "0"
    av = abs(val)
    sign = "-" if val < 0 else ""
    for scale, suffix in [(1e12, "T"), (1e9, "G"), (1e6, "MEG"),
                           (1e3, "k"), (1, ""), (1e-3, "m"),
                           (1e-6, "u"), (1e-9, "n"), (1e-12, "p"),
                           (1e-15, "f")]:
        if av >= scale * 0.999:
            return f"{sign}{val/scale:.4g}{suffix}"
    return f"{sign}{val:.4g}"


def _header(design: PLLDesign) -> str:
    s = design.spec
    proc = get_process_params(s.process)
    return f"""\
* Kestrel PLL Generator v0.1.0 — Transistor-Level SPICE
*
* Target: {format_eng(s.freq_min, 'Hz')} - {format_eng(s.freq_max, 'Hz')}
* Ref:    {format_eng(s.ref_freq, 'Hz')}
* Process: {s.process} ({proc['nfet']}, {proc['pfet']})
*
* Auto-generated -- do not hand-edit.
*
"""


# ======================================================================
# VCO — Maneatis differential ring oscillator
# ======================================================================

def _write_vco(d: PLLDesign, out: str) -> str:
    path = os.path.join(out, "kestrel_vco.sp")
    proc = get_process_params(d.spec.process)
    nfet = proc["nfet"]
    pfet = proc["pfet"]
    pfx = proc.get("inst_prefix", "M")
    m = _mosfet

    with open(path, "w") as f:
        f.write(_header(d))

        tail_w = _um(d.vco_tail_w)
        tail_l = _um(d.vco_tail_l)
        diff_w = _um(d.vco_diff_w)
        diff_l = _um(d.vco_diff_l)
        load_w = _um(d.vco_load_w)
        load_l = _um(d.vco_load_l)
        bias_w = _um(d.vco_bias_w)
        bias_l = _um(d.vco_bias_l)
        buf_nw = _um(d.pfd_nw)
        buf_nl = _um(d.pfd_nl)
        buf_pw = _um(d.pfd_pw)
        buf_pl = _um(d.pfd_pl)

        # --- Maneatis delay cell subcircuit ---
        # Ports: outp outn inp inn vctrl vbn vdd vss
        #   inp/inn  = differential input
        #   outp/outn = differential output
        #   vctrl    = symmetric load control (sets output swing)
        #   vbn      = tail bias
        f.write(f"""\
**********************************************************************
* Maneatis delay cell — differential pair + symmetric load
**********************************************************************
.SUBCKT kestrel_delay_cell outp outn inp inn vctrl vbn vdd vss

* Tail current source
{m("tail", "tail", "vbn", "vss", "vss", nfet, tail_w, tail_l, pfx)}

* Differential pair
{m("n1", "outn", "inp", "tail", "vss", nfet, diff_w, diff_l, pfx)}
{m("n2", "outp", "inn", "tail", "vss", nfet, diff_w, diff_l, pfx)}

* Symmetric load (Maneatis)
* Each load has two PMOS in series: one diode-connected, one controlled
* The control voltage sets the ratio of triode/saturation operation,
* which determines the output swing independent of frequency.
{m("p1a", "outn", "outn", "vdd", "vdd", pfet, load_w, load_l, pfx)}
{m("p1b", "outn", "vctrl", "vdd", "vdd", pfet, load_w, load_l, pfx)}
{m("p2a", "outp", "outp", "vdd", "vdd", pfet, load_w, load_l, pfx)}
{m("p2b", "outp", "vctrl", "vdd", "vdd", pfet, load_w, load_l, pfx)}

.ENDS kestrel_delay_cell

""")

        # --- Bias generator subcircuit ---
        # Self-biased replica: sets vbn and vctrl via replica feedback
        f.write(f"""\
**********************************************************************
* Self-biased replica bias generator (Maneatis)
**********************************************************************
.SUBCKT kestrel_vco_bias vbn vctrl vdd vss

* Replica of one delay-cell half: a diode NMOS (mirrors the tail current)
* loaded by the same symmetric load as the ring.  Negative feedback:
*   vbn up -> more current -> replica output (= vbn) pulled down.
* The Vctrl PFET sits inside this loop, so vbn tracks Vctrl exactly as
* the delay-cell loads do (same topology as the validated sim/ decks).
{m("rep_n", "vbn", "vbn", "vss", "vss", nfet, bias_w, bias_l, pfx)}
{m("rep_pd", "vbn", "vbn", "vdd", "vdd", pfet, load_w, load_l, pfx)}
{m("rep_pc", "vbn", "vctrl", "vdd", "vdd", pfet, load_w, load_l, pfx)}

* Startup current — avoids the degenerate vbn = 0 solution
Istart vdd vbn {_eng(d.vco_i_start if d.vco_i_start > 0 else d.vco_i_stage / 100)}

.ENDS kestrel_vco_bias

""")

        # --- Differential-to-CMOS output buffer ---
        f.write(f"""\
**********************************************************************
* Differential-to-CMOS output buffer
* NMOS pair (tail biased from vbn, so it tracks the ring current) with
* a PMOS mirror load, then two inverters.  Converts the symmetric-load
* swing (Vctrl..VDD) to rail-to-rail CMOS at any control voltage and
* isolates the ring from the divider's input capacitance.  Without it
* the static divider sees no logic low until the loop has pulled Vctrl
* down, so the PLL cannot start.  Non-inverting: out follows inp.
**********************************************************************
.SUBCKT kestrel_vco_inv out in vdd vss
{m("p", "out", "in", "vdd", "vdd", pfet, buf_pw, buf_pl, pfx)}
{m("n", "out", "in", "vss", "vss", nfet, buf_nw, buf_nl, pfx)}
.ENDS kestrel_vco_inv

.SUBCKT kestrel_vco_buf out inp inn vbn vdd vss
{m("tail", "tail", "vbn", "vss", "vss", nfet, tail_w, tail_l, pfx)}
{m("n1", "d1", "inp", "tail", "vss", nfet, diff_w, diff_l, pfx)}
{m("n2", "d2", "inn", "tail", "vss", nfet, diff_w, diff_l, pfx)}
{m("p1", "d1", "d1", "vdd", "vdd", pfet, load_w, load_l, pfx)}
{m("p2", "d2", "d1", "vdd", "vdd", pfet, load_w, load_l, pfx)}
Xinv1 o1 d2 vdd vss kestrel_vco_inv
Xinv2 out o1 vdd vss kestrel_vco_inv
.ENDS kestrel_vco_buf

""")

        # --- Top-level VCO subcircuit ---
        n_stg = d.spec.vco_stages
        f.write(f"""\
**********************************************************************
* Ring oscillator VCO — {n_stg}-stage differential
**********************************************************************
.SUBCKT kestrel_vco outp outn vctrl_ext vdd vss
""")
        # For self-biased operation, the bias generator sets vbn and vctrl.
        # The external control voltage (vctrl_ext) overrides the internal
        # vctrl when the PLL loop is closed.
        f.write(f"""
* Bias generator
Xbias vbn vctrl_int vdd vss kestrel_vco_bias

* Control voltage: use external when PLL loop is closed
* (In self-biased mode, connect vctrl_ext to vctrl_int)
Rsw vctrl_ext vctrl 1
* Internal bias also drives vctrl through high resistance (startup)
Rbias vctrl_int vctrl 100k

""")

        # Generate the ring of delay cells
        # Each stage: inputs from previous stage outputs, outputs to next
        # Last stage feeds back to first with inversion (cross-coupled)
        for i in range(n_stg):
            prev = (i - 1) % n_stg
            # Node naming: dp_0/dn_0, dp_1/dn_1, ...
            if i == 0:
                # First stage gets feedback from last stage (inverted)
                inp = f"dn_{n_stg - 1}"
                inn = f"dp_{n_stg - 1}"
            else:
                inp = f"dp_{prev}"
                inn = f"dn_{prev}"
            op = f"dp_{i}"
            on = f"dn_{i}"
            f.write(f"Xstage{i} {op} {on} {inp} {inn} vctrl vbn vdd vss kestrel_delay_cell\n")

        # Buffered CMOS outputs from the last stage (both polarities)
        f.write(f"""
* Differential-to-CMOS output buffers
Xbufp outp dp_{n_stg - 1} dn_{n_stg - 1} vbn vdd vss kestrel_vco_buf
Xbufn outn dn_{n_stg - 1} dp_{n_stg - 1} vbn vdd vss kestrel_vco_buf

.ENDS kestrel_vco
""")
    return path


# ======================================================================
# PFD — NAND-based tri-state phase-frequency detector
# ======================================================================

def _write_pfd(d: PLLDesign, out: str) -> str:
    path = os.path.join(out, "kestrel_pfd.sp")
    proc = get_process_params(d.spec.process)
    nfet = proc["nfet"]
    pfet = proc["pfet"]
    pfx = proc.get("inst_prefix", "M")
    m = _mosfet
    nw = _um(d.pfd_nw)
    nl = _um(d.pfd_nl)
    pw = _um(d.pfd_pw)
    pl = _um(d.pfd_pl)

    with open(path, "w") as f:
        f.write(_header(d))

        # 2-input NAND gate subcircuit
        f.write(f"""\
**********************************************************************
* 2-input NAND gate
**********************************************************************
.SUBCKT nand2 out a b vdd vss
{m("p1", "out", "a", "vdd", "vdd", pfet, pw, pl, pfx)}
{m("p2", "out", "b", "vdd", "vdd", pfet, pw, pl, pfx)}
{m("n1", "out", "a", "mid", "vss", nfet, nw, nl, pfx)}
{m("n2", "mid", "b", "vss", "vss", nfet, nw, nl, pfx)}
.ENDS nand2

""")

        # Inverter subcircuit
        f.write(f"""\
**********************************************************************
* Inverter
**********************************************************************
.SUBCKT inv out in vdd vss
{m("p", "out", "in", "vdd", "vdd", pfet, pw, pl, pfx)}
{m("n", "out", "in", "vss", "vss", nfet, nw, nl, pfx)}
.ENDS inv

""")

        # 3-input NAND gate (used by the flip-flop)
        f.write(f"""\
**********************************************************************
* 3-input NAND gate
**********************************************************************
.SUBCKT nand3 out a b c vdd vss
{m("p1", "out", "a", "vdd", "vdd", pfet, pw, pl, pfx)}
{m("p2", "out", "b", "vdd", "vdd", pfet, pw, pl, pfx)}
{m("p3", "out", "c", "vdd", "vdd", pfet, pw, pl, pfx)}
{m("n1", "out", "a", "m1", "vss", nfet, nw, nl, pfx)}
{m("n2", "m1", "b", "m2", "vss", nfet, nw, nl, pfx)}
{m("n3", "m2", "c", "vss", "vss", nfet, nw, nl, pfx)}
.ENDS nand3

""")

        # Edge-triggered DFF: classic 6-NAND structure with async reset
        f.write("""\
**********************************************************************
* D flip-flop — positive edge, asynchronous active-low reset
*
* Classic six-NAND edge-triggered flip-flop: two input latches (G1-G4)
* generate set_b (node b) and reset_b (node c) for the output latch
* (G5/G6); both stay inactive while clk is low and exactly one fires on
* the rising edge.  rst_b goes into G2, G4 and G6 only: Q is forced low
* regardless of clk, and because the reset_b latch (G3) keeps its state
* the flop does not re-set when rst_b is released while clk is still
* high — the PFD depends on that.  (Reset into G3 as well makes the
* PFD chatter at the reset-loop rate.)
* Also used as the divider's toggle stage (D = Qbar).
**********************************************************************
.SUBCKT kestrel_dff q qb d clk rst_b vdd vss

* Input latches
Xg1 a  b   d4  vdd vss nand2
Xg2 b  a   clk rst_b vdd vss nand3
Xg3 c  b   clk d4 vdd vss nand3
Xg4 d4 c   d   rst_b vdd vss nand3

* Output latch
Xg5 q  b   qb  vdd vss nand2
Xg6 qb q   c   rst_b vdd vss nand3

.ENDS kestrel_dff

""")

        # PFD top level
        f.write("""\
**********************************************************************
* Phase-Frequency Detector (tri-state)
**********************************************************************
.SUBCKT kestrel_pfd up dn ref_clk fb_clk vdd vss

* Two flip-flops with D tied high: UP sets on the ref edge, DN on the
* feedback edge.
Xdff_ref up up_b vdd ref_clk rst_b vdd vss kestrel_dff
Xdff_fb  dn dn_b vdd fb_clk  rst_b vdd vss kestrel_dff

* Reset both as soon as UP and DN are both high.  The two inverters add
* delay so the coincident pulses keep a minimum width (no dead zone).
Xrst_nand rst_n0 up dn vdd vss nand2
Xrst_d1 rst_n1 rst_n0 vdd vss inv
Xrst_d2 rst_b rst_n1 vdd vss inv

.ENDS kestrel_pfd
""")
    return path


# ======================================================================
# Charge pump — symmetric cascode
# ======================================================================

def _write_charge_pump(d: PLLDesign, out: str) -> str:
    path = os.path.join(out, "kestrel_charge_pump.sp")
    proc = get_process_params(d.spec.process)
    nfet = proc["nfet"]
    pfet = proc["pfet"]
    pfx = proc.get("inst_prefix", "M")
    m = _mosfet

    cp_up_w = _um(d.cp_up_w)
    cp_up_l = _um(d.cp_up_l)
    cp_dn_w = _um(d.cp_dn_w)
    cp_dn_l = _um(d.cp_dn_l)
    sw_w = _um(d.cp_sw_w)
    sw_l = _um(d.cp_sw_l)
    sw_half_w = _um(d.cp_sw_w * 0.5)

    with open(path, "w") as f:
        f.write(_header(d))
        f.write(f"""\
**********************************************************************
* Charge pump — symmetric UP/DN current sources
**********************************************************************
.SUBCKT kestrel_charge_pump out up dn pbias nbias vdd vss

* UP current source (PMOS) — sources Icp when UP is active
* Current mirror: Mp_mir is diode-connected reference
{m("p_mir", "pbias", "pbias", "vdd", "vdd", pfet, cp_up_w, cp_up_l, pfx)}
{m("p_src", "up_drain", "pbias", "vdd", "vdd", pfet, cp_up_w, cp_up_l, pfx)}

* UP switch — PMOS pass gate (active-low UP means UP_b)
* UP signal is active-high from PFD, so invert for PMOS switch
{m("p_sw", "out", "up_b", "up_drain", "vdd", pfet, sw_w, sw_l, pfx)}
Rinv_up up_node up 1
* Simple inverter for UP_b
{m("p_inv", "up_b", "up_node", "vdd", "vdd", pfet, sw_w, sw_l, pfx)}
{m("n_inv", "up_b", "up_node", "vss", "vss", nfet, sw_half_w, sw_l, pfx)}

* DN current source (NMOS) — sinks Icp when DN is active
{m("n_mir", "nbias", "nbias", "vss", "vss", nfet, cp_dn_w, cp_dn_l, pfx)}
{m("n_src", "dn_drain", "nbias", "vss", "vss", nfet, cp_dn_w, cp_dn_l, pfx)}

* DN switch — NMOS pass gate (active-high DN)
{m("n_sw", "out", "dn", "dn_drain", "vss", nfet, sw_half_w, sw_l, pfx)}

* Mirror references: pull Icp OUT of the PMOS diode (pbias) and push it
* INTO the NMOS diode (nbias).  (Reversed polarity leaves both mirrors at
* zero current and the loop filter frozen.)
* Bias current reference
Ibias_p pbias vss {_eng(d.icp)}
Ibias_n vdd nbias {_eng(d.icp)}

.ENDS kestrel_charge_pump
""")
    return path


# ======================================================================
# Loop filter — passive R-C1-C2
# ======================================================================

def _write_loop_filter(d: PLLDesign, out: str) -> str:
    path = os.path.join(out, "kestrel_loop_filter.sp")
    with open(path, "w") as f:
        f.write(_header(d))
        f.write(f"""\
**********************************************************************
* Loop filter — second-order passive (series R+C1, shunt C2)
**********************************************************************
.SUBCKT kestrel_loop_filter in out vss

* C2: shunt capacitor (ripple suppression)
C2 in vss {_eng(d.c2)}

* R1: series resistor (creates stabilizing zero)
R1 in mid {_eng(d.r_filter)}

* C1: main integrating capacitor
C1 mid vss {_eng(d.c1)}

* Output is voltage across C1
Rout mid out 1

.ENDS kestrel_loop_filter
""")
    return path


# ======================================================================
# Divider — TSPC divide-by-2 chain
# ======================================================================

def _write_divider(d: PLLDesign, out: str) -> str:
    path = os.path.join(out, "kestrel_divider.sp")
    n_stages = d.div_stages

    with open(path, "w") as f:
        f.write(_header(d))
        f.write(f"""\
**********************************************************************
* Divide-by-2 stage — toggle flip-flop (D = Qbar) on the kestrel_dff
* from kestrel_pfd.sp (include that file first).  Static CMOS: no
* minimum clock rate; the upper limit is the six-NAND loop delay.
**********************************************************************
.SUBCKT kestrel_div2 out in vdd vss
Xff out out_b out_b in vdd vdd vss kestrel_dff
.ENDS kestrel_div2

**********************************************************************
* Feedback divider — {n_stages}-stage divide-by-2 chain
* Total division: 2^{n_stages} = {2**n_stages}
* (design N = {d.n_nom}; a programmable N needs a swallow counter)
**********************************************************************
.SUBCKT kestrel_divider out in vdd vss
""")
        for i in range(n_stages):
            inp = "in" if i == 0 else f"div{i-1}"
            f.write(f"Xdiv{i} div{i} {inp} vdd vss kestrel_div2\n")
        f.write(f"\nRout div{n_stages - 1} out 1\n")
        f.write("\n.ENDS kestrel_divider\n")
    return path


# ======================================================================
# Top-level PLL
# ======================================================================

def _write_pll_top(d: PLLDesign, out: str) -> str:
    path = os.path.join(out, "kestrel_pll_top.sp")
    if d.kvco_sign < 0:
        cp_inst = ("* UP/DN swapped: this VCO has negative Kvco (f falls as Vctrl rises)\n"
                   "Xcp cp_out dn up pbias nbias vdd vss kestrel_charge_pump")
    else:
        cp_inst = "Xcp cp_out up dn pbias nbias vdd vss kestrel_charge_pump"
    with open(path, "w") as f:
        f.write(_header(d))
        f.write(f"""\
**********************************************************************
* Top-level PLL — all blocks interconnected
**********************************************************************
.SUBCKT kestrel_pll_top clk_out clk_outb vctrl_mon ref_clk vdd vss

* Phase-frequency detector
Xpfd up dn ref_clk fb_clk vdd vss kestrel_pfd

* Charge pump
{cp_inst}

* Loop filter
Xlf cp_out vctrl vss kestrel_loop_filter

* VCO
Xvco clk_out clk_outb vctrl vdd vss kestrel_vco

* Frequency divider (feedback path)
Xdiv fb_clk clk_out vdd vss kestrel_divider

* Monitor port
Rmon vctrl vctrl_mon 1k

.ENDS kestrel_pll_top
""")
    return path


# ======================================================================
# VCO characterization testbench (open loop)
# ======================================================================

def _write_vco_testbench(d: PLLDesign, out: str) -> str:
    """Open-loop VCO deck: sweep Vctrl, measure the oscillation frequency.

    Mirrors sim/kes_vco_xyce*.cir so a generated VCO can be checked on its
    own before closing the loop.  Xyce syntax (Xvco:node, .STEP, .MEASURE).
    """
    path = os.path.join(out, "kestrel_vco_tb.sp")
    proc = get_process_params(d.spec.process)
    vdd = d.spec.supply_voltage
    cal = proc.get("vco_calibration", {})
    v_lo = cal.get("vctrl_min", 0.1 * vdd)
    v_hi = cal.get("vctrl_max", 0.8 * vdd)
    v_step = round((v_hi - v_lo) / 6, 3)
    # ~150 cycles at f_center, measured after the first third
    tstop = max(100e-9, 150.0 / d.f_center)
    td = tstop / 3
    sign = '-' if d.kvco_sign < 0 else '+'
    with open(path, "w") as f:
        f.write(f"""\
* Kestrel VCO characterization testbench — {d.spec.process}
*
* Open-loop sweep of the generated kestrel_vco: Vctrl is stepped and the
* oscillation frequency measured from the differential output
* (f = 1/(T2-T1) in the .mt0 file; the full curve is in the .prn — count
* V(diff) rising zero crossings per step).  Run from this directory:
*     Xyce kestrel_vco_tb.sp
* Design intent: f_center {format_eng(d.f_center, 'Hz')}, Kvco {sign}{format_eng(d.kvco, 'Hz/V')}
*

{_model_section(proc)}
.INCLUDE "kestrel_vco.sp"

Vdd vdd 0 {vdd}
Vss vss 0 0

* Control voltage, stepped below
Vctrl vctrl 0 {{vctrl_val}}
.PARAM vctrl_val = {d.vctrl_nom:.3g}

Xvco outp outn vctrl vdd vss kestrel_vco

* Light output load + differential probe
Cload_p outp 0 10f
Cload_n outn 0 10f
Ediff diff 0 outp outn 1.0

* Startup kick — breaks the symmetric (non-oscillating) solution
Ikick 0 Xvco:dp_0 PULSE(0 1m 0 50p 50p 10n 0)

.TRAN 50p {_eng(tstop)}
.PRINT TRAN V(outp) V(outn) V(vctrl) V(Xvco:vbn) V(diff)
.MEASURE TRAN t1 WHEN V(diff)=0 RISE=3 TD={_eng(td)}
.MEASURE TRAN t2 WHEN V(diff)=0 RISE=4 TD={_eng(td)}

.STEP vctrl_val {v_lo:.3g} {v_hi:.3g} {v_step:.3g}

.END
""")
    return path


# ======================================================================
# Testbench
# ======================================================================

def _write_testbench(d: PLLDesign, out: str) -> str:
    path = os.path.join(out, "kestrel_tb.sp")
    proc = get_process_params(d.spec.process)
    vdd = d.spec.supply_voltage
    ref_period = 1.0 / d.spec.ref_freq
    # Simulate for enough cycles to see lock
    sim_time = max(d.lock_time * 3, 50 * ref_period) if d.lock_time > 0 else 100 * ref_period
    tstep = ref_period / 20

    with open(path, "w") as f:
        f.write(f"""\
* Kestrel PLL Testbench
*
* Process: {d.spec.process}
* Expected lock time: ~{format_eng(d.lock_time, 's')}
*

{_model_section(proc)}
**********************************************************************
* Include all subcircuits
**********************************************************************
.INCLUDE "kestrel_vco.sp"
.INCLUDE "kestrel_pfd.sp"
.INCLUDE "kestrel_charge_pump.sp"
.INCLUDE "kestrel_loop_filter.sp"
.INCLUDE "kestrel_divider.sp"
.INCLUDE "kestrel_pll_top.sp"

**********************************************************************
* Supply
**********************************************************************
Vdd vdd 0 {vdd}
Vss vss 0 0

**********************************************************************
* Reference clock — {format_eng(d.spec.ref_freq, 'Hz')}
**********************************************************************
Vref ref_clk 0 PULSE(0 {vdd} 0 100p 100p {_eng(ref_period/2)} {_eng(ref_period)})

**********************************************************************
* DUT
**********************************************************************
Xpll clk_out clk_outb vctrl_mon ref_clk vdd vss kestrel_pll_top

* Startup kick — breaks the ring's symmetric (non-oscillating) DC point
Ikick 0 Xpll:Xvco:dp_0 PULSE(0 1m 0 50p 50p 10n 0)

**********************************************************************
* Simulation
**********************************************************************
.TRAN {_eng(tstep)} {_eng(sim_time)}

**********************************************************************
* Output
**********************************************************************
.PRINT TRAN V(clk_out) V(vctrl_mon) V(ref_clk) V(Xpll:fb_clk) V(Xpll:up) V(Xpll:dn)

.END
""")
    return path
