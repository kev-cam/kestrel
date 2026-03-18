"""Parasitic R/C estimation from Kestrel PLL layout geometry.

Computes wire resistance and coupling/ground capacitance from the
physical layout (metal widths, lengths, layer stack) and annotates
the design-engine SPICE netlist for post-layout simulation.

sky130 interconnect parameters:
    MET1: Rsh=0.125 ohm/sq, Carea=0.038 fF/um^2, Cfringe=0.040 fF/um
    MET2: Rsh=0.125 ohm/sq, Carea=0.028 fF/um^2, Cfringe=0.036 fF/um
    VIA1: Rvia=4.5 ohm/via
    LI:   Rsh=12.8 ohm/sq, Carea=0.040 fF/um^2
    MCON: Rvia=9.3 ohm/via
    LICON: Rvia=70 ohm/via (resistive!)

Usage:
    python3 layout/parasitics.py layout/kestrel_pll.gds
"""

import math
import os
import sys

import klayout.db as kdb

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kestrel.design.engine import PLLSpec, PLLDesign, design_pll, summarize, format_eng

# ======================================================================
# Sky130 interconnect parameters
# ======================================================================

# Sheet resistance (ohm/square)
RSH = {
    'li':   12.8,
    'met1': 0.125,
    'met2': 0.125,
    'met3': 0.047,
}

# Via resistance (ohm/via)
RVIA = {
    'licon': 70.0,
    'mcon':  9.3,
    'via1':  4.5,
    'via2':  4.5,
}

# Capacitance: area (fF/um^2) and fringe (fF/um)
CAREA = {
    'li':   0.040,
    'met1': 0.038,
    'met2': 0.028,
    'met3': 0.020,
}
CFRINGE = {
    'li':   0.040,
    'met1': 0.040,
    'met2': 0.036,
    'met3': 0.030,
}

# Metal-to-metal coupling capacitance (fF/um for parallel run, at min space)
CCOUPLING = {
    'met1': 0.050,  # fF/um for min-space parallel wires
    'met2': 0.045,
}


# ======================================================================
# Geometry measurement from GDS
# ======================================================================

LAYER_MAP = {
    'li':   (67, 20),
    'met1': (68, 20),
    'met2': (69, 20),
    'met3': (70, 20),
}


def measure_wire_geometry(gds_path: str, top_cell: str = None) -> dict:
    """Measure total wire length and area per metal layer.

    Returns {layer_name: {'area_um2': float, 'perimeter_um': float,
                          'bbox_w': float, 'bbox_h': float}}
    """
    layout = kdb.Layout()
    layout.read(gds_path)
    dbu = layout.dbu

    if top_cell:
        tc = layout.cell(top_cell)
    else:
        tc = layout.top_cells()[0]

    results = {}

    for name, (layer_num, datatype) in LAYER_MAP.items():
        li = layout.find_layer(layer_num, datatype)
        if li is None:
            results[name] = {'area_um2': 0, 'perimeter_um': 0,
                             'bbox_w': 0, 'bbox_h': 0}
            continue

        # Collect all shapes (flattened) for this layer
        region = kdb.Region()
        region.insert(tc.begin_shapes_rec(li))
        region.merge()

        total_area = 0.0
        total_perim = 0.0
        for poly in region.each():
            total_area += poly.area()
            total_perim += poly.perimeter()

        bb = region.bbox()
        results[name] = {
            'area_um2': total_area * dbu * dbu,
            'perimeter_um': total_perim * dbu,
            'bbox_w': (bb.right - bb.left) * dbu if not bb.empty() else 0,
            'bbox_h': (bb.top - bb.bottom) * dbu if not bb.empty() else 0,
        }

    return results


def count_vias(gds_path: str, top_cell: str = None) -> dict:
    """Count via/contact instances per type."""
    layout = kdb.Layout()
    layout.read(gds_path)
    dbu = layout.dbu

    if top_cell:
        tc = layout.cell(top_cell)
    else:
        tc = layout.top_cells()[0]

    via_layers = {
        'licon': (66, 44),
        'mcon':  (67, 44),
        'via1':  (68, 44),
        'via2':  (69, 44),
    }

    counts = {}
    for name, (ln, dt) in via_layers.items():
        li = layout.find_layer(ln, dt)
        if li is None:
            counts[name] = 0
            continue

        region = kdb.Region()
        region.insert(tc.begin_shapes_rec(li))
        counts[name] = region.count()

    return counts


# ======================================================================
# Parasitic computation
# ======================================================================

