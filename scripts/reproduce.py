#!/usr/bin/env python3
"""Run the paper calculation end to end or one stage at a time.

The default command starts from the released three-month checkpoints and
regenerates Figures 2--6.  ``--full`` explicitly enables the source-data path,
including acquisition, minute preparation, and Figure 1.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = REPOSITORY_ROOT / "data" / "config" / "paper.toml"


def _run(script: str, *arguments: object) -> None:
    command = [sys.executable, str(REPOSITORY_ROOT / "scripts" / script)]
    command.extend(str(argument) for argument in arguments if argument is not None)
    print("\n+", " ".join(command), flush=True)
    subprocess.run(command, cwd=REPOSITORY_ROOT, check=True)


def _common_option(flag: str, value: object | None) -> list[object]:
    return [flag, value] if value is not None else []


def run_acquisition(args: argparse.Namespace) -> None:
    options: list[object] = ["all", "--config", args.config]
    options += _common_option("--start", args.start)
    options += _common_option("--end", args.end)
    options += _common_option("--missions", args.missions)
    if args.force:
        options.append("--force")
    _run("acquire_data.py", *options)


def run_preparation(args: argparse.Namespace, *, source_data: bool) -> None:
    common: list[object] = ["--config", args.config]
    common += _common_option("--missions", args.missions)

    if source_data:
        minute_options = ["minute", *common]
        if args.force:
            minute_options.append("--force")
        _run("prepare_data.py", *minute_options)

        # The repository includes released quarterly files.  Once minute
        # checkpoints exist, the source-data path must deliberately replace
        # those files so later stages consume the newly prepared values.
        _run("prepare_data.py", "quarterly", *common, "--force")
        return

    # The released quarterly inputs are the authoritative lightweight
    # checkpoint.  Verification-only mode cannot cross into minute preparation
    # when one of those tracked inputs is absent.
    _run("prepare_data.py", "all", *common, "--released-only")


def run_analysis(args: argparse.Namespace) -> None:
    options: list[object] = ["all", "--config", args.config]
    if args.force:
        options.append("--force")
    _run("analyze.py", *options)


def run_figures(args: argparse.Namespace, *, include_figure_1: bool) -> None:
    if args.figure is not None:
        figures = (args.figure,)
    elif include_figure_1:
        figures = tuple(range(1, 7))
    else:
        figures = tuple(range(2, 7))

    for number in figures:
        _run(
            "make_figures.py",
            number,
            "--config",
            args.config,
            "--dpi",
            args.dpi,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage",
        choices=("acquire", "prepare", "analyze", "figures"),
        help="run only one stage; omission runs the lightweight or full chain",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="enable acquisition, minute preparation, and Figure 1",
    )
    parser.add_argument(
        "--figure",
        type=int,
        choices=range(1, 7),
        metavar="N",
        help="generate one figure (use with --stage figures)",
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--start", help="bounded acquisition start (YYYY-MM-DD)")
    parser.add_argument("--end", help="bounded acquisition end (YYYY-MM-DD)")
    parser.add_argument(
        "--missions",
        help="optional mission list/ranges, for example 6-8,10,12",
    )
    parser.add_argument("--dpi", type=int, default=600, help="figure resolution")
    parser.add_argument("--force", action="store_true", help="replace requested checkpoints")
    return parser


def _validate(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if (args.start is None) != (args.end is None):
        parser.error("--start and --end must be supplied together")
    if (args.start is not None or args.missions is not None) and args.stage not in {
        "acquire",
        "prepare",
        None,
    }:
        parser.error("--start/--end and --missions apply only to acquisition/preparation")
    if args.start is not None and args.stage not in {"acquire", None}:
        parser.error("--start/--end apply only to acquisition")
    if args.start is not None and args.stage is None and not args.full:
        parser.error("--start/--end require --stage acquire or --full")
    if args.missions is not None and args.stage is None and not args.full:
        parser.error("--missions requires an explicit stage or --full")
    if args.stage is None and args.full and (
        args.start is not None or args.missions is not None
    ):
        parser.error(
            "the complete --full chain uses every paper mission and the full "
            "date ranges; use --stage acquire for a bounded download check"
        )
    if args.figure is not None and args.stage != "figures":
        parser.error("--figure requires --stage figures")
    if args.stage == "acquire" and args.figure is not None:
        parser.error("the acquisition stage cannot generate a figure")
    if args.dpi < 1:
        parser.error("--dpi must be positive")
    if args.stage == "prepare" and args.force and not args.full:
        parser.error("forcing raw-data preparation requires --full")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _validate(parser, args)

    if args.stage == "acquire":
        run_acquisition(args)
    elif args.stage == "prepare":
        run_preparation(args, source_data=args.full)
    elif args.stage == "analyze":
        run_analysis(args)
    elif args.stage == "figures":
        run_figures(args, include_figure_1=args.full)
    elif args.full:
        run_acquisition(args)
        run_preparation(args, source_data=True)
        run_analysis(args)
        run_figures(args, include_figure_1=True)
    else:
        run_preparation(args, source_data=False)
        run_analysis(args)
        run_figures(args, include_figure_1=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
