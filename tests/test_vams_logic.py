#!/usr/bin/env python3
"""Closed-loop check of the emitted Verilog-AMS logic with Icarus Verilog.

The emitted PFD and divider are plain event-driven Verilog and compile
verbatim.  The VCO reads V(vctrl) in its logic process; for this check its
control port is turned into a real-valued port (the one textual change,
made here) and the analog charge pump + loop filter are replaced by an
exact event-driven model of the same circuit (kestrel_cp_lf_evt: the
R1-C1 / C2 filter driven by a piecewise-constant pump current has a
closed-form response between pump events).  The loop must lock at N*f_ref
with a bounded ref->fb phase error for both the SG13G2 and the sky130
designs.

Skips (exit 0, SKIP printed) when iverilog is not found: set IVERILOG /
VVP, or have them on PATH, or in the kev-cam fork's build tree.
"""

import os, re, shutil, subprocess, sys, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from kestrel.generators.pll.engine import PLLSpec, design_pll
from kestrel.generators.pll.models.verilog_ams import (
    emit_verilog_ams, reference_release_delay, simulation_time)

PASS = 0
FAIL = 0


def check(name, condition, detail=''):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ''))


def find_tool(env, name, extra):
    p = os.environ.get(env)
    if p and os.access(p, os.X_OK):
        return p
    p = shutil.which(name)
    if p:
        return p
    for cand in extra:
        if os.access(cand, os.X_OK):
            return cand
    return None


IVERILOG = find_tool('IVERILOG', 'iverilog', ['/usr/local/src/iverilog/driver/iverilog'])
VVP = find_tool('VVP', 'vvp', ['/usr/local/src/iverilog/vvp/vvp'])


# Exact event-driven charge pump + loop filter (test harness only).
#
# Pump current i into node v; C2 v->gnd; R1 v->mid; C1 mid->gnd.  With
# x = v - mid (the drop across R1) and tau = R1*C1*C2/(C1+C2):
#   dx/dt  = i/C2 - x/tau            -> x(t) = xs + (x0 - xs) e^(-t/tau),
#                                       xs = i*R1*C1/(C1+C2)
#   dmid/dt = x/(R1*C1)              -> mid(t) = mid0 + [xs t + (x0-xs) tau (1-e^(-t/tau))]/(R1 C1)
# so the state is advanced exactly at every pump event; a tick refreshes
# the output between events so the VCO always samples a fresh value.
CP_LF_EVT = """\
`timescale 1ps / 1fs
module kestrel_cp_lf_evt(input up, input dn, output real vctrl);
    parameter real    icp    = 100e-6;
    parameter real    r1     = 1e3;
    parameter real    c1     = 10e-12;
    parameter real    c2     = 1e-12;
    parameter real    vdd    = 1.8;
    parameter real    v_init = 0.0;
    parameter real    t_tick = 50e-12;   // output refresh between pump events
    parameter integer sign   = 1;        // -1: UP sinks (negative-Kvco wiring)
    localparam real   TU     = 1e-12;

    real v1, x, i, t0, ctot, tau, xs, dt, e, now;
    integer iu, idn;

    task advance;
        begin
            now = $realtime * TU;
            dt  = now - t0;
            xs  = i * r1 * c1 / ctot;
            e   = $exp(-dt / tau);
            v1  = v1 + (xs * dt + (x - xs) * tau * (1.0 - e)) / (r1 * c1);
            x   = xs + (x - xs) * e;
            t0  = now;
            if (v1 + x > vdd) begin
                if (v1 > vdd) v1 = vdd;
                x = vdd - v1;
            end
            if (v1 + x < 0.0) begin
                if (v1 < 0.0) v1 = 0.0;
                x = -v1;
            end
            vctrl = v1 + x;
        end
    endtask

    initial begin
        ctot = c1 + c2;
        tau  = r1 * c1 * c2 / ctot;
        v1 = v_init; x = 0.0; i = 0.0; t0 = 0.0;
        vctrl = v_init;
    end

    always @(up or dn) begin
        advance;
        iu  = (up  === 1'b1) ? 1 : 0;
        idn = (dn  === 1'b1) ? 1 : 0;
        i   = sign * icp * (iu - idn);
    end

    always #(t_tick / TU) advance;
endmodule
"""

