"""Verilog-AMS behavioral model emitter for the charge-pump PLL.

Style: the logic (PFD, divider, VCO) is event-driven Verilog; the VCO
schedules the delay to its next edge from the control voltage; only the
charge pump and the loop filter are analog blocks.  Every logic block
carries stat-sim timing-variability parameters (per-stage mismatch,
per-edge jitter, a seed) that default to zero, so the same source is the
deterministic behavioral model and the statistical one.
"""

import os
from kestrel.process import format_eng, get_process_params
from ..engine import PLLDesign

# `timescale 1ps / 1fs in every file; delays in the models are computed in
# seconds and converted with this constant.
_TU = "1e-12"

# N(0,1) from the seeded $random stream (Box-Muller), the way the stat-sim
# models draw their random times: plain Verilog, bit-exact between
# simulators that share IEEE 1364's $random, no $rdist_* needed.
_GAUSS = """\
    // N(0,1) from the seeded $random stream (Box-Muller); st advances per call
    function real gauss;
        input integer unused;
        real u1, u2;
        begin
            u1 = ($random(st) & 32'h7fffffff) / 2147483648.0 + 1e-12;
            u2 = ($random(st) & 32'h7fffffff) / 2147483648.0;
            gauss = $sqrt(-2.0 * $ln(u1)) * $cos(6.283185307179586 * u2);
        end
    endfunction
"""


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
    files.append(_write_testbench(design, output_dir))

    return files


def _header(module_name: str, design: PLLDesign, note: str = "") -> str:
    s = design.spec
    return f"""\
// {module_name}.vams — Kestrel PLL Generator v0.1.0
//
// Target: {format_eng(s.freq_min, 'Hz')} — {format_eng(s.freq_max, 'Hz')}
// Ref:    {format_eng(s.ref_freq, 'Hz')}
// Process: {s.process}
//
// Auto-generated — do not hand-edit.
{note}
`include "disciplines.vams"
`include "constants.vams"
`timescale 1ps / 1fs
"""


def reference_release_delay(d: PLLDesign) -> float:
    """Seconds after which the testbenches release the reference clock.

    Same guard as the SPICE testbench (spice.py): a reference edge that
    arrives before the divider's first feedback edge sets UP with nothing to
    reset it, and one full-width pump pulse steps the control node by
    Icp*R1, which on a negative-Kvco VCO can carry it past the divider's
    range.  Wait two divider periods at the VCO's start-up speed, and at
    least two reference periods.
    """
    proc = get_process_params(d.spec.process)
    cal = proc.get("vco_calibration") or {}
    ref_period = 1.0 / d.spec.ref_freq
    f_start = (cal.get("f_max") if d.kvco_sign < 0 else cal.get("f_min")) or d.f_center
    t_fb1 = 20e-9 + d.n_nom / f_start if f_start > 0 else 2 * ref_period
    return max(2 * ref_period, 2 * t_fb1)


def simulation_time(d: PLLDesign) -> float:
    """Seconds of transient the testbenches run (acquisition + settling)."""
    ref_period = 1.0 / d.spec.ref_freq
    return reference_release_delay(d) + (
        max(d.acq_time * 1.5, d.lock_time * 3, 50 * ref_period)
        if d.lock_time > 0 else 100 * ref_period)


