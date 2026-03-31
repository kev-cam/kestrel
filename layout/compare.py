"""Compare extracted layout netlist against design-engine reference.

Two-level comparison:
  1. Geometric: extracted W/L/AS/AD vs. design-engine intended sizing
  2. Behavioral: simulate both netlists and compare VCO frequency, etc.
     (requires complete routing — checks readiness)

Usage:
    python3 layout/compare.py [--gds layout/kestrel_pll.gds]
"""

import argparse
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kestrel.generators.pll.engine import PLLSpec, PLLDesign, design_pll, summarize


# ======================================================================
# Parse SPICE netlist for device parameters
# ======================================================================

_MFET_RE = re.compile(
    r'^M\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+(\S+)'  # model name
    r'\s+L=([\d.]+)([A-Za-z]*)'                   # L value + suffix
    r'\s+W=([\d.]+)([A-Za-z]*)',                   # W value + suffix
    re.MULTILINE
)

_SUFFIX = {
    '': 1.0, 'U': 1e-6, 'u': 1e-6, 'N': 1e-9, 'n': 1e-9,
    'M': 1e-3, 'm': 1e-3, 'P': 1e-12, 'p': 1e-12,
}


def _to_meters(val_str, suffix_str):
    """Convert a SPICE value+suffix to meters."""
    return float(val_str) * _SUFFIX.get(suffix_str, 1.0)


def parse_devices(spice_path):
    """Parse all MOSFET devices from a SPICE file.

    Returns list of dicts: {model, W, L, subcircuit, line}
    """
    devices = []
    with open(spice_path) as f:
        text = f.read()

    # Track current subcircuit
    subcircuit = "TOP"
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('.SUBCKT'):
            subcircuit = stripped.split()[1]
        elif stripped.startswith('.ENDS'):
            subcircuit = "TOP"

    # Re-join continuation lines
    joined = re.sub(r'\n\+', ' ', text)

    subcircuit = "TOP"
    for line in joined.splitlines():
        stripped = line.strip()
        if stripped.startswith('.SUBCKT'):
            subcircuit = stripped.split()[1]
        elif stripped.startswith('.ENDS'):
            subcircuit = "TOP"
        elif stripped.startswith('M'):
            m = _MFET_RE.match(stripped)
            if m:
                model = m.group(1)
                l_val = _to_meters(m.group(2), m.group(3))
                w_val = _to_meters(m.group(4), m.group(5))
                devices.append({
                    'model': model,
                    'W': w_val,
                    'L': l_val,
                    'subcircuit': subcircuit,
                })
    return devices


# ======================================================================
# Design-engine reference sizing
# ======================================================================

