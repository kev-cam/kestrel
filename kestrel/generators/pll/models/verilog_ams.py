"""Verilog-AMS behavioral model emitter for charge-pump PLL."""

import math
import os
from kestrel.process import format_eng
from ..engine import PLLDesign


def emit_verilog_ams(design: PLLDesign, output_dir: str) -> list:
    """Generate Verilog-AMS files for a complete PLL.

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

    return files


def _header(module_name: str, design: PLLDesign) -> str:
    s = design.spec
    return f"""\
// {module_name}.vams — Kestrel PLL Generator v0.1.0
//
// Target: {format_eng(s.freq_min, 'Hz')} — {format_eng(s.freq_max, 'Hz')}
// Ref:    {format_eng(s.ref_freq, 'Hz')}
// Process: {s.process}
//
// Auto-generated — do not hand-edit.

`include "disciplines.vams"
`include "constants.vams"
"""


def _write_vco(d: PLLDesign, out: str) -> str:
    """Ring oscillator VCO with configurable Kvco."""
    path = os.path.join(out, "kestrel_vco.vams")
    s = d.spec

    # VCO output is a voltage toggling between 0 and Vdd at the VCO frequency.
    # Frequency = f_center + Kvco * (Vctrl - Vctrl_nom)
    with open(path, "w") as f:
        f.write(_header("kestrel_vco", d))
        f.write(f"""\

module kestrel_vco(vctrl, clk_out, clk_outb);
    input vctrl;
    output clk_out, clk_outb;
    electrical vctrl, clk_out, clk_outb;

    parameter real f_center = {d.f_center:.6e};  // Hz
    parameter real kvco     = {d.kvco:.6e};      // Hz/V
    parameter real vctrl_nom = {d.vctrl_nom:.4f}; // V — control voltage at f_center
    parameter real vdd      = {s.supply_voltage:.2f};
    parameter real n_stages = {s.vco_stages};     // ring oscillator stages
    parameter real tr       = 1.0 / (20.0 * f_center); // rise/fall time

    real freq, phase, vout;

    analog begin
        // Compute instantaneous frequency from control voltage
        freq = f_center + kvco * (V(vctrl) - vctrl_nom);
        if (freq < 0) freq = 0;

        // Phase accumulator (integral of frequency)
        phase = idtmod(freq, 0.0, 1.0, -0.5);

        // Square wave output: 50% duty cycle
        if (phase >= 0)
            vout = vdd;
        else
            vout = 0;

        V(clk_out)  <+ transition(vout, 0, tr, tr);
        V(clk_outb) <+ transition(vdd - vout, 0, tr, tr);

        // Bound the time step for accurate phase tracking
        $bound_step(0.2 / freq);
    end
endmodule
""")
    return path


def _write_pfd(d: PLLDesign, out: str) -> str:
    """Tri-state phase-frequency detector with dead-zone elimination."""
    path = os.path.join(out, "kestrel_pfd.vams")
    s = d.spec

    # The PFD outputs UP and DN pulses proportional to the phase error
    # between the reference clock and the feedback clock.
    with open(path, "w") as f:
        f.write(_header("kestrel_pfd", d))
        f.write(f"""\

module kestrel_pfd(ref_clk, fb_clk, up, dn);
    input ref_clk, fb_clk;
    output up, dn;
    electrical ref_clk, fb_clk, up, dn;

    parameter real vdd    = {s.supply_voltage:.2f};
    parameter real vth    = {s.supply_voltage/2:.2f}; // switching threshold
    parameter real td_rst = 200e-12;  // reset delay (dead-zone elimination)
    parameter real tr     = 50e-12;   // output transition time

    integer up_state, dn_state;
    integer ref_prev, fb_prev;
    real    ref_v, fb_v;

    analog begin
        @(initial_step) begin
            up_state = 0;
            dn_state = 0;
            ref_prev = 0;
            fb_prev  = 0;
        end

        ref_v = V(ref_clk);
        fb_v  = V(fb_clk);

        // Detect rising edges
        @(cross(ref_v - vth, +1)) begin
            up_state = 1;
            // If both UP and DN are high, reset after td_rst
        end

        @(cross(fb_v - vth, +1)) begin
            dn_state = 1;
        end

        // Reset: both high -> both low (with dead-zone delay)
        if (up_state == 1 && dn_state == 1) begin
            up_state = 0;
            dn_state = 0;
        end

        V(up) <+ transition(up_state ? vdd : 0, td_rst, tr, tr);
        V(dn) <+ transition(dn_state ? vdd : 0, td_rst, tr, tr);
    end
endmodule
""")
    return path


def _write_charge_pump(d: PLLDesign, out: str) -> str:
    """Symmetric charge pump: sources/sinks Icp based on UP/DN."""
    path = os.path.join(out, "kestrel_charge_pump.vams")
    s = d.spec

    with open(path, "w") as f:
        f.write(_header("kestrel_charge_pump", d))
        f.write(f"""\