def _write_vco(d: PLLDesign, out: str) -> str:
    """Ring-oscillator VCO: an edge travelling round n_stages delay cells.

    Event-driven: one process is the travelling edge.  Each time it enters
    a stage it reads the control voltage and waits that stage's delay; the
    output toggles when it has gone round.  No frequency is integrated and
    the simulator only lands on the edges.
    """
    path = os.path.join(out, "kestrel_vco.vams")
    s = d.spec
    kvco_signed = d.kvco * d.kvco_sign
    td_nom = 1.0 / (2.0 * s.vco_stages * d.f_center)

    with open(path, "w") as f:
        f.write(_header("kestrel_vco", d, """\
//
// Delay-to-next-edge model of the ring.  The engine's (f_center, Kvco)
// pair appears as the delay of one stage at vctrl_nom (td_nom =
// 1/(2*n_stages*f_center)) and the fractional speed change per volt
// (s_td = Kvco/f_center): td(V) = td_nom / (1 + s_td*(V - vctrl_nom)).
// The travelling edge samples the control voltage at every stage, as the
// real ring does; the output period is 2*n_stages stage delays.
//
// stat-sim timing variability (all default to 0 = deterministic):
//   sigma_mm  per-stage static mismatch, fraction of td_nom, drawn once
//             per stage from the seed (Pelgrom-style, per instance)
//   sigma_j   per-edge jitter, fraction of the stage delay, drawn at every
//             stage edge (white; thermal noise of the delay cell)
"""))
        f.write(f"""\

module kestrel_vco(vctrl, clk_out, clk_outb);
    input  vctrl;
    output clk_out, clk_outb;
    electrical vctrl;
    reg    clk_out;
    wire   clk_outb;

    parameter real    f_center  = {d.f_center:.6e};   // Hz — at vctrl_nom
    parameter real    kvco      = {kvco_signed:.6e};   // Hz/V, signed ({'falls' if d.kvco_sign < 0 else 'rises'} with Vctrl)
    parameter real    vctrl_nom = {d.vctrl_nom:.4f};        // V
    parameter integer n_stages  = {s.vco_stages};
    parameter real    f_floor   = {d.f_center / 20:.6e};   // Hz — slowest the ring may run (keeps td finite)
    parameter real    td_nom    = {td_nom:.6e};   // s — stage delay at vctrl_nom = 1/(2*n_stages*f_center)
    parameter real    s_td      = {kvco_signed / d.f_center:.6e};   // 1/V — Kvco/f_center
    parameter real    sigma_mm  = 0.0;            // stat-sim: per-stage mismatch (fraction of td_nom)
    parameter real    sigma_j   = 0.0;            // stat-sim: per-edge jitter (fraction of td)
    parameter integer seed      = 1;
    localparam real   TU        = {_TU};         // s per `timescale unit
    localparam real   x_min     = f_floor / f_center;

    integer stage, st, i;
    real    x, td;
    real    mm [0:n_stages-1];   // per-stage mismatch, fixed for the run

{_GAUSS}
    initial begin
        clk_out = 1'b0;
        stage   = 0;
        st      = seed;
        for (i = 0; i < n_stages; i = i + 1)
            mm[i] = (sigma_mm > 0.0) ? sigma_mm * gauss(0) : 0.0;
    end

    assign clk_outb = ~clk_out;

    // The travelling edge: wait the delay of the stage being entered, computed
    // from the control voltage at this instant, then move on; every n_stages
    // stages the output toggles.
    always begin
        x = 1.0 + s_td * (V(vctrl) - vctrl_nom);
        if (x < x_min) x = x_min;
        td = td_nom / x * (1.0 + mm[stage]);
        if (sigma_j > 0.0) td = td * (1.0 + sigma_j * gauss(0));
        if (td < TU) td = TU;
        #(td / TU);
        stage = stage + 1;
        if (stage >= n_stages) begin
            stage   = 0;
            clk_out = ~clk_out;
        end
    end
endmodule
""")
    return path


def _write_pfd(d: PLLDesign, out: str) -> str:
    """Tri-state phase-frequency detector: two flops set by the clock edges,
    both cleared through an AND of their outputs (event-driven logic)."""
    path = os.path.join(out, "kestrel_pfd.vams")

    with open(path, "w") as f:
        f.write(_header("kestrel_pfd", d, """\
//
// Event-driven logic: UP sets on the reference edge, DN on the feedback
// edge, and both clear td_rst after both are set.  The td_rst overlap is
// the pulse the pump sees in lock (no dead zone).  sigma_j jitters the
// flop clock-to-Q per edge (stat-sim).
"""))
        f.write(f"""\

module kestrel_pfd(ref_clk, fb_clk, up, dn);
    input  ref_clk, fb_clk;
    output up, dn;
    reg    up, dn;
    wire   rst;

    parameter real    td_rst  = 200e-12;   // s — reset path (AND + flop clear): UP/DN overlap in lock
    parameter real    td_cq   = 50e-12;    // s — flop clock-to-Q
    parameter real    sigma_j = 0.0;       // stat-sim: per-edge jitter on td_cq (fraction)
    parameter integer seed    = 1;
    localparam real   TU      = {_TU};

    integer st;
    real    tq_up, tq_dn;

{_GAUSS}
    initial begin
        up = 1'b0;
        dn = 1'b0;
        st = seed;
    end

    assign #(td_rst / TU) rst = up & dn;

    always @(posedge ref_clk or posedge rst)
        if (rst)
            up <= 1'b0;
        else begin
            tq_up = td_cq;
            if (sigma_j > 0.0) tq_up = tq_up * (1.0 + sigma_j * gauss(0));
            if (tq_up < TU) tq_up = TU;
            up <= #(tq_up / TU) 1'b1;
        end

    always @(posedge fb_clk or posedge rst)
        if (rst)
            dn <= 1'b0;
        else begin
            tq_dn = td_cq;
            if (sigma_j > 0.0) tq_dn = tq_dn * (1.0 + sigma_j * gauss(0));
            if (tq_dn < TU) tq_dn = TU;
            dn <= #(tq_dn / TU) 1'b1;
        end
endmodule
""")
    return path


