"""Command-line interface shared by every public reproduction script."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from .config import DEFAULT_CONFIG
from .pipeline import PipelineOptions, ReproductionPipeline


STAGES = ("all", "acquire", "prepare", "analyze", "figures", "figure", "status")

_STAGE_DESCRIPTIONS = {
    "all": "Run or resume the complete QBO reproduction chain.",
    "acquire": "Acquire GOES observations and orbital inputs.",
    "prepare": "Prepare minute-resolution and three-month GOES data.",
    "analyze": "Select, filter, and calculate the wavelet products.",
    "figures": "Regenerate the paper figures available for this run mode.",
    "figure": "Regenerate one paper figure.",
    "status": "Report available reproduction artifacts as JSON.",
}


def build_parser(
    *,
    fixed_stage: str | None = None,
    fixed_figure_number: int | None = None,
) -> argparse.ArgumentParser:
    """Build the general CLI or a role-specific wrapper CLI."""

    if fixed_stage is not None and fixed_stage not in STAGES:
        raise ValueError(f"unknown fixed stage: {fixed_stage}")
    if fixed_figure_number is not None and (
        fixed_stage != "figure" or fixed_figure_number not in range(1, 7)
    ):
        raise ValueError("a fixed figure from 1 through 6 requires stage 'figure'")

    description = (
        "Reproduce the 2023 GOES QBO-detection study."
        if fixed_stage is None
        else _STAGE_DESCRIPTIONS[fixed_stage]
    )
    parser = argparse.ArgumentParser(description=description)
    if fixed_stage is None:
        parser.add_argument("stage", nargs="?", choices=STAGES, default="all")
        parser.add_argument("figure_number", nargs="?", type=int, choices=range(1, 7))
    else:
        parser.set_defaults(stage=fixed_stage)
        if fixed_stage == "figure" and fixed_figure_number is None:
            parser.add_argument("figure_number", type=int, choices=range(1, 7))
        else:
            parser.set_defaults(figure_number=fixed_figure_number)

    parser.set_defaults(
        start=None,
        end=None,
        missions=None,
        source_dir=None,
        processed_dir=None,
        wavelet_dir=None,
        figures_dir=None,
        dpi=600,
        full=False,
        force=False,
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)

    general = fixed_stage is None
    if general or fixed_stage == "acquire":
        parser.add_argument("--start", help="bounded acquisition start (YYYY-MM-DD)")
        parser.add_argument("--end", help="bounded acquisition end (YYYY-MM-DD)")
    if general or fixed_stage in ("acquire", "prepare"):
        parser.add_argument("--missions", help="mission list/ranges, for example 6-8,10,12")
    if general or fixed_stage in ("acquire", "prepare", "analyze", "figures", "figure", "status"):
        parser.add_argument("--source-dir", type=Path)
    if general or fixed_stage in ("prepare", "analyze", "figures", "figure", "status"):
        parser.add_argument("--processed-dir", type=Path)
    if general or fixed_stage in ("analyze", "figures", "figure", "status"):
        parser.add_argument("--wavelet-dir", type=Path)
    if general or fixed_stage in ("figures", "figure", "status"):
        parser.add_argument("--figures-dir", type=Path)
    if general or fixed_stage in ("all", "figures", "figure"):
        parser.add_argument("--dpi", type=int, default=600, help="figure resolution")
    if general or fixed_stage in ("all", "prepare", "figures"):
        parser.add_argument(
            "--full",
            action="store_true",
            help="enable the source-data path and include Figure 1",
        )
    if general or fixed_stage in ("all", "acquire", "prepare", "analyze"):
        parser.add_argument(
            "--force",
            action="store_true",
            help="replace outputs of the selected calculation stage",
        )
    return parser


def _validate(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if (args.start is None) != (args.end is None):
        parser.error("--start and --end must be supplied together")
    if args.start is not None and args.stage != "acquire":
        parser.error("--start/--end apply only to the acquire stage")
    if args.missions is not None and args.stage not in {"acquire", "prepare"}:
        parser.error("--missions applies only to acquisition or preparation")
    if args.full and args.stage not in {"all", "prepare", "figures"}:
        parser.error("--full applies only to all, prepare, or figures")
    if args.force and args.stage not in {"all", "acquire", "prepare", "analyze"}:
        parser.error("--force does not apply to the selected stage")
    if args.stage == "prepare" and args.force and not args.full:
        parser.error("forcing raw-data preparation requires --full")
    if args.dpi < 1:
        parser.error("--dpi must be positive")
    if args.stage == "figure" and args.figure_number is None:
        parser.error("the figure stage requires a number from 1 through 6")
    if args.stage != "figure" and args.figure_number is not None:
        parser.error("a figure number is accepted only after the figure stage")


def _run(args: argparse.Namespace) -> int:
    pipeline = ReproductionPipeline(
        PipelineOptions(
            config=args.config,
            start=args.start,
            end=args.end,
            missions=args.missions,
            source_dir=args.source_dir,
            processed_dir=args.processed_dir,
            wavelet_dir=args.wavelet_dir,
            figures_dir=args.figures_dir,
            dpi=args.dpi,
            force=args.force,
        )
    )
    if args.stage == "all":
        pipeline.all(full=args.full)
    elif args.stage == "acquire":
        pipeline.acquire()
    elif args.stage == "prepare":
        pipeline.prepare(source_data=args.full)
    elif args.stage == "analyze":
        pipeline.analyze()
    elif args.stage == "figures":
        pipeline.render_figures(include_figure_1=args.full)
    elif args.stage == "figure":
        pipeline.figure(args.figure_number)
    elif args.stage == "status":
        pipeline.print_status()
    return 0


def _run_with_cli_boundary(args: argparse.Namespace) -> int:
    try:
        return _run(args)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _validate(parser, args)
    return _run_with_cli_boundary(args)


def main_for_stage(
    stage: str,
    argv: Sequence[str] | None = None,
    *,
    figure_number: int | None = None,
) -> int:
    parser = build_parser(fixed_stage=stage, fixed_figure_number=figure_number)
    args = parser.parse_args(argv)
    _validate(parser, args)
    return _run_with_cli_boundary(args)


__all__ = ["STAGES", "build_parser", "main", "main_for_stage"]
