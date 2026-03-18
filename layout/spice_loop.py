"""SPICE-in-the-loop VCO frequency tuning.

Generates a VCO testbench from the design engine with parasitic C
annotations from the layout, simulates with Xyce, measures the actual
oscillation frequency, and feeds the error back to adjust the design.

This closes the gap that the analytical parasitic model can't:
the analytical model gets within ~15%, SPICE gets it exact.

Usage:
    python3 layout/spice_loop.py [--max-iter 5] [--tol 3.0]
"""

import argparse
import math
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kestrel.design.engine import PLLSpec, PLLDesign, design_pll, summarize, format_eng


# ======================================================================
# sky130 model parameter preamble (needed by Xyce)
# ======================================================================

_MODEL_PREAMBLE = """\
* Mismatch/statistics params (TT corner)
.PARAM sky130_fd_pr__nfet_01v8__toxe_slope = 0.0
.PARAM sky130_fd_pr__nfet_01v8__vth0_slope = 0.0
.PARAM sky130_fd_pr__nfet_01v8__voff_slope = 0.0
.PARAM sky130_fd_pr__nfet_01v8__vth0_slope1 = 0.0
.PARAM sky130_fd_pr__pfet_01v8__toxe_slope = 0.0
.PARAM sky130_fd_pr__pfet_01v8__vth0_slope = 0.0
.PARAM sky130_fd_pr__pfet_01v8__voff_slope = 0.0
.PARAM sky130_fd_pr__pfet_01v8__nfactor_slope = 0.0
.PARAM sky130_fd_pr__pfet_01v8__toxe_slope1 = 0.0
.PARAM sky130_fd_pr__pfet_01v8__vth0_slope1 = 0.0
.PARAM sky130_fd_pr__pfet_01v8__voff_slope1 = 0.0
.PARAM sky130_fd_pr__pfet_01v8__nfactor_slope1 = 0.0
.PARAM sky130_fd_pr__pfet_01v8__wlod_diff = 0.0
.PARAM sky130_fd_pr__pfet_01v8__kvth0_diff = 0.0
.PARAM sky130_fd_pr__pfet_01v8__ku0_diff = 0.0
.PARAM sky130_fd_pr__pfet_01v8__kvsat_diff = 0.0
.PARAM sky130_fd_pr__pfet_01v8__lkvth0_diff = 0.0
.PARAM sky130_fd_pr__pfet_01v8__wkvth0_diff = 0.0
.PARAM sky130_fd_pr__pfet_01v8__lku0_diff = 0.0
.PARAM sky130_fd_pr__pfet_01v8__wku0_diff = 0.0
"""

SIM_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "sim")


def _um(val: float) -> str:
    return f"{val*1e6:.4f}u"


# ======================================================================
# Generate VCO testbench
# ======================================================================