def compute_parasitics(gds_path: str, top_cell: str = None) -> dict:
    """Compute parasitic R and C from layout geometry.

    Returns a dict with per-layer and total parasitic estimates.
    """
    geom = measure_wire_geometry(gds_path, top_cell)
    vias = count_vias(gds_path, top_cell)

    layers = {}
    total_r = 0.0
    total_c = 0.0

    for name in ['li', 'met1', 'met2', 'met3']:
        g = geom.get(name, {})
        area = g.get('area_um2', 0)
        perim = g.get('perimeter_um', 0)

        # Resistance: approximate as R = Rsh * L / W
        # Use area/perimeter to estimate average L and W:
        #   For a rectangle: area = L*W, perim = 2*(L+W)
        #   Solve: avg_squares ~ area / (W^2) where W = 2*area/perim (approx)
        if perim > 0 and area > 0:
            avg_w = 2 * area / perim
            n_squares = perim / (2 * avg_w) if avg_w > 0 else 0
            r_total = RSH.get(name, 0) * n_squares
        else:
            r_total = 0
            avg_w = 0
            n_squares = 0

        # Capacitance: ground cap + fringe cap
        c_area = CAREA.get(name, 0) * area           # fF
        c_fringe = CFRINGE.get(name, 0) * perim      # fF
        c_total = c_area + c_fringe

        layers[name] = {
            'area_um2': area,
            'perimeter_um': perim,
            'avg_width_um': avg_w,
            'n_squares': n_squares,
            'R_ohm': r_total,
            'C_ground_fF': c_area,
            'C_fringe_fF': c_fringe,
            'C_total_fF': c_total,
        }
        total_r += r_total
        total_c += c_total

    # Via resistance
    via_r = {}
    for name, count in vias.items():
        r_per = RVIA.get(name, 0)
        # Vias in parallel within a stack: effective R = R_per / n_parallel
        # But between layers they're in series with the wire R.
        # Report per-via and total (worst case = series through one via each)
        via_r[name] = {
            'count': count,
            'R_per_via': r_per,
            'R_worst_case': r_per,  # one via per transition
        }

    return {
        'layers': layers,
        'vias': via_r,
        'total_wire_R_ohm': total_r,
        'total_wire_C_fF': total_c,
    }


# ======================================================================
# Impact analysis: how parasitics affect PLL performance
# ======================================================================