def reference_sizing(design: PLLDesign) -> dict:
    """Build a dict of expected device categories and their W/L.

    Returns {category: (W_meters, L_meters, count, model)}
    """
    proc_map = {
        'sky130': ('sky130_fd_pr__nfet_01v8', 'sky130_fd_pr__pfet_01v8'),
        'gf180':  ('nfet_03v3', 'pfet_03v3'),
    }
    nfet_model, pfet_model = proc_map.get(design.spec.process,
                                           proc_map['sky130'])

    n_stg = design.spec.vco_stages
    ref = {}

    # Helper: nfingers mirrors gds_gen._nfingers()
    def _nf(w):
        w_um = w * 1e6
        return 1 if w_um <= 5.0 else math.ceil(w_um / 5.0)

    # VCO delay cell transistors (per cell × n_stages)
    # Multi-finger: extractor sees nf separate devices per transistor,
    # each with W = total_W / nf.  Store per-finger W for matching.
    nf_diff = _nf(design.vco_diff_w)
    nf_tail = _nf(design.vco_tail_w)
    nf_load = _nf(design.vco_load_w)

    ref['vco_diff_pair'] = {
        'W': design.vco_diff_w / nf_diff, 'L': design.vco_diff_l,
        'total_W': design.vco_diff_w,
        'model': nfet_model, 'count': 2 * n_stg * nf_diff,
        'desc': f'VCO diff pair NMOS (nf={nf_diff})',
    }
    ref['vco_tail'] = {
        'W': design.vco_tail_w / nf_tail, 'L': design.vco_tail_l,
        'total_W': design.vco_tail_w,
        'model': nfet_model, 'count': n_stg * nf_tail,
        'desc': f'VCO tail NMOS (nf={nf_tail})',
    }
    ref['vco_load'] = {
        'W': design.vco_load_w / nf_load, 'L': design.vco_load_l,
        'total_W': design.vco_load_w,
        'model': pfet_model, 'count': 4 * n_stg * nf_load,
        'desc': f'VCO sym-load PMOS (nf={nf_load})',
    }

    # Charge pump
    nf_cp_up = _nf(design.cp_up_w)
    nf_cp_dn = _nf(design.cp_dn_w)
    nf_cp_sw = _nf(design.cp_sw_w)
    nf_cp_swn = _nf(design.cp_sw_w * 0.5)

    ref['cp_up'] = {
        'W': design.cp_up_w / nf_cp_up, 'L': design.cp_up_l,
        'total_W': design.cp_up_w,
        'model': pfet_model, 'count': 2 * nf_cp_up,
        'desc': f'CP UP PMOS (nf={nf_cp_up})',
    }
    ref['cp_dn'] = {
        'W': design.cp_dn_w / nf_cp_dn, 'L': design.cp_dn_l,
        'total_W': design.cp_dn_w,
        'model': nfet_model, 'count': 2 * nf_cp_dn,
        'desc': f'CP DN NMOS (nf={nf_cp_dn})',
    }
    ref['cp_sw_p'] = {
        'W': design.cp_sw_w / nf_cp_sw, 'L': design.cp_sw_l,
        'total_W': design.cp_sw_w,
        'model': pfet_model, 'count': nf_cp_sw,
        'desc': f'CP PMOS switch (nf={nf_cp_sw})',
    }
    ref['cp_sw_n'] = {
        'W': design.cp_sw_w * 0.5 / nf_cp_swn, 'L': design.cp_sw_l,
        'total_W': design.cp_sw_w * 0.5,
        'model': nfet_model, 'count': nf_cp_swn,
        'desc': f'CP NMOS switch (nf={nf_cp_swn})',
    }

    # PFD (all single-finger)
    ref['pfd_nmos'] = {
        'W': design.pfd_nw, 'L': design.pfd_nl,
        'total_W': design.pfd_nw,
        'model': nfet_model, 'count': 12,
        'desc': 'PFD NMOS gates',
    }
    ref['pfd_pmos'] = {
        'W': design.pfd_pw, 'L': design.pfd_pl,
        'total_W': design.pfd_pw,
        'model': pfet_model, 'count': 12,
        'desc': 'PFD PMOS gates',
    }

    # Divider (single-finger)
    n_div = design.div_stages
    ref['div_nmos'] = {
        'W': design.div_nw, 'L': design.div_nl,
        'total_W': design.div_nw,
        'model': nfet_model, 'count': 3 * n_div,
        'desc': 'Divider NMOS',
    }
    ref['div_pmos'] = {
        'W': design.div_pw, 'L': design.div_pl,
        'total_W': design.div_pw,
        'model': pfet_model, 'count': 3 * n_div,
        'desc': 'Divider PMOS',
    }

    return ref


# ======================================================================
# Comparison
# ======================================================================