def _write_charge_pump(d: PLLDesign, out: str) -> str:
    """Symmetric charge pump: sources Icp while UP, sinks while DN (analog)."""
    path = os.path.join(out, "kestrel_charge_pump.vams")
    s = d.spec

    with open(path, "w") as f:
        f.write(_header("kestrel_charge_pump", d, """\
//
// Analog block with logic inputs: UP and DN are read in the analog context
// (their events are analog time points) and shaped with transition() so
// the pump has finite switching edges.
"""))
        f.write(f"""\

module kestrel_charge_pump(up, dn, out);
    input  up, dn;
    output out;
    wire   up, dn;
    electrical out;

    parameter real icp = {d.icp:.6e};  // A — charge pump current
    parameter real vdd = {s.supply_voltage:.2f};
    parameter real tr  = 50e-12;      // s — current switching edge

    real i_up, i_dn;

    analog begin
        i_up = transition((up == 1'b1) ? icp : 0.0, 0.0, tr, tr);
        i_dn = transition((dn == 1'b1) ? icp : 0.0, 0.0, tr, tr);

        // Source Icp into the filter node while UP, sink while DN; both on
        // (the reset overlap) leaves no net current.
        I(out) <+ -(i_up - i_dn);

        // Soft rail clamps: the current sources drop out at the rails
        if (V(out) > vdd)
            I(out) <+ (V(out) - vdd) * 1e-3;
        if (V(out) < 0.0)
            I(out) <+ V(out) * 1e-3;
    end
endmodule
""")
    return path


def _write_loop_filter(d: PLLDesign, out: str) -> str:
    """Second-order passive loop filter: series R-C1, shunt C2 (analog)."""
    path = os.path.join(out, "kestrel_loop_filter.vams")

    with open(path, "w") as f:
        f.write(_header("kestrel_loop_filter", d, """\
//
// v_init is the start point of the filter (the SPICE testbench's .IC):
// both capacitor nodes are held there through a 1 S switch for the first
// t_pre of the run, before the reference is released.
"""))
        f.write(f"""\

module kestrel_loop_filter(inp, vctrl);
    input  inp;
    output vctrl;
    electrical inp, vctrl, mid;

    parameter real r1     = {d.r_filter:.6e};  // ohms — series resistor
    parameter real c1     = {d.c1:.6e};        // F — main integrating cap
    parameter real c2     = {d.c2:.6e};        // F — ripple bypass cap
    parameter real v_init = {d.vctrl_start:.4f};          // V — start point (0 = discharged)
    parameter real t_pre  = 1e-9;             // s — precharge window

    real g_pre;
    integer pre_on;

    analog begin
        @(initial_step) pre_on = 1;
        @(timer(t_pre)) pre_on = 0;
        g_pre = transition(pre_on ? 1.0 : 0.0, 0.0, 100e-12, 100e-12);

        // C2 from input to ground (ripple suppression)
        I(inp) <+ c2 * ddt(V(inp));

        // R1 from input to mid node
        V(inp, mid) <+ r1 * I(inp, mid);

        // C1 from mid to ground (main integrating cap)
        I(mid) <+ c1 * ddt(V(mid));

        // Precharge both nodes to v_init
        I(inp) <+ g_pre * (V(inp) - v_init);
        I(mid) <+ g_pre * (V(mid) - v_init);

        // Output is the charge-pump node, so the VCO sees the R1-C1 zero.
        // (Following the mid node — the voltage across C1 — would hide the
        // zero and leave the loop with no phase margin.)
        V(vctrl) <+ V(inp);
    end
endmodule
""")
    return path