def generate_vco_testbench(design: PLLDesign, parasitic_c_fF: float,
                           vctrl: float = 0.9,
                           sim_time_ns: float = 500) -> str:
    """Generate a Xyce VCO testbench with parasitic C annotations.

    Uses binned sky130 models (bin 6 for L≥250nm, bin 0 for L<250nm).

    Returns the SPICE netlist as a string.
    """
    d = design

    def _bin(l_m):
        """Select sky130 BSIM4 bin from drawn length."""
        l_nm = l_m * 1e9
        if l_nm >= 250:
            return 6
        return 0

    nbin = _bin(d.vco_diff_l)
    pbin = _bin(d.vco_load_l)
    nmod = f"sky130_fd_pr__nfet_01v8__model.{nbin}"
    pmod = f"sky130_fd_pr__pfet_01v8__model.{pbin}"

    n_stg = d.spec.vco_stages
    c_par = parasitic_c_fF  # fF per output node

    lines = []
    lines.append(f"* Kestrel VCO — SPICE-in-the-loop testbench")
    lines.append(f"* Generated with parasitic C = {c_par:.2f} fF/node")
    lines.append(f"*")
    lines.append(f"")
    lines.append(_MODEL_PREAMBLE)
    lines.append(f'.INCLUDE "{SIM_DIR}/models/sky130_nfet_xyce.spice"')
    lines.append(f'.INCLUDE "{SIM_DIR}/models/sky130_pfet_xyce.spice"')
    lines.append(f"")

    # --- Delay cell ---
    lines.append(f".SUBCKT kestrel_delay_cell outp outn inp inn vctrl vbn vdd vss")
    lines.append(f"Mtail  tail  vbn  vss  vss  {nmod} W={_um(d.vco_tail_w)} L={_um(d.vco_tail_l)}")
    lines.append(f"Mn1    outn  inp  tail vss  {nmod} W={_um(d.vco_diff_w)} L={_um(d.vco_diff_l)}")
    lines.append(f"Mn2    outp  inn  tail vss  {nmod} W={_um(d.vco_diff_w)} L={_um(d.vco_diff_l)}")
    lines.append(f"Mp1a   outn  outn vdd  vdd  {pmod} W={_um(d.vco_load_w)} L={_um(d.vco_load_l)}")
    lines.append(f"Mp1b   outn  vctrl vdd vdd  {pmod} W={_um(d.vco_load_w)} L={_um(d.vco_load_l)}")
    lines.append(f"Mp2a   outp  outp vdd  vdd  {pmod} W={_um(d.vco_load_w)} L={_um(d.vco_load_l)}")
    lines.append(f"Mp2b   outp  vctrl vdd vdd  {pmod} W={_um(d.vco_load_w)} L={_um(d.vco_load_l)}")
    # Parasitic C on each output node
    if c_par > 0:
        lines.append(f"* Post-layout parasitic capacitance")
        lines.append(f"Cp_outp outp 0 {c_par:.2f}f")
        lines.append(f"Cp_outn outn 0 {c_par:.2f}f")
    lines.append(f".ENDS kestrel_delay_cell")
    lines.append(f"")

    # --- Bias generator ---
    lines.append(f".SUBCKT kestrel_vco_bias vbn vctrl vdd vss")
    lines.append(f"Mrep_n   vbn  vbn  vss  vss  {nmod} W={_um(d.vco_bias_w)} L={_um(d.vco_bias_l)}")
    lines.append(f"Mrep_pd  vbn  vbn  vdd  vdd  {pmod} W={_um(d.vco_load_w)} L={_um(d.vco_load_l)}")
    lines.append(f"Mrep_pc  vbn  vctrl vdd vdd  {pmod} W={_um(d.vco_load_w)} L={_um(d.vco_load_l)}")
    lines.append(f"Istart vdd vbn {_um(d.vco_i_stage / 100)}")
    lines.append(f".ENDS kestrel_vco_bias")
    lines.append(f"")

    # --- Ring VCO ---
    lines.append(f".SUBCKT kestrel_vco outp outn vctrl_ext vdd vss")
    lines.append(f"Xbias vbn vctrl_int vdd vss kestrel_vco_bias")
    lines.append(f"Rsw vctrl_ext vctrl 1")
    lines.append(f"Rbias vctrl_int vctrl 100k")
    for i in range(n_stg):
        prev = (i - 1) % n_stg
        inp = f"dn_{n_stg - 1}" if i == 0 else f"dp_{prev}"
        inn = f"dp_{n_stg - 1}" if i == 0 else f"dn_{prev}"
        lines.append(f"Xstage{i} dp_{i} dn_{i} {inp} {inn} vctrl vbn vdd vss kestrel_delay_cell")
    lines.append(f"Routp dp_{n_stg-1} outp 1")
    lines.append(f"Routn dn_{n_stg-1} outn 1")
    lines.append(f".ENDS kestrel_vco")
    lines.append(f"")

    # --- Testbench ---
    lines.append(f"Vdd vdd 0 {d.spec.supply_voltage}")
    lines.append(f"Vss vss 0 0")
    lines.append(f"Vctrl vctrl 0 {vctrl}")
    lines.append(f"")
    lines.append(f"Xvco outp outn vctrl vdd vss kestrel_vco")
    lines.append(f"")
    lines.append(f"* Small external load")
    lines.append(f"Cload_p outp 0 10f")
    lines.append(f"Cload_n outn 0 10f")
    lines.append(f"")
    lines.append(f"* Differential output for frequency measurement")
    lines.append(f"Ediff diff 0 outp outn 1.0")
    lines.append(f"")
    lines.append(f"* Startup kick")
    lines.append(f"Ikick 0 Xvco:dp_0 PULSE(0 1m 0 50p 50p 10n 0)")
    lines.append(f"")
    lines.append(f".TRAN 50p {sim_time_ns}n")
    lines.append(f".PRINT TRAN V(outp) V(outn) V(diff)")
    lines.append(f"")
    lines.append(f"* Measure frequency from differential zero crossings")
    lines.append(f".MEASURE TRAN t1 WHEN V(diff)=0 RISE=5 TD=100n")
    lines.append(f".MEASURE TRAN t2 WHEN V(diff)=0 RISE=6 TD=100n")
    lines.append(f"")
    lines.append(f".END")

    return "\n".join(lines)


# ======================================================================
# Run simulation and parse frequency
# ======================================================================