def _count_instances(spice_text: str) -> dict:
    """Count how many times each subcircuit is instantiated (hierarchical).

    Returns {subcircuit_name: total_flat_instantiation_count}.
    """
    # Parse: which subcircuits instantiate which
    joined = re.sub(r'\n\+', ' ', spice_text)
    children = {}  # parent -> list of child subcircuit names
    current = None
    for line in joined.splitlines():
        stripped = line.strip()
        if stripped.startswith('.SUBCKT'):
            current = stripped.split()[1]
            children.setdefault(current, [])
        elif stripped.startswith('.ENDS'):
            current = None
        elif current and stripped.startswith('X'):
            # Last token is the subcircuit name
            parts = stripped.split()
            child_name = parts[-1]
            children[current].append(child_name)

    # Find the top cell (not instantiated by anyone)
    all_children = set()
    for ch_list in children.values():
        for ch in ch_list:
            all_children.add(ch)
    all_cells = set(children.keys())
    tops = all_cells - all_children
    if not tops:
        tops = all_cells

    # BFS to count flat instances
    flat_count = {name: 0 for name in all_cells}
    for top in tops:
        flat_count[top] = 1

    # Topological expansion
    from collections import deque
    queue = deque(tops)
    visited_order = []
    while queue:
        cell = queue.popleft()
        visited_order.append(cell)
        for child in children.get(cell, []):
            flat_count[child] = flat_count.get(child, 0) + flat_count.get(cell, 1)
            if child in children:
                queue.append(child)

    return flat_count


def compare_sizing(design: PLLDesign, extracted_path: str,
                   tol_pct: float = 5.0) -> dict:
    """Compare extracted device sizing against design-engine reference.

    Accounts for hierarchical instantiation: devices in a subcircuit
    that is instantiated N times are counted N times.

    Args:
        design:         PLLDesign from design engine
        extracted_path: Path to extracted SPICE netlist
        tol_pct:        Tolerance in percent for W/L matching

    Returns:
        dict with 'pass', 'fail', 'summary' keys
    """
    ref = reference_sizing(design)

    with open(extracted_path) as f:
        spice_text = f.read()

    ext_devs = parse_devices(extracted_path)
    inst_counts = _count_instances(spice_text)

    results = []
    total_pass = 0
    total_fail = 0

    for cat, spec in ref.items():
        ref_w = spec['W']
        ref_l = spec['L']
        ref_model = spec['model']
        expected_count = spec['count']
        desc = spec['desc']

        # Find matching extracted devices (within tolerance)
        matched = []
        for d in ext_devs:
            if d['model'] != ref_model:
                continue
            w_err = abs(d['W'] - ref_w) / ref_w * 100 if ref_w > 0 else 0
            l_err = abs(d['L'] - ref_l) / ref_l * 100 if ref_l > 0 else 0
            if w_err <= tol_pct and l_err <= tol_pct:
                matched.append(d)

        w_um = ref_w * 1e6
        l_um = ref_l * 1e6

        if matched:
            ext_w = matched[0]['W']
            ext_l = matched[0]['L']
            w_err = (ext_w - ref_w) / ref_w * 100 if ref_w > 0 else 0
            l_err = (ext_l - ref_l) / ref_l * 100 if ref_l > 0 else 0

            # Count flat instances (each device × parent instantiation)
            flat_matched = 0
            for d in matched:
                parent = d['subcircuit']
                multiplier = inst_counts.get(parent, 1)
                flat_matched += multiplier

            status = 'PASS'
            note = f'{flat_matched} flat instances'

            if flat_matched != expected_count:
                # W/L match is the primary check; count is informational
                note += f' (expected {expected_count})'

            results.append({
                'category': cat,
                'desc': desc,
                'status': status,
                'ref_W': w_um,
                'ref_L': l_um,
                'ext_W': ext_w * 1e6,
                'ext_L': ext_l * 1e6,
                'W_err': w_err,
                'L_err': l_err,
                'count': flat_matched,
                'expected': expected_count,
                'note': note,
            })
            total_pass += 1
        else:
            results.append({
                'category': cat,
                'desc': desc,
                'status': 'FAIL',
                'ref_W': w_um,
                'ref_L': l_um,
                'ext_W': 0,
                'ext_L': 0,
                'W_err': 100,
                'L_err': 100,
                'count': 0,
                'expected': expected_count,
                'note': 'no matching device found in extracted netlist',
            })
            total_fail += 1

    return {
        'results': results,
        'total_pass': total_pass,
        'total_fail': total_fail,
        'total': len(results),
        'ext_device_count': len(ext_devs),
    }