def _write_divider(d: PLLDesign, out: str) -> str:
    """Integer-N frequency divider: a counter on the VCO's rising edges
    (event-driven logic)."""
    path = os.path.join(out, "kestrel_divider.vams")

    with open(path, "w") as f:
        f.write(_header("kestrel_divider", d, """\
//
// Event-driven logic: counts rising edges of clk_in; the output rises every
// n_div edges and falls after n_div/2 of them (the PFD uses the rising
// edge only).  sigma_j jitters the clock-to-Q per edge (stat-sim).
"""))
        f.write(f"""\

module kestrel_divider(clk_in, clk_out);
    input  clk_in;
    output clk_out;
    reg    clk_out;

    parameter integer n_div   = {d.n_nom};  // division ratio
    parameter real    td_cq   = 50e-12;     // s — clock-to-Q
    parameter real    sigma_j = 0.0;        // stat-sim: per-edge jitter on td_cq (fraction)
    parameter integer seed    = 1;
    localparam real   TU      = {_TU};

    integer count, st;
    real    tq;

{_GAUSS}
    initial begin
        count   = 0;
        clk_out = 1'b0;
        st      = seed;
    end

    always @(posedge clk_in) if (n_div > 1) begin
        tq = td_cq;
        if (sigma_j > 0.0) tq = tq * (1.0 + sigma_j * gauss(0));
        if (tq < TU) tq = TU;
        count = count + 1;
        if (count >= n_div) begin
            count = 0;
            clk_out <= #(tq / TU) 1'b1;
        end else if (count == n_div / 2)
            clk_out <= #(tq / TU) 1'b0;
    end

    // n_div = 1: pass the clock through
    always @(clk_in) if (n_div <= 1)
        clk_out <= #(td_cq / TU) clk_in;
endmodule
""")
    return path