TB = """\
`timescale 1ps / 1fs
module kestrel_logic_tb;
    parameter real    t_ref     = {t_ref:.6e};
    parameter real    ref_delay = {ref_delay:.6e};
    parameter real    t_stop    = {t_stop:.6e};
    parameter integer n_div     = {n_div};
    localparam real   TU        = 1e-12;

    reg  ref_clk;
    wire clk_out, clk_outb, up, dn, fb_clk;
    real vctrl;

    kestrel_pfd pfd (.ref_clk(ref_clk), .fb_clk(fb_clk), .up(up), .dn(dn));
    kestrel_cp_lf_evt #(.icp({icp:.6e}), .r1({r1:.6e}), .c1({c1:.6e}), .c2({c2:.6e}),
                        .vdd({vdd:.3f}), .v_init({v_init:.4f}), .sign({sign}))
        cplf (.up(up), .dn(dn), .vctrl(vctrl));
    kestrel_vco vco (.vctrl(vctrl), .clk_out(clk_out), .clk_outb(clk_outb));
    kestrel_divider div (.clk_in(clk_out), .clk_out(fb_clk));

    initial begin
        ref_clk = 1'b0;
        #(ref_delay / TU);
        forever #(t_ref / 2.0 / TU) ref_clk = ~ref_clk;
    end

    real    t_ref_edge, pe, pe_max, pe_last, t_first, t_last;
    integer n_ref, n_edge;
    initial begin t_ref_edge = 0.0; pe_max = 0.0; pe_last = 0.0; n_ref = 0; n_edge = 0; t_first = -1.0; end
    always @(posedge ref_clk) begin t_ref_edge = $realtime * TU; n_ref = n_ref + 1; end
    // Signed phase error: feedback edge relative to the nearest reference edge
    always @(posedge fb_clk) begin
        pe = $realtime * TU - t_ref_edge;
        if (pe > t_ref / 2.0) pe = pe - t_ref;
        if ($realtime * TU > 0.75 * t_stop) begin
            pe_last = pe;
            if (pe > pe_max) pe_max = pe;
            if (-pe > pe_max) pe_max = -pe;
        end
    end
    always @(posedge clk_out) if ($realtime * TU > 0.75 * t_stop) begin
        if (t_first < 0.0) t_first = $realtime * TU;
        t_last = $realtime * TU;
        n_edge = n_edge + 1;
    end
    initial begin
        #(t_stop / TU);
        $display("KESTREL_LOGIC f_vco_MHz=%.5f pe_max_ns=%.4f pe_last_ns=%.4f vctrl=%.5f n_ref=%0d",
                 (n_edge > 1) ? 1e-6 * (n_edge - 1) / (t_last - t_first) : 0.0,
                 pe_max * 1e9, pe_last * 1e9, vctrl, n_ref);
        $finish;
    end
endmodule
"""


def derive_real_port_vco(text):
    """The emitted VCO with its electrical control port made a real port."""
    head = re.search(r"module kestrel_vco\(vctrl, clk_out, clk_outb\);\n"
                     r"    input  vctrl;\n    output clk_out, clk_outb;\n"
                     r"    electrical vctrl;\n    reg    clk_out;\n    wire   clk_outb;\n", text)
    assert head, "VCO port block not as emitted"
    text = text.replace(head.group(0),
                        "module kestrel_vco(input real vctrl, output reg clk_out, output wire clk_outb);\n")
    assert text.count("V(vctrl)") == 1, "VCO should read V(vctrl) once"
    return text.replace("V(vctrl)", "vctrl")