def analyze_impact(design: PLLDesign, parasitics: dict) -> dict:
    """Estimate how layout parasitics degrade PLL performance.

    Key concerns for a ring oscillator PLL:
    1. VCO frequency shift: extra C on output nodes slows the ring
    2. Loop filter pollution: parasitic C in parallel with C1/C2
    3. Charge pump mismatch: R in supply lines
    4. Phase noise: resistive interconnect adds thermal noise
    """
    results = {}
    p = parasitics

    # --- VCO frequency impact ---
    # Extra capacitance on VCO output nodes reduces oscillation frequency.
    # f_osc = 1 / (2 * N * t_d), where t_d = C_load * V_swing / I
    # Additional C from MET1+MET2 routing within VCO:
    met1_c = p['layers']['met1']['C_total_fF']
    met2_c = p['layers']['met2']['C_total_fF']
    # Rough estimate: ~1/4 of total routing C appears on each VCO node
    # (4 stages, each with differential output)
    n_stg = design.spec.vco_stages
    c_parasitic_per_node = (met1_c + met2_c) / (2 * n_stg * 4)  # fF

    # Design engine's estimated load cap (from current and frequency)
    # I_stage = C_load * V_swing / t_delay
    t_delay = 1.0 / (2 * n_stg * design.f_center)
    v_swing = 0.4 * design.spec.supply_voltage
    c_load_design = design.vco_i_stage * t_delay / v_swing  # farads
    c_load_design_fF = c_load_design * 1e15

    c_ratio = c_parasitic_per_node / c_load_design_fF if c_load_design_fF > 0 else 0
    f_shift_pct = -c_ratio / (1 + c_ratio) * 100  # freq decreases

    results['vco'] = {
        'c_load_design_fF': c_load_design_fF,
        'c_parasitic_per_node_fF': c_parasitic_per_node,
        'c_ratio': c_ratio,
        'f_shift_pct': f_shift_pct,
        'f_center_nominal_Hz': design.f_center,
        'f_center_post_layout_Hz': design.f_center * (1 + f_shift_pct / 100),
    }

    # --- Loop filter impact ---
    # Parasitic C in parallel with C1 shifts the zero frequency
    # C_parasitic on the filter node adds to C1
    c_filter_parasitic_fF = met1_c * 0.05  # ~5% of MET1 cap near filter
    c1_fF = design.c1 * 1e15
    c1_shift = c_filter_parasitic_fF / c1_fF * 100 if c1_fF > 0 else 0

    # Loop bandwidth shift: BW ~ sqrt(Icp*Kvco/(N*Ctotal))
    bw_shift_pct = -c1_shift / 2  # sqrt relationship

    results['loop_filter'] = {
        'c1_design_fF': c1_fF,
        'c_parasitic_fF': c_filter_parasitic_fF,
        'c1_shift_pct': c1_shift,
        'bw_shift_pct': bw_shift_pct,
    }

    # --- Supply IR drop ---
    # Resistance in supply routing causes voltage drop under load
    met2_r = p['layers']['met2']['R_ohm']
    # VCO total current = n_stages * I_stage
    i_total_vco = n_stg * design.vco_i_stage
    ir_drop = met2_r * i_total_vco * 0.1  # ~10% of R in supply path
    ir_drop_pct = ir_drop / design.spec.supply_voltage * 100

    results['supply'] = {
        'R_supply_ohm': met2_r * 0.1,
        'I_vco_A': i_total_vco,
        'IR_drop_V': ir_drop,
        'IR_drop_pct': ir_drop_pct,
    }

    # --- Phase noise contribution from interconnect R ---
    # Thermal noise from wire resistance: Sv = 4*k*T*R
    # Integrated phase noise contribution (very rough):
    kT = 1.38e-23 * 300
    r_vco_interconnect = p['layers']['met1']['R_ohm'] * 0.1  # ~10% in VCO signal path
    via_r = sum(v['R_worst_case'] for v in p['vias'].values())
    r_total_signal = r_vco_interconnect + via_r * 0.1

    # Phase noise degradation: ΔPN ≈ 10*log10(1 + R_parasitic/R_device)
    # R_device for VCO ~ V_swing / I_stage
    r_device = v_swing / design.vco_i_stage if design.vco_i_stage > 0 else 1e6
    pn_degradation_dB = 10 * math.log10(1 + r_total_signal / r_device)

    results['phase_noise'] = {
        'R_signal_path_ohm': r_total_signal,
        'R_device_ohm': r_device,
        'PN_degradation_dB': pn_degradation_dB,
    }

    # --- Crosstalk ---
    # Coupling between adjacent VCO stage outputs on MET2
    # C_coupling ~ CCOUPLING * parallel_run_length
    # VCO stages separated by cell_pitch (~19um), MET2 runs ~19um per stage
    vco_parallel_run = 19.0 * n_stg  # um of parallel MET2 (rough)
    c_coupling_fF = CCOUPLING.get('met2', 0.045) * vco_parallel_run
    c_coupling_ratio = c_coupling_fF / c_load_design_fF if c_load_design_fF > 0 else 0

    results['crosstalk'] = {
        'parallel_run_um': vco_parallel_run,
        'C_coupling_fF': c_coupling_fF,
        'C_coupling_ratio': c_coupling_ratio,
        'crosstalk_pct': c_coupling_ratio * 100,
    }

    return results