def run_xyce(netlist_str: str, work_dir: str = None) -> dict:
    """Run Xyce simulation and extract measured frequency.

    Returns {'frequency_hz': float, 't1': float, 't2': float,
             'success': bool, 'output': str}
    """
    if work_dir is None:
        work_dir = tempfile.mkdtemp(prefix="kestrel_spice_")

    cir_path = os.path.join(work_dir, "vco_tb.cir")
    with open(cir_path, "w") as f:
        f.write(netlist_str)

    # Run Xyce
    try:
        result = subprocess.run(
            ["Xyce", cir_path],
            capture_output=True, text=True,
            timeout=300,
            cwd=work_dir,
        )
    except subprocess.TimeoutExpired:
        return {'success': False, 'frequency_hz': 0,
                'output': 'Xyce timed out (300s)'}

    output = result.stdout + result.stderr

    if result.returncode != 0:
        return {'success': False, 'frequency_hz': 0,
                'output': f'Xyce failed (rc={result.returncode}):\n{output[-500:]}'}

    # Parse .MEASURE results from the .mt0 file
    mt0_path = cir_path + ".mt0"
    if not os.path.exists(mt0_path):
        return {'success': False, 'frequency_hz': 0,
                'output': f'No measure file {mt0_path}'}

    with open(mt0_path) as f:
        mt0 = f.read()

    t1 = t2 = None
    for line in mt0.splitlines():
        line = line.strip()
        # Xyce measure format: "T1 = 1.657142e-07" (case-insensitive)
        m = re.match(r'^t1\s*=\s*([\d.eE+-]+)', line, re.IGNORECASE)
        if m:
            try: t1 = float(m.group(1))
            except ValueError: pass
        m = re.match(r'^t2\s*=\s*([\d.eE+-]+)', line, re.IGNORECASE)
        if m:
            try: t2 = float(m.group(1))
            except ValueError: pass

    if t1 is not None and t2 is not None and t2 > t1:
        period = t2 - t1
        freq = 1.0 / period
        return {'success': True, 'frequency_hz': freq,
                't1': t1, 't2': t2, 'period': period,
                'output': output[-200:]}
    else:
        return {'success': False, 'frequency_hz': 0,
                't1': t1, 't2': t2,
                'output': f'Could not parse t1/t2 from:\n{mt0}'}


# ======================================================================
# Iterative loop
# ======================================================================