module kestrel_charge_pump(up, dn, out);
    input up, dn;
    output out;
    electrical up, dn, out;

    parameter real icp = {d.icp:.6e};  // A — charge pump current
    parameter real vdd = {s.supply_voltage:.2f};
    parameter real vth = {s.supply_voltage/2:.2f};

    real i_out;

    analog begin
        // Source current when UP is high, sink when DN is high
        if (V(up) > vth && V(dn) <= vth)
            i_out = icp;       // pump up: increase control voltage
        else if (V(dn) > vth && V(up) <= vth)
            i_out = -icp;      // pump down: decrease control voltage
        else
            i_out = 0;         // both high or both low: no current

        I(out) <+ -i_out;  // conventional current into node

        // Clamp output to supply rails
        if (V(out) > vdd)
            I(out) <+ (V(out) - vdd) * 1e-3;
        if (V(out) < 0)
            I(out) <+ V(out) * 1e-3;
    end
endmodule
""")
    return path


def _write_loop_filter(d: PLLDesign, out: str) -> str:
    """Second-order passive loop filter: series R-C1, shunt C2."""
    path = os.path.join(out, "kestrel_loop_filter.vams")

    with open(path, "w") as f:
        f.write(_header("kestrel_loop_filter", d))
        f.write(f"""\

module kestrel_loop_filter(inp, vctrl);
    input inp;
    output vctrl;
    electrical inp, vctrl, mid;

    parameter real r1 = {d.r_filter:.6e};  // ohms — series resistor
    parameter real c1 = {d.c1:.6e};        // F — main integrating cap
    parameter real c2 = {d.c2:.6e};        // F — ripple bypass cap

    analog begin
        // C2 from input to ground (ripple suppression)
        I(inp) <+ c2 * ddt(V(inp));

        // R1 from input to mid node
        V(inp, mid) <+ r1 * I(inp, mid);

        // C1 from mid to ground (main integrating cap)
        I(mid) <+ c1 * ddt(V(mid));

        // Output follows mid node (vctrl = voltage across C1)
        V(vctrl) <+ V(mid);
    end
endmodule
""")
    return path


def _write_divider(d: PLLDesign, out: str) -> str:
    """Programmable integer-N frequency divider."""
    path = os.path.join(out, "kestrel_divider.vams")
    s = d.spec

    with open(path, "w") as f:
        f.write(_header("kestrel_divider", d))
        f.write(f"""\

module kestrel_divider(clk_in, clk_out);
    input clk_in;
    output clk_out;
    electrical clk_in, clk_out;

    parameter integer n_div = {d.n_nom};  // division ratio
    parameter real    vdd   = {s.supply_voltage:.2f};
    parameter real    vth   = {s.supply_voltage/2:.2f};
    parameter real    tr    = 50e-12;  // output transition time

    integer count;
    real    div_out;

    analog begin
        @(initial_step) begin
            count   = 0;
            div_out = 0;
        end

        @(cross(V(clk_in) - vth, +1)) begin
            count = count + 1;
            if (count >= n_div) begin
                count = 0;
                if (div_out > vth)
                    div_out = 0;
                else
                    div_out = vdd;
            end
        end

        V(clk_out) <+ transition(div_out, 0, tr, tr);
    end
endmodule
""")
    return path


def _write_pll_top(d: PLLDesign, out: str) -> str:
    """Top-level PLL interconnecting all sub-blocks."""
    path = os.path.join(out, "kestrel_pll_top.vams")
    s = d.spec

    with open(path, "w") as f:
        f.write(_header("kestrel_pll_top", d))
        f.write(f"""\

module kestrel_pll_top(ref_clk, clk_out, clk_outb, vctrl_mon);
    input  ref_clk;
    output clk_out, clk_outb, vctrl_mon;
    electrical ref_clk, clk_out, clk_outb, vctrl_mon;
    electrical up, dn, cp_out, vctrl, fb_clk;

    // Sub-block instances
    kestrel_pfd pfd_i (
        .ref_clk(ref_clk),
        .fb_clk(fb_clk),
        .up(up),
        .dn(dn)
    );

    kestrel_charge_pump cp_i (
        .up(up),
        .dn(dn),
        .out(cp_out)
    );

    kestrel_loop_filter lf_i (
        .inp(cp_out),
        .vctrl(vctrl)
    );

    kestrel_vco vco_i (
        .vctrl(vctrl),
        .clk_out(clk_out),
        .clk_outb(clk_outb)
    );

    kestrel_divider div_i (
        .clk_in(clk_out),
        .clk_out(fb_clk)
    );

    // Monitor port for control voltage observation
    analog begin
        V(vctrl_mon) <+ V(vctrl);
    end
endmodule
""")
    return path