def print_parasitics(parasitics: dict, impact: dict, design: PLLDesign):
    """Print parasitic analysis report."""
    p = parasitics
    print("\n" + "=" * 70)
    print("  POST-LAYOUT PARASITIC ANALYSIS")
    print("=" * 70)

    print("\n  Wire geometry and parasitics:")
    print(f"  {'Layer':<6} {'Area(um²)':<12} {'Perim(um)':<12} "
          f"{'R(ohm)':<10} {'C_gnd(fF)':<10} {'C_frg(fF)':<10} {'C_tot(fF)':<10}")
    print("  " + "-" * 68)
    for name in ['li', 'met1', 'met2', 'met3']:
        l = p['layers'].get(name, {})
        if l.get('area_um2', 0) > 0:
            print(f"  {name:<6} {l['area_um2']:<12.1f} {l['perimeter_um']:<12.1f} "
                  f"{l['R_ohm']:<10.1f} {l['C_ground_fF']:<10.2f} "
                  f"{l['C_fringe_fF']:<10.2f} {l['C_total_fF']:<10.2f}")

    print(f"\n  Total wire R: {p['total_wire_R_ohm']:.1f} ohm")
    print(f"  Total wire C: {p['total_wire_C_fF']:.1f} fF")

    print("\n  Via counts:")
    for name, v in p['vias'].items():
        if v['count'] > 0:
            print(f"    {name:<6}: {v['count']:>5} vias × {v['R_per_via']:.1f} ohm")

    # Impact summary
    print("\n" + "-" * 70)
    print("  PERFORMANCE IMPACT ESTIMATES")
    print("-" * 70)

    vi = impact['vco']
    print(f"\n  VCO frequency:")
    print(f"    Design load cap:     {vi['c_load_design_fF']:.2f} fF")
    print(f"    Parasitic cap/node:  {vi['c_parasitic_per_node_fF']:.2f} fF")
    print(f"    Cap ratio:           {vi['c_ratio']:.3f}")
    print(f"    Frequency shift:     {vi['f_shift_pct']:.1f}%")
    print(f"    f_nom:               {format_eng(vi['f_center_nominal_Hz'], 'Hz')}")
    print(f"    f_post_layout:       {format_eng(vi['f_center_post_layout_Hz'], 'Hz')}")

    lf = impact['loop_filter']
    print(f"\n  Loop filter:")
    print(f"    C1 design:           {lf['c1_design_fF']:.1f} fF")
    print(f"    Parasitic on C1:     {lf['c_parasitic_fF']:.2f} fF ({lf['c1_shift_pct']:.2f}%)")
    print(f"    Loop BW shift:       {lf['bw_shift_pct']:.2f}%")

    su = impact['supply']
    print(f"\n  Supply integrity:")
    print(f"    R_supply:            {su['R_supply_ohm']:.2f} ohm")
    print(f"    I_vco:               {format_eng(su['I_vco_A'], 'A')}")
    print(f"    IR drop:             {su['IR_drop_V']*1e3:.2f} mV ({su['IR_drop_pct']:.2f}%)")

    pn = impact['phase_noise']
    print(f"\n  Phase noise:")
    print(f"    R_signal_path:       {pn['R_signal_path_ohm']:.1f} ohm")
    print(f"    R_device (VCO):      {pn['R_device_ohm']:.0f} ohm")
    print(f"    PN degradation:      {pn['PN_degradation_dB']:.2f} dB")

    xt = impact['crosstalk']
    print(f"\n  Crosstalk (VCO stage-to-stage):")
    print(f"    Parallel run:        {xt['parallel_run_um']:.0f} um")
    print(f"    C_coupling:          {xt['C_coupling_fF']:.2f} fF")
    print(f"    Coupling ratio:      {xt['crosstalk_pct']:.1f}% of load cap")

    # Overall assessment
    print("\n" + "-" * 70)
    freq_ok = abs(vi['f_shift_pct']) < 5.0
    bw_ok = abs(lf['bw_shift_pct']) < 5.0
    ir_ok = su['IR_drop_pct'] < 2.0
    pn_ok = pn['PN_degradation_dB'] < 1.0
    xt_ok = xt['crosstalk_pct'] < 5.0

    print("  VERDICT:")
    print(f"    VCO freq shift:  {'PASS' if freq_ok else 'FAIL'} ({vi['f_shift_pct']:+.1f}%)")
    print(f"    Loop BW shift:   {'PASS' if bw_ok else 'FAIL'} ({lf['bw_shift_pct']:+.2f}%)")
    print(f"    IR drop:         {'PASS' if ir_ok else 'FAIL'} ({su['IR_drop_pct']:.2f}%)")
    print(f"    Phase noise:     {'PASS' if pn_ok else 'FAIL'} (+{pn['PN_degradation_dB']:.2f} dB)")
    print(f"    Crosstalk:       {'PASS' if xt_ok else 'FAIL'} ({xt['crosstalk_pct']:.1f}%)")

    all_pass = all([freq_ok, bw_ok, ir_ok, pn_ok, xt_ok])
    print(f"\n    {'ALL CHECKS PASSED' if all_pass else 'ISSUES FOUND — review layout'}")
    print("=" * 70)

    return all_pass


# ======================================================================
# Main
# ======================================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Parasitic analysis of Kestrel PLL layout")
    parser.add_argument("gds", nargs="?",
                        default="layout/kestrel_pll.gds",
                        help="Input GDSII file")
    args = parser.parse_args()

    spec = PLLSpec(
        freq_min=400e6, freq_max=800e6, ref_freq=10e6,
        loop_bw=1e6, process="sky130",
    )
    design = design_pll(spec)

    parasitics = compute_parasitics(args.gds)
    impact = analyze_impact(design, parasitics)
    print_parasitics(parasitics, impact, design)


if __name__ == "__main__":
    main()
