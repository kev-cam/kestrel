"""Command-line interface for Kestrel circuit generators."""

import argparse
import sys

from .plugins import discover_generators


def main():
    generators = discover_generators()

    parser = argparse.ArgumentParser(
        prog="kestrel",
        description="Kestrel -- Open-Source Circuit Generator",
    )
    sub = parser.add_subparsers(dest="command")

    # --- gui ---
    sub.add_parser("gui", help="Launch the GUI")

    # --- Register each generator as a subcommand ---
    gen_runners = {}
    for name, reg in generators.items():
        gen_sub = sub.add_parser(name, help=reg.get("description", f"{name} generator"))
        gen_cmds = gen_sub.add_subparsers(dest="gen_command")
        gen_parser = gen_cmds.add_parser("gen", help=f"Generate {name} from specs")
        reg["add_arguments"](gen_parser)
        gen_runners[name] = reg["run"]

    args = parser.parse_args()

    if args.command == "gui":
        from .gui import main as gui_main
        gui_main()
    elif args.command in gen_runners:
        if getattr(args, 'gen_command', None) == "gen":
            gen_runners[args.command](args)
        else:
            parser.parse_args([args.command, "--help"])
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
