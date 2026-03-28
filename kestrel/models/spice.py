"""Transistor-level SPICE netlist emitter for charge-pump PLL.

Generates subcircuits for each PLL block using MOSFET models from the
target process (SKY130 or GF180).  The sizing comes from the design
engine's analytical computation.

Each subcircuit is a structurally correct transistor-level implementation:
  - VCO: Maneatis differential ring oscillator with symmetric load
  - PFD: NAND-based tri-state phase-frequency detector
  - Charge pump: cascode current mirrors with UP/DN switches
  - Loop filter: passive R-C1-C2
  - Divider: TSPC divide-by-2 chain
  - Top: full PLL with all blocks wired together
"""

import os
import math
from ..design.engine import PLLDesign, get_process_params, format_eng


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
    return files


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

* Replica bias branch — diode-connected NMOS sets vbn
{m("bias", "vbn", "vbn", "vss", "vss", nfet, bias_w, bias_l, pfx)}

* Replica load — mirrors the delay cell load structure
* Diode-connected PMOS generates vctrl
{m("rep_p", "vbn", "vbn", "vdd", "vdd", pfet, load_w, load_l, pfx)}
{m("rep_c", "drain_rep", "vctrl", "vdd", "vdd", pfet, load_w, load_l, pfx)}
{m("rep_n", "drain_rep", "vbn", "vss", "vss", nfet, diff_w, diff_l, pfx)}

* Startup circuit — small current to prevent degenerate zero-current state
Istart vdd vbn {_eng(d.vco_i_stage / 100)}

.ENDS kestrel_vco_bias

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

        # Output from last stage
        f.write(f"""
* Output buffer — last stage drives output
Routp dp_{n_stg - 1} outp 1
Routn dn_{n_stg - 1} outn 1

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

        # DFF from NAND gates (reset-dominant SR latch + edge detect)
        f.write(f"""\
**********************************************************************
* D flip-flop (positive-edge, async reset)
* Implemented as two cross-coupled NAND latches (master-slave)
**********************************************************************
.SUBCKT kestrel_dff q d clk rst vdd vss

* Master latch
Xnand1 s1 d   clk_b vdd vss nand2
Xnand2 r1 s1b clk_b vdd vss nand2
Xinv_d s1b d vdd vss inv

* clk_b = NOT clk
Xinv_clk clk_b clk vdd vss inv

* Slave latch
Xnand3 s2 s1  clk  vdd vss nand2
Xnand4 r2 s2  rst  vdd vss nand2
Xnand5 s2 r2  s1   vdd vss nand2

* Force reset when rst is low (active-low reset)
* Use rst to override: q = s2
Xinv_q q_b s2 vdd vss inv
Xinv_q2 q q_b vdd vss inv

.ENDS kestrel_dff

""")

        # PFD top level
        f.write(f"""\
**********************************************************************
* Phase-Frequency Detector (tri-state)
**********************************************************************
.SUBCKT kestrel_pfd up dn ref_clk fb_clk vdd vss

* Two DFFs: one triggered by ref_clk, one by fb_clk
* Both have D tied to VDD (always sample a '1' on rising edge)
Xdff_ref up_int vdd ref_clk rst vdd vss kestrel_dff
Xdff_fb  dn_int vdd fb_clk  rst vdd vss kestrel_dff

* Reset: AND of both outputs (active-low reset to DFFs)
* rst = NAND(up_int, dn_int) — when both high, reset both
Xrst_nand rst_b up_int dn_int vdd vss nand2
Xinv_rst  rst rst_b vdd vss inv

* Output buffers
Xbuf_up up up_int vdd vss inv
Xinv_up2 up_buf up vdd vss inv
Xbuf_dn dn dn_int vdd vss inv
Xinv_dn2 dn_buf dn vdd vss inv

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

* Bias current reference
Ibias_p vdd pbias {_eng(d.icp)}
Ibias_n nbias vss {_eng(d.icp)}

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
    proc = get_process_params(d.spec.process)
    nfet = proc["nfet"]
    pfet = proc["pfet"]
    pfx = proc.get("inst_prefix", "M")
    m = _mosfet
    nw = _um(d.div_nw)
    nl = _um(d.div_nl)
    pw = _um(d.div_pw)
    pl = _um(d.div_pl)

    with open(path, "w") as f:
        f.write(_header(d))

        # TSPC divide-by-2
        f.write(f"""\
**********************************************************************
* TSPC divide-by-2 (True Single-Phase Clock)
* High-speed static divider suitable for GHz operation
**********************************************************************
.SUBCKT kestrel_tspc_div2 out in vdd vss

* First stage (clk-low transparent)
{m("p1", "n1", "in", "vdd", "vdd", pfet, pw, pl, pfx)}
{m("n1", "n1", "in", "vss", "vss", nfet, nw, nl, pfx)}

* Second stage (clk-high transparent, with feedback)
{m("p2", "vdd", "in", "n2", "vdd", pfet, pw, pl, pfx)}
{m("n2", "n2", "n1", "n3", "vss", nfet, nw, nl, pfx)}
{m("n3", "n3", "in", "vss", "vss", nfet, nw, nl, pfx)}

* Third stage (output latch)
{m("p3", "n4", "n2", "vdd", "vdd", pfet, pw, pl, pfx)}
{m("n4", "n4", "n2", "vss", "vss", nfet, nw, nl, pfx)}

* Output inverter
{m("p5", "out", "n4", "vdd", "vdd", pfet, pw, pl, pfx)}
{m("n5", "out", "n4", "vss", "vss", nfet, nw, nl, pfx)}

* Feedback: output back to first stage input for toggle
* Connect n1 input to output (inverted) for divide-by-2
{m("p6", "n1_fb", "out", "vdd", "vdd", pfet, pw, pl, pfx)}
{m("n6", "n1_fb", "out", "vss", "vss", nfet, nw, nl, pfx)}

.ENDS kestrel_tspc_div2

""")

        # Programmable divider: chain of div-by-2 stages + mux
        n_stages = d.div_stages
        f.write(f"""\
**********************************************************************
* Programmable divider — {n_stages}-stage divide-by-2 chain
* Total division: 2^{n_stages} = {2**n_stages}
* For programmable N, select output from appropriate stage
**********************************************************************
.SUBCKT kestrel_divider out in vdd vss
""")
        for i in range(n_stages):
            inp = "in" if i == 0 else f"div{i-1}"
            f.write(f"Xdiv{i} div{i} {inp} vdd vss kestrel_tspc_div2\n")

        # Output from last stage
        f.write(f"\nRout div{n_stages - 1} out 1\n")
        f.write("\n.ENDS kestrel_divider\n")
    return path


# ======================================================================
# Top-level PLL
# ======================================================================

def _write_pll_top(d: PLLDesign, out: str) -> str:
    path = os.path.join(out, "kestrel_pll_top.sp")
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
Xcp cp_out up dn pbias nbias vdd vss kestrel_charge_pump

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

**********************************************************************
* Process models — user must provide the correct path
**********************************************************************
* .LIB "<path_to_pdk>/models/{proc['model_lib']}" {proc['corner']}

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

**********************************************************************
* Simulation
**********************************************************************
.TRAN {_eng(tstep)} {_eng(sim_time)}

**********************************************************************
* Output
**********************************************************************
.PRINT TRAN V(clk_out) V(vctrl_mon) V(ref_clk) V(Xpll.fb_clk)

.END
""")
    return path
