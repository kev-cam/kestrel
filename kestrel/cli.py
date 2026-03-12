"""Command-line interface for Kestrel PLL Generator."""

import argparse
import sys

from .design.engine import PLLSpec, design_pll, summarize
from .models.verilog_ams import emit_verilog_ams
from .models.spice import emit_spice
from .models.behavioral import emit_behavioral, emit_kicad_sch


def main():
    parser = argparse.ArgumentParser(
        prog="kestrel",
        description="Kestrel — Open-Source PLL Generator",
    )
    sub = parser.add_subparsers(dest="command")

    # --- gui ---
    sub.add_parser("gui", help="Launch the spec entry GUI")

    # --- gen ---
    gen = sub.add_parser("gen", help="Generate PLL from command-line specs")
    gen.add_argument("--freq-min", required=True, help="Minimum output freq (e.g. 800M)")
    gen.add_argument("--freq-max", required=True, help="Maximum output freq (e.g. 1.2G)")
    gen.add_argument("--ref-freq", required=True, help="Reference clock freq (e.g. 25M)")
    gen.add_argument("--loop-bw", required=True, help="Loop bandwidth (e.g. 5M)")
    gen.add_argument("--phase-margin", default="60", help="Phase margin in degrees (default 60)")
    gen.add_argument("--jitter", default=None, help="Jitter target (e.g. 5p)")
    gen.add_argument("--vdd", default="1.8", help="Supply voltage (default 1.8)")
    gen.add_argument("--vco-type", default="ring", choices=["ring", "lc"])
    gen.add_argument("--vco-stages", default=4, type=int)
    gen.add_argument("--process", default="sky130", choices=["sky130", "gf180"])
    gen.add_argument("--output", "-o", default="./kestrel_out", help="Output directory")

    args = parser.parse_args()

    if args.command == "gui":
        from .gui import main as gui_main
        gui_main()

    elif args.command == "gen":
        from .spec import parse_freq, parse_time
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
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