def run_loop(label, spec):
    design = design_pll(spec)
    work = tempfile.mkdtemp(prefix=f'kestrel_vams_{label}_')
    try:
        emit_verilog_ams(design, work)
        for stub in ('disciplines.vams', 'constants.vams'):
            open(os.path.join(work, stub), 'w').close()
        vco = open(os.path.join(work, 'kestrel_vco.vams')).read()
        open(os.path.join(work, 'vco_real.v'), 'w').write(derive_real_port_vco(vco))
        open(os.path.join(work, 'cp_lf_evt.v'), 'w').write(CP_LF_EVT)
        t_stop = simulation_time(design)
        open(os.path.join(work, 'tb.v'), 'w').write(TB.format(
            t_ref=1.0 / spec.ref_freq, ref_delay=reference_release_delay(design),
            t_stop=t_stop, n_div=design.n_nom, icp=design.icp, r1=design.r_filter,
            c1=design.c1, c2=design.c2, vdd=spec.supply_voltage,
            v_init=design.vctrl_start, sign=design.kvco_sign))
        srcs = ['kestrel_pfd.vams', 'kestrel_divider.vams', 'vco_real.v', 'cp_lf_evt.v', 'tb.v']
        cmd = [IVERILOG, '-g2012', '-I', work, '-o', 'tb.vvp'] + srcs
        r = subprocess.run(cmd, cwd=work, capture_output=True, text=True)
        check(f'{label}: iverilog compiles the emitted PFD, divider and VCO logic',
              r.returncode == 0, (r.stdout + r.stderr).strip()[-600:])
        if r.returncode != 0:
            return
        r = subprocess.run([VVP, '-n', 'tb.vvp'], cwd=work, capture_output=True, text=True, timeout=600)
        m = re.search(r'KESTREL_LOGIC f_vco_MHz=([0-9.]+) pe_max_ns=([-0-9.]+) pe_last_ns=([-0-9.]+) '
                      r'vctrl=([-0-9.]+) n_ref=(\d+)', r.stdout)
        check(f'{label}: simulation ran to the end', m is not None, (r.stdout + r.stderr).strip()[-600:])
        if not m:
            return
        f_vco, pe_max, pe_last, vctrl, n_ref = (float(m.group(1)), float(m.group(2)),
                                                float(m.group(3)), float(m.group(4)), int(m.group(5)))
        f_target = design.n_nom * spec.ref_freq
        print(f"    {label}: f_vco = {f_vco:.4f} MHz (target {f_target/1e6:.4f}), "
              f"|ref->fb| max {pe_max:.3f} ns, last {pe_last:+.3f} ns over the last quarter "
              f"of {t_stop*1e6:.3f} us, vctrl = {vctrl:.4f} V")
        check(f'{label}: locked at N*f_ref within 0.05 %',
              abs(f_vco * 1e6 - f_target) < 5e-4 * f_target, f'{f_vco} MHz')
        check(f'{label}: |ref->fb| phase error bounded (< 1 ns) once locked',
              0.0 <= pe_max < 1.0, f'max {pe_max} ns')
        check(f'{label}: control voltage inside the supply',
              0.0 <= vctrl <= spec.supply_voltage, f'{vctrl} V')
        if design.vctrl_nom:
            check(f'{label}: control voltage near the design point ({design.vctrl_nom:.2f} V)',
                  abs(vctrl - design.vctrl_nom) < 0.25 * spec.supply_voltage, f'{vctrl} V')
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    print("=== kestrel Verilog-AMS logic (iverilog closed loop) ===\n")
    if not (IVERILOG and VVP):
        print("  SKIP  iverilog/vvp not found (set IVERILOG and VVP)")
        sys.exit(0)
    print(f"  using {IVERILOG}")
    run_loop('sg13g2', PLLSpec(freq_min=840e6, freq_max=1080e6, ref_freq=30e6, loop_bw=2e6,
                               supply_voltage=1.2, process='sg13g2'))
    run_loop('sky130', PLLSpec(freq_min=800e6, freq_max=1.2e9, ref_freq=25e6, loop_bw=5e6))
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
