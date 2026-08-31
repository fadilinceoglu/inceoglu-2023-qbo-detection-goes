"""Causal orchestration for lightweight and full QBO reproduction runs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from .config import DEFAULT_CONFIG, resolve_repository_path


@dataclass(frozen=True)
class PipelineOptions:
    """User-selected inputs that affect stage dispatch."""

    config: Path = DEFAULT_CONFIG
    start: str | None = None
    end: str | None = None
    missions: str | None = None
    source_dir: Path | None = None
    processed_dir: Path | None = None
    wavelet_dir: Path | None = None
    figures_dir: Path | None = None
    dpi: int = 600
    force: bool = False


class ReproductionPipeline:
    """Run each stage directly while preserving the public checkpoint rules."""

    def __init__(self, options: PipelineOptions) -> None:
        self.options = options

    @staticmethod
    def _run_stage(
        name: str,
        entrypoint: Callable[[Sequence[str] | None], int],
        arguments: list[str],
    ) -> None:
        print(f"\n[{name}] {' '.join(arguments)}", flush=True)
        result = entrypoint(arguments)
        if result != 0:
            raise RuntimeError(f"{name} stage exited with status {result}")

    @staticmethod
    def _option(flag: str, value: object | None) -> list[str]:
        return [flag, str(value)] if value is not None else []

    def _path_option(self, flag: str, value: Path | None) -> list[str]:
        resolved = resolve_repository_path(value) if value is not None else None
        return self._option(flag, resolved)

    def acquire(self) -> None:
        from . import acquisition

        arguments = ["all", "--config", str(self.options.config)]
        arguments += self._option("--start", self.options.start)
        arguments += self._option("--end", self.options.end)
        arguments += self._option("--missions", self.options.missions)
        arguments += self._path_option("--source-dir", self.options.source_dir)
        if self.options.force:
            arguments.append("--force")
        self._run_stage("acquire", acquisition.main, arguments)

    def prepare(self, *, source_data: bool) -> None:
        from . import preparation

        common = ["--config", str(self.options.config)]
        common += self._option("--missions", self.options.missions)
        common += self._path_option("--source-dir", self.options.source_dir)
        common += self._path_option("--processed-dir", self.options.processed_dir)

        if source_data:
            minute_arguments = ["minute", *common]
            if self.options.force:
                minute_arguments.append("--force")
            self._run_stage("prepare minute data", preparation.main, minute_arguments)

            # Newly prepared minute data causally replace the released
            # quarterly checkpoint set before downstream analysis can run.
            self._run_stage(
                "prepare quarterly data",
                preparation.main,
                ["quarterly", *common, "--force"],
            )
            return

        # Lightweight reproduction verifies the released quarterly inputs and
        # can never fall through into minute-resolution preparation.
        self._run_stage(
            "verify released quarterly data",
            preparation.main,
            ["all", *common, "--released-only"],
        )

    def analyze(self, *, verify_released_sunspot: bool = True) -> None:
        from . import acquisition, analysis

        if verify_released_sunspot:
            settings = analysis.load_settings(self.options.config)
            source_dir = (
                resolve_repository_path(self.options.source_dir)
                if self.options.source_dir is not None
                else settings.source_dir
            )
            acquisition.verify_released_sunspot(source_dir)
        arguments = ["all", "--config", str(self.options.config)]
        arguments += self._path_option("--processed-dir", self.options.processed_dir)
        arguments += self._path_option("--source-dir", self.options.source_dir)
        arguments += self._path_option("--output-dir", self.options.wavelet_dir)
        if self.options.force:
            arguments.append("--force")
        self._run_stage("analyze", analysis.main, arguments)

    def figure(self, number: int) -> None:
        from . import figures

        if number not in range(1, 7):
            raise ValueError("figure number must be between 1 and 6")
        arguments = [
            str(number),
            "--config",
            str(self.options.config),
            "--dpi",
            str(self.options.dpi),
        ]
        arguments += self._path_option("--processed-dir", self.options.processed_dir)
        arguments += self._path_option("--source-dir", self.options.source_dir)
        arguments += self._path_option("--wavelet-dir", self.options.wavelet_dir)
        arguments += self._path_option("--figures-dir", self.options.figures_dir)
        self._run_stage(
            f"figure {number}",
            figures.main,
            arguments,
        )

    def render_figures(
        self,
        *,
        include_figure_1: bool,
        figure_number: int | None = None,
    ) -> None:
        if figure_number is not None:
            numbers = (figure_number,)
        elif include_figure_1:
            numbers = tuple(range(1, 7))
        else:
            numbers = tuple(range(2, 7))
        for number in numbers:
            self.figure(number)

    def all(self, *, full: bool) -> None:
        if full:
            self.acquire()
            self.prepare(source_data=True)
            self.analyze(verify_released_sunspot=False)
            self.render_figures(include_figure_1=True)
            return
        self.prepare(source_data=False)
        self.analyze(verify_released_sunspot=True)
        self.render_figures(include_figure_1=False)

    def status(self) -> dict[str, object]:
        """Describe current repository artifacts without creating any files."""

        from . import acquisition, analysis
        from .io import manifest_checksum, sha256_file

        settings = analysis.load_settings(self.options.config)
        processed_dir = (
            resolve_repository_path(self.options.processed_dir)
            if self.options.processed_dir is not None
            else settings.processed_dir
        )
        quarterly_dir = processed_dir / "quarterly"
        selected_dir = processed_dir / "selected"
        wavelet_dir = (
            resolve_repository_path(self.options.wavelet_dir)
            if self.options.wavelet_dir is not None
            else settings.work_dir / "wavelets"
        )
        figures_dir = (
            resolve_repository_path(self.options.figures_dir)
            if self.options.figures_dir is not None
            else settings.figures_dir
        )
        source_dir = (
            resolve_repository_path(self.options.source_dir)
            if self.options.source_dir is not None
            else settings.source_dir
        )
        try:
            acquisition.verify_released_sunspot(source_dir)
        except (FileNotFoundError, OSError, RuntimeError):
            sunspot_input = False
        else:
            sunspot_input = True
        figure_paths = [
            figures_dir / f"Fig_{number:02d}.jpg" for number in range(1, 7)
        ]
        quarterly_paths = [
            quarterly_dir / f"goes{mission:02d}_quarterly.csv"
            for mission in analysis.ALL_MISSIONS
        ]
        quarterly_file_count = sum(path.is_file() for path in quarterly_paths)
        try:
            quarterly_manifest = quarterly_dir / "SHA256SUMS"
            released_quarterly = all(
                path.is_file()
                and sha256_file(path) == manifest_checksum(quarterly_manifest, path.name)
                for path in quarterly_paths
            )
        except (OSError, RuntimeError):
            released_quarterly = False
        selected_paths = [
            selected_dir / f"goes{mission:02d}_selected.csv"
            for mission in analysis.PAPER_MISSIONS
        ]
        return {
            "config": str(Path(self.options.config).resolve()),
            "released_quarterly": released_quarterly,
            "released_quarterly_files": quarterly_file_count,
            "sunspot_input": sunspot_input,
            "selected": all(path.is_file() for path in selected_paths),
            "selected_files": sum(path.is_file() for path in selected_paths),
            "wavelet_files": len(list(wavelet_dir.glob("*.npz"))) if wavelet_dir.is_dir() else 0,
            "figures": {path.name: path.is_file() for path in figure_paths},
        }

    def print_status(self) -> None:
        print(json.dumps(self.status(), indent=2, sort_keys=True))


__all__ = ["PipelineOptions", "ReproductionPipeline"]
