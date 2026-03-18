"""KLayout netlist extraction for Kestrel PLL — sky130 process.

Uses KLayout's LayoutToNetlist engine to extract MOSFET devices from
the GDSII layout and produce a SPICE netlist.  This is the layout-side
half of LVS: it identifies transistors (W, L, AS, AD, PS, PD) and
connectivity from the physical geometry.

Usage:
    python3 layout/extract.py layout/kestrel_pll.gds [--output extracted.cir]

The extraction recognizes:
    - NFET: POLY ∩ DIFF outside NWELL, with NSDM
    - PFET: POLY ∩ DIFF inside NWELL, with PSDM
    - Connectivity: DIFF→LICON→LI→MCON→MET1→VIA1→MET2
"""

import argparse
import os
import sys

import klayout.db as kdb


# ======================================================================
# Sky130 layer map (must match gds_gen.py)
# ======================================================================

LAYER_MAP = {
    "diff":   (65, 20),
    "tap":    (65, 44),
    "nwell":  (64, 20),
    "poly":   (66, 20),
    "nsdm":   (93, 44),
    "psdm":   (94, 20),
    "licon":  (66, 44),
    "li":     (67, 20),
    "mcon":   (67, 44),
    "met1":   (68, 20),
    "via1":   (68, 44),
    "met2":   (69, 20),
    "via2":   (69, 44),
    "met3":   (70, 20),
}


def extract_netlist(gds_path: str, top_cell: str = None,
                    output_path: str = None,
                    verbose: bool = False) -> kdb.Netlist:
    """Extract SPICE netlist from a sky130 GDS layout.

    Args:
        gds_path:    Path to input GDSII file.
        top_cell:    Top cell name (auto-detected if None).
        output_path: Path for extracted SPICE netlist (optional).
        verbose:     Print progress messages.

    Returns:
        kdb.Netlist object with extracted devices and connectivity.
    """
    # --- Load layout ---
    layout = kdb.Layout()
    layout.read(gds_path)

    if top_cell:
        tc = layout.cell(top_cell)
        if tc is None:
            raise ValueError(f"Cell '{top_cell}' not found in {gds_path}")
    else:
        tops = layout.top_cells()
        if not tops:
            raise ValueError(f"No top cells in {gds_path}")
        tc = tops[0]
        if verbose:
            print(f"Auto-detected top cell: {tc.name}")

    dbu = layout.dbu  # database unit in um

    # --- Create extractor ---
    l2n = kdb.LayoutToNetlist(kdb.RecursiveShapeIterator(layout, tc, []))
    # device_scaling left at default — SPICE writer handles dbu→um

    # --- Register layers ---
    layers = {}
    for name, (layer_num, datatype) in LAYER_MAP.items():
        li = layout.find_layer(layer_num, datatype)
        if li is not None:
            layers[name] = l2n.make_layer(li, name)
            if verbose:
                print(f"  Layer {name} ({layer_num}/{datatype}): found")
        else:
            # Create empty layer so Boolean ops don't crash
            layers[name] = l2n.make_layer(name)
            if verbose:
                print(f"  Layer {name} ({layer_num}/{datatype}): empty")

    # --- Derived layers for device recognition ---
    # Gate region: poly overlapping diffusion
    gate = layers["poly"] & layers["diff"]

    # Source/drain: diffusion minus poly
    sd = layers["diff"] - layers["poly"]

    # NFET S/D: outside nwell, with NSDM implant
    nsd = sd & layers["nsdm"]
    nsd = nsd - layers["nwell"]
    # PFET S/D: inside nwell, with PSDM implant
    psd = sd & layers["psdm"]
    psd = psd & layers["nwell"]

    # Gate split by well for separate NFET/PFET recognition
    ngate = gate - layers["nwell"]
    pgate = gate & layers["nwell"]

    # --- Extract devices ---
    # MOS3Transistor: terminals G, S, D (no body).
    # Layer map: "SD" = source/drain, "G" = gate, "P" = S/D marker
    nfet_ext = kdb.DeviceExtractorMOS3Transistor("sky130_fd_pr__nfet_01v8")
    l2n.extract_devices(nfet_ext, {
        "SD": nsd,
        "G": ngate,
        "P": ngate,     # gate oxide marker = gate region itself
    })

    pfet_ext = kdb.DeviceExtractorMOS3Transistor("sky130_fd_pr__pfet_01v8")
    l2n.extract_devices(pfet_ext, {
        "SD": psd,
        "G": pgate,
        "P": pgate,     # gate oxide marker = gate region itself
    })

    # --- Define connectivity ---
    # Conducting layers (self-connect)
    l2n.connect(layers["poly"])
    l2n.connect(layers["li"])
    l2n.connect(layers["met1"])
    l2n.connect(layers["met2"])
    if "met3" in layers:
        l2n.connect(layers["met3"])
    l2n.connect(layers["licon"])
    l2n.connect(layers["mcon"])

    # Device terminal layers (self-connect)
    l2n.connect(nsd)
    l2n.connect(psd)
    l2n.connect(ngate)
    l2n.connect(pgate)

    # Gate regions connect through poly to routing stack
    l2n.connect(ngate, layers["poly"])
    l2n.connect(pgate, layers["poly"])

    # S/D → contact stack
    l2n.connect(nsd,  layers["licon"])
    l2n.connect(psd,  layers["licon"])
    l2n.connect(layers["poly"],  layers["licon"])   # gate contacts
    l2n.connect(layers["licon"], layers["li"])
    l2n.connect(layers["li"],    layers["mcon"])
    l2n.connect(layers["mcon"],  layers["met1"])
    l2n.connect(layers["met1"],  layers["via1"])
    l2n.connect(layers["via1"],  layers["met2"])
    if "via2" in layers and "met3" in layers:
        l2n.connect(layers["met2"],  layers["via2"])
        l2n.connect(layers["via2"],  layers["met3"])

    # --- Run extraction ---
    if verbose:
        print("Extracting netlist...")

    l2n.extract_netlist()

    netlist = l2n.netlist()

    # --- Simplify ---
    netlist.combine_devices()
    netlist.purge()

    # --- Report ---
    if verbose:
        _print_summary(netlist)

    # --- Write SPICE ---
    if output_path:
        writer = kdb.NetlistSpiceWriter()
        netlist.write(output_path, writer)
        if verbose:
            print(f"\nExtracted netlist written: {output_path}")

    # --- Write L2N database for debugging ---
    l2n_path = os.path.splitext(output_path or gds_path)[0] + ".l2n"
    l2n.write_l2n(l2n_path)
    if verbose:
        print(f"L2N database written: {l2n_path}")

    return netlist