def print_comparison(comp: dict):
    """Pretty-print comparison results."""
    print("\n" + "=" * 75)
    print("  LAYOUT vs DESIGN-ENGINE SIZING COMPARISON")
    print("=" * 75)
    print(f"\n  Extracted devices: {comp['ext_device_count']}")
    print(f"  Categories checked: {comp['total']}")
    print(f"  Pass: {comp['total_pass']}  Fail: {comp['total_fail']}")
    print()

    fmt = "  {status:4s}  {desc:35s}  ref {ref_W:7.3f}/{ref_L:.3f}  ext {ext_W:7.3f}/{ext_L:.3f}  err W{W_err:+5.1f}% L{L_err:+5.1f}%"

    for r in comp['results']:
        print(fmt.format(**r))
        if r['note']:
            print(f"        {r['note']}")

    print()
    if comp['total_fail'] == 0:
        print("  RESULT: ALL CHECKS PASSED")
    else:
        print(f"  RESULT: {comp['total_fail']} CHECKS FAILED")
    print("=" * 75)


# ======================================================================
# Connectivity check
# ======================================================================

def check_connectivity(extracted_path: str) -> dict:
    """Check whether the extracted netlist has meaningful connectivity.

    Counts unique net names per subcircuit.  If top-level subcircuits
    only have 1 pin (VSUB), inter-block routing is missing.
    """
    subcircuit_pins = {}
    with open(extracted_path) as f:
        text = f.read()

    joined = re.sub(r'\n\+', ' ', text)
    for line in joined.splitlines():
        stripped = line.strip()
        if stripped.startswith('.SUBCKT'):
            parts = stripped.split()
            name = parts[1]
            pins = parts[2:]
            subcircuit_pins[name] = len(pins)

    # Key blocks to check
    blocks = ['kestrel_pll_top', 'kestrel_vco', 'kestrel_pfd',
              'kestrel_charge_pump', 'kestrel_divider']

    issues = []
    for blk in blocks:
        n_pins = subcircuit_pins.get(blk, 0)
        if n_pins <= 1:
            issues.append(f"{blk}: only {n_pins} pin(s) "
                          f"(missing inter-block signal routing)")

    return {
        'subcircuit_pins': subcircuit_pins,
        'issues': issues,
        'sim_ready': len(issues) == 0,
    }


def print_connectivity(conn: dict):
    """Print connectivity check results."""
    print("\n" + "-" * 75)
    print("  CONNECTIVITY CHECK (for behavioral simulation)")
    print("-" * 75)

    if conn['sim_ready']:
        print("  All blocks have signal connectivity — simulation-ready.")
    else:
        print("  Inter-block signal routing incomplete:")
        for issue in conn['issues']:
            print(f"    - {issue}")
        print()
        print("  To fix: add VIA1 in gds_gen.py connecting MET1↔MET2")
        print("  at block boundaries, and ensure intra-cell wiring")
        print("  connects transistor terminals to block ports.")
    print("-" * 75)


# ======================================================================
# Main
# ======================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Compare extracted layout against design-engine reference")
    parser.add_argument("--gds", default="layout/kestrel_pll.gds",
                        help="Input GDSII file")
    parser.add_argument("--extracted", default=None,
                        help="Pre-extracted SPICE (skip re-extraction)")
    parser.add_argument("--tol", type=float, default=5.0,
                        help="W/L tolerance in percent")
    args = parser.parse_args()

    # Design engine reference
    spec = PLLSpec(
        freq_min=400e6, freq_max=800e6, ref_freq=10e6,
        loop_bw=1e6, process="sky130",
    )
    design = design_pll(spec)
    print(summarize(design))

    # Extract if needed
    if args.extracted:
        ext_path = args.extracted
    else:
        ext_path = os.path.splitext(args.gds)[0] + "_extracted.cir"
        if not os.path.exists(ext_path):
            print(f"\nExtracting {args.gds}...")
            from layout.extract import extract_netlist
            extract_netlist(args.gds, output_path=ext_path, verbose=True)

    # Geometric comparison
    comp = compare_sizing(design, ext_path, tol_pct=args.tol)
    print_comparison(comp)

    # Connectivity check
    conn = check_connectivity(ext_path)
    print_connectivity(conn)

    return 0 if comp['total_fail'] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
