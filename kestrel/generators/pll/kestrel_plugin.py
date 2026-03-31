"""PLL generator plugin for Kestrel."""


def add_arguments(parser):
    """Add PLL-specific CLI arguments to a subparser."""
    parser.add_argument("--freq-min", required=True, help="Minimum output freq (e.g. 800M)")
    parser.add_argument("--freq-max", required=True, help="Maximum output freq (e.g. 1.2G)")
    parser.add_argument("--ref-freq", required=True, help="Reference clock freq (e.g. 25M)")
    parser.add_argument("--loop-bw", required=True, help="Loop bandwidth (e.g. 5M)")
    parser.add_argument("--phase-margin", default="60", help="Phase margin in degrees (default 60)")
    parser.add_argument("--jitter", default=None, help="Jitter target (e.g. 5p)")
    parser.add_argument("--vdd", default="1.8", help="Supply voltage (default 1.8)")
    parser.add_argument("--vco-type", default="ring", choices=["ring", "lc"])
    parser.add_argument("--vco-stages", default=4, type=int)
    parser.add_argument("--process", default="sky130", choices=["sky130", "gf180", "sg13g2"])
    parser.add_argument("--output", "-o", default="./kestrel_out", help="Output directory")


def run(args):
    """Execute PLL generation from parsed CLI args."""
    from kestrel.spec import parse_freq, parse_time
    from .engine import PLLSpec, design_pll, summarize
    from .models.verilog_ams import emit_verilog_ams
    from .models.spice import emit_spice
    from .models.behavioral import emit_behavioral, emit_kicad_sch

    jitter = parse_time(args.jitter) if args.jitter else None
    spec = PLLSpec(
        freq_min=parse_freq(args.freq_min),
        freq_max=parse_freq(args.freq_max),
        ref_freq=parse_freq(args.ref_freq),
        loop_bw=parse_freq(args.loop_bw),
        phase_margin=float(args.phase_margin),
        jitter_target=jitter,
        vco_type=args.vco_type,
        vco_stages=args.vco_stages,
        supply_voltage=float(args.vdd),
        process=args.process,
    )
    design = design_pll(spec)
    print(summarize(design))
    print()
    files = emit_verilog_ams(design, args.output)
    for f in files:
        print(f"  wrote {f}")
    print()
    files = emit_spice(design, args.output)
    for f in files:
        print(f"  wrote {f}")
    print()
    files = emit_behavioral(design, args.output)
    files += emit_kicad_sch(design, args.output)
    for f in files:
        print(f"  wrote {f}")


def gui_fields():
    """Return PLL GUI field definitions."""
    return {
        "entries": [
            {"label": "Freq min:",       "key": "freq_min",  "default": "800M",  "hint": "Hz (e.g. 800M, 1G, 500k)"},
            {"label": "Freq max:",       "key": "freq_max",  "default": "1.2G",  "hint": "Hz"},
            {"label": "Reference freq:", "key": "ref_freq",  "default": "25M",   "hint": "Hz"},
            {"label": "Loop bandwidth:", "key": "loop_bw",   "default": "5M",    "hint": "Hz"},
            {"label": "Phase margin:",   "key": "pm",        "default": "60",    "hint": "degrees"},
            {"label": "Jitter target:",  "key": "jitter",    "default": "5p",    "hint": "s rms (blank = unconstrained)"},
            {"label": "Supply voltage:", "key": "vdd",       "default": "1.8",   "hint": "V"},
        ],
        "combos": [
            {"label": "VCO type:",  "key": "vco_type",  "default": "ring",   "choices": ["ring", "lc"]},
            {"label": "Process:",   "key": "process",   "default": "sky130", "choices": ["sky130", "gf180", "sg13g2"]},
        ],
        "extras": [
            {"label": "VCO stages:", "key": "vco_stages", "default": "4", "hint": "(ring only)"},
        ],
    }


def register():
    """Register the PLL generator with Kestrel."""
    return {
        "name": "pll",
        "version": "0.1.0",
        "description": "Charge-pump PLL generator (Maneatis topology)",
        "add_arguments": add_arguments,
        "run": run,
        "gui_fields": gui_fields,
    }