def _print_summary(netlist: kdb.Netlist):
    """Print extraction summary."""
    print("\n=== Extraction Summary ===")

    n_nfet = 0
    n_pfet = 0
    n_nets = 0
    n_circuits = 0

    for circuit in netlist.each_circuit():
        n_circuits += 1
        n_nets += len(list(circuit.each_net()))
        for dev in circuit.each_device():
            dc = dev.device_class()
            if dc is not None:
                if "nfet" in dc.name:
                    n_nfet += 1
                elif "pfet" in dc.name:
                    n_pfet += 1

    print(f"  Circuits:   {n_circuits}")
    print(f"  NFET count: {n_nfet}")
    print(f"  PFET count: {n_pfet}")
    print(f"  Total nets: {n_nets}")

    # Print per-circuit detail
    for circuit in netlist.each_circuit():
        devs = list(circuit.each_device())
        pins = list(circuit.each_pin())
        nets = list(circuit.each_net())
        if devs:
            print(f"\n  Circuit: {circuit.name}")
            print(f"    Devices: {len(devs)}  Pins: {len(pins)}  Nets: {len(nets)}")
            for dev in devs[:20]:  # limit output
                dc = dev.device_class()
                name = dc.name if dc else "?"
                try:
                    w = dev.parameter("W")  # um (layout dbu-scaled)
                    l = dev.parameter("L")
                    print(f"      {dev.name or '?'}: {name} W={w:.3f}u L={l:.3f}u")
                except Exception:
                    print(f"      {dev.name or '?'}: {name}")
            if len(devs) > 20:
                print(f"      ... and {len(devs) - 20} more")


# ======================================================================
# CLI
# ======================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Extract SPICE netlist from Kestrel PLL GDS (sky130)")
    parser.add_argument("gds", help="Input GDSII file")
    parser.add_argument("--output", "-o",
                        help="Output SPICE netlist path",
                        default=None)
    parser.add_argument("--top", "-t",
                        help="Top cell name (auto-detect if omitted)",
                        default=None)
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Print progress and summary")
    args = parser.parse_args()

    if args.output is None:
        args.output = os.path.splitext(args.gds)[0] + "_extracted.cir"

    extract_netlist(args.gds, top_cell=args.top,
                    output_path=args.output, verbose=True)


if __name__ == "__main__":
    main()