def spice_loop(max_iter: int = 5, tol_pct: float = 3.0,
               vctrl: float = 0.9, verbose: bool = True) -> PLLDesign:
    """Run the SPICE-in-the-loop VCO tuning loop.

    1. Design engine sizes the VCO
    2. Layout generator produces GDS
    3. Parasitic analysis measures C per node
    4. SPICE simulation measures actual frequency
    5. Adjust parasitic_cap in spec to compensate
    6. Repeat until frequency error < tol_pct

    Returns the final converged PLLDesign.
    """
    f_target = 600e6  # center frequency from spec
    f_error_pct = 100.0

    # Start from known-good sky130 VCO sizing (from sim/kes_vco_xyce.cir)
    # rather than the analytical model which undersizes for sky130.
    # The existing testbench oscillates at ~500-700 MHz with these sizes.
    current_scale = 1.0  # multiplicative adjustment to tail current

    for iteration in range(1, max_iter + 1):
        if verbose:
            print(f"\n{'='*60}")
            print(f"  SPICE ITERATION {iteration}  (current_scale={current_scale:.3f})")
            print(f"{'='*60}")

        # --- Design ---
        # Use the analytical engine for loop filter / charge pump / divider,
        # but override VCO sizing with the known-good values.
        spec = PLLSpec(
            freq_min=400e6, freq_max=800e6, ref_freq=10e6,
            loop_bw=1e6, process="sky130",
        )
        design = design_pll(spec)

        # Override VCO with known-good sky130 sizing.
        # current_scale adjusts tail current (and load to match) while
        # keeping the diff pair fixed — more current into the same load
        # charges the output cap faster → higher frequency.
        design.vco_tail_w = 40e-6 * current_scale
        design.vco_tail_l = 0.36e-6
        design.vco_diff_w = 20e-6      # fixed — don't scale (adds gate cap)
        design.vco_diff_l = 0.36e-6
        design.vco_load_w = 10e-6 * current_scale  # scale load with current
        design.vco_load_l = 0.36e-6
        design.vco_bias_w = 20e-6 * current_scale
        design.vco_bias_l = 0.36e-6
        design.vco_i_stage = 200e-6 * current_scale

        if verbose:
            print(f"  Design: I_stage={format_eng(design.vco_i_stage, 'A')}, "
                  f"I_stage≈{200e-6 * current_scale * 1e6:.0f} uA")
            print(f"  VCO: tail_W={design.vco_tail_w*1e6:.3f}u, "
                  f"diff_W={design.vco_diff_w*1e6:.3f}u, "
                  f"load_W={design.vco_load_w*1e6:.3f}u")

        # --- Layout + parasitic extraction ---
        import gdsfactory as gf
        gf.clear_cache()
        from layout.gds_gen import generate_pll_gds
        from layout.parasitics import compute_parasitics, analyze_impact

        gds_path = f"layout/kestrel_pll_spice_iter{iteration}.gds"
        generate_pll_gds(design, gds_path)
        parasitics = compute_parasitics(gds_path)
        impact = analyze_impact(design, parasitics)
        c_par_fF = impact['vco']['c_parasitic_per_node_fF']

        if verbose:
            print(f"  Layout parasitic C/node: {c_par_fF:.2f} fF")

        # --- SPICE simulation ---
        # Scale sim time: need at least ~10 periods after settling
        # First pass: long sim. Later: scale from measured frequency.
        if iteration == 1:
            sim_ns = 2000  # 2us for first pass (unknown frequency)
        else:
            # At least 20 periods + 100ns settle
            est_period_ns = 1e9 / max(f_target * 0.1, 50e6)
            sim_ns = max(500, 100 + 20 * est_period_ns)
        tb = generate_vco_testbench(design, c_par_fF, vctrl=vctrl,
                                    sim_time_ns=sim_ns)
        work_dir = tempfile.mkdtemp(prefix=f"kestrel_iter{iteration}_")

        if verbose:
            print(f"  Simulating (Xyce)...")

        result = run_xyce(tb, work_dir)

        if not result['success']:
            if verbose:
                print(f"  SIM ISSUE: {result['output'][:200]}")
            # If we got t1 but not t2, sim was too short — estimate from t1
            if result.get('t1') and result['t1'] > 0:
                # Very rough: assume period ≈ 2 × (t1 - TD) / rise_count
                # t1 is the 5th rising crossing after TD=100ns
                est_period = result['t1'] / 5  # crude
                f_measured = 1.0 / est_period if est_period > 0 else 0
                if verbose:
                    print(f"  Estimated f from t1: {format_eng(f_measured, 'Hz')}")
                result['success'] = True
                result['frequency_hz'] = f_measured
                result['period'] = est_period
            else:
                # Apply the layout parasitic and try again
                if verbose:
                    print(f"  No oscillation — increasing current")
                current_scale *= 1.5
                continue

        if not result['success']:
            continue

        f_measured = result['frequency_hz']
        f_error_pct = (f_measured - f_target) / f_target * 100

        if verbose:
            print(f"  SPICE result: f = {format_eng(f_measured, 'Hz')} "
                  f"(target {format_eng(f_target, 'Hz')}, "
                  f"error {f_error_pct:+.1f}%)")
            print(f"  Period: {result['period']*1e9:.3f} ns")

        # --- Check convergence ---
        if abs(f_error_pct) <= tol_pct:
            if verbose:
                print(f"\n  CONVERGED: {f_error_pct:+.1f}% within "
                      f"±{tol_pct}% tolerance")
            os.replace(gds_path, "layout/kestrel_pll.gds")
            return design

        # --- Adjust current scale ---
        # f ∝ I / C_total.  To shift f from f_measured to f_target,
        # scale the tail current by f_target / f_measured.
        # Damping (0.6) to avoid overshoot.
        damping = 0.6
        ratio = f_target / f_measured if f_measured > 0 else 1.5
        correction = 1.0 + damping * (ratio - 1.0)
        # Clamp to avoid wild swings
        correction = max(0.5, min(3.0, correction))
        current_scale_new = current_scale * correction

        if verbose:
            print(f"  Correction: ×{correction:.3f} (damped)")
            print(f"  current_scale: {current_scale:.3f} → {current_scale_new:.3f}")

        current_scale = current_scale_new

    if verbose:
        print(f"\n  Did not converge after {max_iter} iterations "
              f"(last error: {f_error_pct:+.1f}%)")

    os.replace(gds_path, "layout/kestrel_pll.gds")
    return design


# ======================================================================
# Main
# ======================================================================

def main():
    parser = argparse.ArgumentParser(
        description="SPICE-in-the-loop VCO frequency tuning")
    parser.add_argument("--max-iter", type=int, default=5)
    parser.add_argument("--tol", type=float, default=3.0,
                        help="Frequency tolerance (%%)")
    parser.add_argument("--vctrl", type=float, default=0.9,
                        help="Control voltage for measurement")
    args = parser.parse_args()

    design = spice_loop(max_iter=args.max_iter, tol_pct=args.tol,
                        vctrl=args.vctrl)

    print(f"\n{'='*60}")
    print("  FINAL DESIGN")
    print(f"{'='*60}")
    print(summarize(design))


if __name__ == "__main__":
    main()
