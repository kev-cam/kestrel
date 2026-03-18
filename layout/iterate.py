"""Iterative design→layout→extract→compare loop for Kestrel PLL.

Closes the loop: the parasitic capacitance measured from the layout
feeds back into the design engine, which re-sizes the VCO to
compensate. Repeats until the post-layout frequency shift is within
tolerance.

Usage:
    python3 layout/iterate.py [--max-iter 5] [--tol 5.0]
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kestrel.design.engine import PLLSpec, design_pll, summarize, format_eng
from layout.gds_gen import generate_pll_gds
from layout.parasitics import compute_parasitics, analyze_impact


def iterate(max_iter: int = 5, tol_pct: float = 5.0, verbose: bool = True):
    """Run the design→layout→extract→compare loop.

    Returns the final (design, parasitics, impact) tuple.
    """
    parasitic_cap = 0.0  # start with zero parasitic budget

    for iteration in range(1, max_iter + 1):
        if verbose:
            print(f"\n{'='*60}")
            print(f"  ITERATION {iteration}  (parasitic budget: "
                  f"{parasitic_cap*1e15:.1f} fF/node)")
            print(f"{'='*60}")

        # --- Design ---
        spec = PLLSpec(
            freq_min=400e6, freq_max=800e6, ref_freq=10e6,
            loop_bw=1e6, process="sky130",
            parasitic_cap=parasitic_cap,
        )
        design = design_pll(spec)

        if verbose:
            print(f"\n  Design: f_center={format_eng(design.f_center, 'Hz')}, "
                  f"I_stage={format_eng(design.vco_i_stage, 'A')}, "
                  f"C_load={format_eng((20e-15 + parasitic_cap), 'F')}")
            print(f"  VCO sizing: diff_W={design.vco_diff_w*1e6:.3f}u, "
                  f"tail_W={design.vco_tail_w*1e6:.3f}u, "
                  f"load_W={design.vco_load_w*1e6:.3f}u")

        # --- Layout ---
        # Clear gdsfactory cell cache to allow regeneration
        import gdsfactory as gf
        gf.clear_cache()

        gds_path = f"layout/kestrel_pll_iter{iteration}.gds"
        generate_pll_gds(design, gds_path)
        if verbose:
            print(f"  Layout: {gds_path}")

        # --- Parasitic extraction ---
        parasitics = compute_parasitics(gds_path)
        impact = analyze_impact(design, parasitics)

        f_shift = impact['vco']['f_shift_pct']
        c_parasitic = impact['vco']['c_parasitic_per_node_fF']
        xt = impact['crosstalk']['crosstalk_pct']

        if verbose:
            print(f"\n  Parasitics: C_parasitic/node={c_parasitic:.2f} fF, "
                  f"total_wire_C={parasitics['total_wire_C_fF']:.1f} fF")
            print(f"  Impact: VCO freq shift={f_shift:+.1f}%, "
                  f"crosstalk={xt:.1f}%")
            print(f"  Post-layout f_center="
                  f"{format_eng(impact['vco']['f_center_post_layout_Hz'], 'Hz')}")

        # --- Check convergence ---
        if abs(f_shift) <= tol_pct:
            if verbose:
                print(f"\n  CONVERGED: frequency shift {f_shift:+.1f}% "
                      f"within {tol_pct}% tolerance")
            # Copy final GDS to canonical location
            os.replace(gds_path, "layout/kestrel_pll.gds")
            return design, parasitics, impact

        # --- Update parasitic budget for next iteration ---
        # Feed the measured parasitic cap back into the design engine
        parasitic_cap = c_parasitic * 1e-15  # fF → F
        if verbose:
            print(f"  → Updating parasitic budget to {c_parasitic:.2f} fF "
                  f"for next iteration")

    if verbose:
        print(f"\n  DID NOT CONVERGE after {max_iter} iterations "
              f"(last shift: {f_shift:+.1f}%)")

    os.replace(gds_path, "layout/kestrel_pll.gds")
    return design, parasitics, impact


def main():
    parser = argparse.ArgumentParser(
        description="Iterative PLL design→layout→extract loop")
    parser.add_argument("--max-iter", type=int, default=5,
                        help="Maximum iterations")
    parser.add_argument("--tol", type=float, default=5.0,
                        help="Frequency shift tolerance (%%)")
    args = parser.parse_args()

    design, parasitics, impact = iterate(
        max_iter=args.max_iter, tol_pct=args.tol)

    # Final summary
    print("\n" + "=" * 60)
    print("  FINAL DESIGN")
    print("=" * 60)
    print(summarize(design))

    from layout.parasitics import print_parasitics
    print_parasitics(parasitics, impact, design)


if __name__ == "__main__":
    main()