def _write_pll_top(d: PLLDesign, out: str) -> str:
    """Top-level PLL interconnecting all sub-blocks (mixed-signal)."""
    path = os.path.join(out, "kestrel_pll_top.vams")
    if d.kvco_sign < 0:
        cp_wiring = """\
    // Negative Kvco (Maneatis polarity: f falls as Vctrl rises): the PFD's
    // UP must lower the control voltage, so UP drives the pump's sink input
    // and DN its source input.
    kestrel_charge_pump cp_i (
        .up(dn),
        .dn(up),
        .out(cp_out)
    );
"""
    else:
        cp_wiring = """\
    kestrel_charge_pump cp_i (
        .up(up),
        .dn(dn),
        .out(cp_out)
    );
"""

    with open(path, "w") as f:
        f.write(_header("kestrel_pll_top", d, """\
//
// ref_clk, clk_out, clk_outb and the internal up/dn/fb_clk nets are logic;
// cp_out, vctrl and vctrl_mon are electrical.  Every block converts at its
// own boundary (the VCO reads V(vctrl) in its logic process, the pump reads
// UP/DN in its analog block), so no connect modules are needed.
//
// stat-sim: seed, sigma_mm and sigma_j are passed to the logic blocks
// (seed, seed+1, seed+2 for the VCO, PFD and divider streams).
"""))
        f.write(f"""\

module kestrel_pll_top(ref_clk, clk_out, clk_outb, vctrl_mon);
    input  ref_clk;
    output clk_out, clk_outb, vctrl_mon;
    wire   ref_clk, clk_out, clk_outb;
    electrical vctrl_mon;
    electrical cp_out, vctrl;
    wire   up, dn, fb_clk;

    parameter integer seed     = 1;     // stat-sim: random stream seed
    parameter real    sigma_mm = 0.0;   // stat-sim: VCO per-stage mismatch (fraction)
    parameter real    sigma_j  = 0.0;   // stat-sim: per-edge jitter, VCO stages and flops (fraction)

    kestrel_pfd #(.sigma_j(sigma_j), .seed(seed + 1)) pfd_i (
        .ref_clk(ref_clk),
        .fb_clk(fb_clk),
        .up(up),
        .dn(dn)
    );

{cp_wiring}
    kestrel_loop_filter lf_i (
        .inp(cp_out),
        .vctrl(vctrl)
    );

    kestrel_vco #(.sigma_mm(sigma_mm), .sigma_j(sigma_j), .seed(seed)) vco_i (
        .vctrl(vctrl),
        .clk_out(clk_out),
        .clk_outb(clk_outb)
    );

    kestrel_divider #(.sigma_j(sigma_j), .seed(seed + 2)) div_i (
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


def _write_testbench(d: PLLDesign, out: str) -> str:
    """Closed-loop testbench: reference generator, DUT, lock measurements."""
    path = os.path.join(out, "kestrel_tb.vams")
    s = d.spec
    ref_period = 1.0 / s.ref_freq
    ref_delay = reference_release_delay(d)
    t_stop = simulation_time(d)

    with open(path, "w") as f:
        f.write(_header("kestrel_tb", d, f"""\
//
// Closed loop of kestrel_pll_top.  The reference is released at
// {format_eng(ref_delay, 's')}, after the divider's first edges (a reference edge arriving
// first would latch UP; see kestrel_tb.sp).  Prints the ref->fb phase
// error every 10 reference cycles and, at the end, the VCO frequency and
// rms period jitter over the last quarter of the run.
//   expected acquisition ~{format_eng(d.acq_time, 's')} from Vctrl = {d.vctrl_start:.2g} V, run {format_eng(t_stop, 's')}
//   stat-sim: override seed / sigma_mm / sigma_j to run a Monte-Carlo sample
"""))
        f.write(f"""\

module kestrel_tb;
    parameter real    t_ref     = {ref_period:.6e};   // s — reference period ({format_eng(s.ref_freq, 'Hz')})
    parameter real    ref_delay = {ref_delay:.6e};   // s — reference released after the divider runs
    parameter real    t_stop    = {t_stop:.6e};   // s
    parameter real    sigma_ref = 0.0;           // stat-sim: reference edge jitter, s rms
    parameter integer seed      = 1;
    parameter real    sigma_mm  = 0.0;
    parameter real    sigma_j   = 0.0;
    localparam real   TU        = {_TU};

    reg  ref_clk;
    wire clk_out, clk_outb;
    electrical vctrl_mon;

    kestrel_pll_top #(.seed(seed), .sigma_mm(sigma_mm), .sigma_j(sigma_j)) dut (
        .ref_clk(ref_clk),
        .clk_out(clk_out),
        .clk_outb(clk_outb),
        .vctrl_mon(vctrl_mon)
    );

    integer st;
    real    tj;
{_GAUSS}
    // Reference: {format_eng(s.ref_freq, 'Hz')}, released at ref_delay, optional edge jitter
    initial begin
        st = seed + 3;
        ref_clk = 1'b0;
        #(ref_delay / TU);
        forever begin
            tj = t_ref / 2.0;
            if (sigma_ref > 0.0) tj = tj + sigma_ref * gauss(0);
            #(tj / TU);
            ref_clk = ~ref_clk;
        end
    end

    // Phase error: feedback edge relative to the nearest reference edge (signed)
    real    t_ref_edge, phase_err;
    integer n_ref;
    initial begin n_ref = 0; t_ref_edge = 0.0; phase_err = 0.0; end
    always @(posedge ref_clk) begin
        t_ref_edge = $realtime * TU;
        n_ref = n_ref + 1;
    end
    always @(posedge dut.fb_clk) begin
        phase_err = $realtime * TU - t_ref_edge;
        if (phase_err > t_ref / 2.0) phase_err = phase_err - t_ref;   // signed, nearest reference edge
        if (n_ref % 10 == 0)
            $display("kestrel_tb: t = %.3f us  ref->fb = %.3f ns  vctrl = %.4f V",
                     $realtime * TU * 1e6, phase_err * 1e9, V(vctrl_mon));
    end

    // VCO period statistics over the last quarter of the run
    real    t_last, per, sum1, sum2;
    integer n_per;
    initial begin t_last = 0.0; sum1 = 0.0; sum2 = 0.0; n_per = 0; end
    always @(posedge clk_out) begin
        per = $realtime * TU - t_last;
        t_last = $realtime * TU;
        if ($realtime * TU > 0.75 * t_stop) begin
            n_per = n_per + 1;
            sum1 = sum1 + per;
            sum2 = sum2 + per * per;
        end
    end

    initial begin
        #(t_stop / TU);
        if (n_per > 1)
            $display("kestrel_tb: f_vco = %.4f MHz, rms period jitter = %.3f ps, last ref->fb = %.3f ns, vctrl = %.4f V (last quarter of %.3f us, seed %0d)",
                     1e-6 / (sum1 / n_per),
                     1e12 * $sqrt(sum2 / n_per - (sum1 / n_per) * (sum1 / n_per)),
                     phase_err * 1e9, V(vctrl_mon), t_stop * 1e6, seed);
        else
            $display("kestrel_tb: VCO produced no edges in the last quarter of the run");
        $finish;
    end
endmodule
""")
    return path
