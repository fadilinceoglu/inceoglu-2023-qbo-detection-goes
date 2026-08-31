#!/usr/bin/env python3
"""Generate the six paper figures from portable analysis checkpoints."""

from __future__ import annotations

import argparse
import os
import shlex
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import gridspec
from matplotlib.lines import Line2D
from scipy.signal import find_peaks

try:
    from analyze import (
        ALL_MISSIONS,
        COMPONENTS,
        COMPONENT_LABELS,
        DEFAULT_CONFIG,
        PAPER_MISSIONS,
        SIGNIFICANCE_METHOD,
        AnalysisSettings,
        _array_digest,
        _selected_component,
        _wavelet_provenance,
        difference_of_lowpasses,
        find_sunspot_file,
        load_settings,
        load_sunspot_monthly,
        quarterly_sunspots,
    )
except ModuleNotFoundError:  # imported as scripts.make_figures
    from scripts.analyze import (
        ALL_MISSIONS,
        COMPONENTS,
        COMPONENT_LABELS,
        DEFAULT_CONFIG,
        PAPER_MISSIONS,
        SIGNIFICANCE_METHOD,
        AnalysisSettings,
        _array_digest,
        _selected_component,
        _wavelet_provenance,
        difference_of_lowpasses,
        find_sunspot_file,
        load_settings,
        load_sunspot_monthly,
        quarterly_sunspots,
    )


MISSION_COLORS = {
    1: "lime",
    2: "navy",
    3: "violet",
    5: "magenta",
    6: "tab:red",
    7: "tab:blue",
    8: "tab:orange",
    9: "royalblue",
    10: "tab:green",
    11: "maroon",
    12: "tab:pink",
    13: "tab:cyan",
    14: "gold",
    15: "tab:purple",
    16: "salmon",
    17: "teal",
}

COMPONENT_FIGURES = {"b_g": 3, "b_s": 4, "b_m": 5}
PERIOD_TICKS = np.round(2.0 ** np.array([-0.73, 0.585, 2.17]), 1)
CANONICAL_FIGURE_COLUMNS = ["time", *COMPONENTS]
SUNSPOT_SPANS: tuple[tuple[str, int, int], ...] = (
    ("goes06_07", 6, 7),
    ("goes08", 8, 8),
    ("goes10", 10, 10),
    ("goes12", 12, 12),
    ("goes13_15", 13, 13),
    ("goes17", 17, 17),
)


def _read_timeseries(path: Path, required: Sequence[str]) -> pd.DataFrame:
    frame = pd.read_csv(path)
    if "time" not in frame:
        raise ValueError(f"{path} has no 'time' column")
    missing = [column for column in required if column not in frame]
    if missing:
        raise ValueError(f"{path} is missing required columns: {', '.join(missing)}")
    frame["time"] = pd.to_datetime(frame["time"], errors="raise")
    for column in required:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if "decyear" in frame:
        frame["decyear"] = pd.to_numeric(frame["decyear"], errors="coerce")
    return frame.sort_values("time", kind="stable").reset_index(drop=True)


def _atomic_save(fig: plt.Figure, destination: Path, *, dpi: int) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=destination.stem + ".", suffix=destination.suffix, dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        fig.savefig(temporary, bbox_inches="tight", dpi=dpi)
        os.replace(temporary, destination)
    finally:
        plt.close(fig)
        temporary.unlink(missing_ok=True)
    return destination


def _figure_path(settings: AnalysisSettings, number: int) -> Path:
    return settings.figures_dir / f"Fig_{number:02d}.jpg"


def _missing_inputs(paths: Mapping[Any, Path], instruction: str) -> None:
    missing = [str(path) for path in paths.values() if not path.exists()]
    if missing:
        raise FileNotFoundError(instruction + "\n  " + "\n  ".join(missing))


def _iter_minute_for_plot(
    path: Path,
    *,
    row_stride: int,
    chunksize: int = 250_000,
) -> Iterator[pd.DataFrame]:
    """Yield deterministic plotting chunks from one minute-resolution mission.

    The global row offset makes optional preview decimation independent of CSV
    chunk boundaries. A stride of one, used by the paper configuration, yields
    every retained row while keeping memory bounded by ``chunksize``.
    """

    if row_stride < 1:
        raise ValueError("row_stride must be positive")
    offset = 0
    for frame in pd.read_csv(
        path,
        usecols=CANONICAL_FIGURE_COLUMNS,
        parse_dates=["time"],
        chunksize=chunksize,
    ):
        positions = np.arange(offset, offset + len(frame), dtype=np.int64)
        keep = positions % row_stride == 0
        if keep.any():
            subset = frame.loc[keep].copy()
            for component in COMPONENTS:
                subset[component] = pd.to_numeric(subset[component], errors="coerce")
            yield subset.reset_index(drop=True)
        offset += len(frame)


def _read_minute_for_plot(
    path: Path,
    *,
    row_stride: int,
    chunksize: int = 250_000,
) -> pd.DataFrame:
    """Collect plotting chunks; retained as a small-data testing utility."""

    selected = list(
        _iter_minute_for_plot(path, row_stride=row_stride, chunksize=chunksize)
    )
    if not selected:
        return pd.DataFrame(columns=CANONICAL_FIGURE_COLUMNS)
    return pd.concat(selected, ignore_index=True)


def make_figure_1(
    config_path: str | os.PathLike[str] = DEFAULT_CONFIG,
    *,
    processed_dir: str | os.PathLike[str] | None = None,
    figures_dir: str | os.PathLike[str] | None = None,
    dpi: int = 600,
) -> Path:
    """Plot the cleaned one-minute GSM components for all sixteen missions."""

    settings = load_settings(config_path)
    processed = Path(processed_dir) if processed_dir is not None else settings.processed_dir
    if figures_dir is not None:
        settings = replace(settings, figures_dir=Path(figures_dir))
    paths = {
        mission: processed / "minute" / f"goes{mission:02d}.csv.gz"
        for mission in ALL_MISSIONS
    }
    _missing_inputs(
        paths,
        "Figure 1 needs the cleaned one-minute mission checkpoints. "
        "Run the merge-and-clean stage first; missing files:",
    )
    fig, axes = plt.subplots(3, 1, figsize=(8, 8), sharex=True)
    fig.subplots_adjust(hspace=0.1, wspace=0.1)
    limits = {"b_g": (-250, 250), "b_s": (-250, 250), "b_m": (-50, 250)}
    for mission in ALL_MISSIONS:
        previous: pd.DataFrame | None = None
        for frame in _iter_minute_for_plot(
            paths[mission], row_stride=settings.figure1_row_stride
        ):
            if previous is not None:
                frame = pd.concat((previous, frame), ignore_index=True)
            for row, component in enumerate(COMPONENTS):
                axes[row].plot(
                    frame["time"],
                    frame[component],
                    color=MISSION_COLORS[mission],
                    linewidth=0.7,
                    zorder=10 if mission == 1 else None,
                )
            previous = frame.tail(1)
    for row, component in enumerate(COMPONENTS):
        axis = axes[row]
        axis.set_ylabel(f"{COMPONENT_LABELS[component]} (nT)", labelpad=10, fontsize=15)
        axis.set_ylim(limits[component])
        axis.grid(color="gray", linestyle="dashed")
        axis.tick_params(labelsize=15)
        axis.text(
            0.90,
            0.85,
            f"({chr(ord('a') + row)})",
            transform=axis.transAxes,
            fontsize=15,
        )
    axes[-1].set_xlabel("time (yr)", fontsize=15)
    handles = [
        Line2D([0], [0], color=MISSION_COLORS[mission], label=f"G{mission:02d}")
        for mission in ALL_MISSIONS
    ]
    fig.legend(
        handles=handles,
        loc="lower center",
        frameon=False,
        ncol=8,
        bbox_to_anchor=(0.5, -0.03),
    )
    return _atomic_save(fig, _figure_path(settings, 1), dpi=dpi)


def _load_selected(settings: AnalysisSettings, processed_dir: Path | None = None) -> dict[int, pd.DataFrame]:
    root = processed_dir if processed_dir is not None else settings.processed_dir
    paths = {
        mission: root / "selected" / f"goes{mission:02d}_selected.csv"
        for mission in PAPER_MISSIONS
    }
    _missing_inputs(
        paths,
        "Figures 2–6 need selected mission checkpoints. Run `python scripts/analyze.py "
        "select-filter` first; missing files:",
    )
    required = [
        *(f"chosen_{component}" for component in COMPONENTS),
        *(f"chosen_{component}_bp" for component in COMPONENTS),
    ]
    return {
        mission: _read_timeseries(path, required)
        for mission, path in paths.items()
    }


def _plot_selected_series(
    axis: plt.Axes,
    frames: Mapping[int, pd.DataFrame],
    missions: Sequence[int],
    column: str,
) -> None:
    for mission in missions:
        frame = frames[mission]
        valid = frame[column].notna()
        if not valid.any():
            continue
        time = frame.loc[valid, "time"]
        values = frame.loc[valid, column].to_numpy(dtype=float)
        color = MISSION_COLORS[mission]
        axis.plot(time, values, color=color, linewidth=1.0, label=f"G{mission:02d}")
        spread = float(np.std(values, ddof=1)) if values.size > 1 else 0.0
        axis.fill_between(time, values - spread, values + spread, color=color, alpha=0.2)


def make_figure_2(
    config_path: str | os.PathLike[str] = DEFAULT_CONFIG,
    *,
    processed_dir: str | os.PathLike[str] | None = None,
    figures_dir: str | os.PathLike[str] | None = None,
    dpi: int = 600,
) -> Path:
    """Plot the selected quarterly components and their 1.1–4.5 yr signals."""

    settings = load_settings(config_path)
    processed = Path(processed_dir) if processed_dir is not None else settings.processed_dir
    if figures_dir is not None:
        settings = replace(settings, figures_dir=Path(figures_dir))
    frames = _load_selected(settings, processed)
    all_times = pd.concat([frame["time"] for frame in frames.values()], ignore_index=True)
    time_limits = (all_times.min(), all_times.max())

    fig, axes = plt.subplots(3, 2, figsize=(16, 8), sharex=True)
    fig.subplots_adjust(hspace=0.1, wspace=0.1)
    raw_limits = {"b_g": (-50, 50), "b_s": (-3, 3), "b_m": (50, 100)}
    filtered_limits = {"b_g": (-50, 50), "b_s": (-2, 2), "b_m": (-4, 4)}

    for row, component in enumerate(COMPONENTS):
        raw_axis = axes[row, 0]
        filtered_axis = axes[row, 1]
        ordinary_missions = PAPER_MISSIONS
        if component == "b_s":
            ordinary_missions = tuple(item for item in PAPER_MISSIONS if item != 6)
            raw_g06 = raw_axis.twinx()
            filtered_g06 = filtered_axis.twinx()
            _plot_selected_series(raw_g06, frames, (6,), "chosen_b_s")
            _plot_selected_series(filtered_g06, frames, (6,), "chosen_b_s_bp")
            raw_g06.set_ylim(-20, 20)
            filtered_g06.set_ylim(-20, 20)
            raw_g06.spines["right"].set_color(MISSION_COLORS[6])
            filtered_g06.spines["right"].set_visible(False)
            filtered_g06.spines["left"].set_visible(True)
            filtered_g06.spines["left"].set_color(MISSION_COLORS[6])
            filtered_g06.yaxis.set_label_position("left")
            filtered_g06.yaxis.tick_left()
            raw_g06.tick_params(axis="y", colors=MISSION_COLORS[6])
            filtered_g06.tick_params(axis="y", colors=MISSION_COLORS[6])
        _plot_selected_series(raw_axis, frames, ordinary_missions, f"chosen_{component}")
        _plot_selected_series(
            filtered_axis, frames, ordinary_missions, f"chosen_{component}_bp"
        )

        raw_axis.set_ylim(raw_limits[component])
        filtered_axis.set_ylim(filtered_limits[component])
        raw_axis.set_xlim(time_limits)
        filtered_axis.set_xlim(time_limits)
        raw_axis.set_ylabel(f"{COMPONENT_LABELS[component]} (nT)", fontsize=15)
        filtered_axis.set_ylabel(f"{COMPONENT_LABELS[component]} (nT)", fontsize=15)
        filtered_axis.yaxis.set_label_position("right")
        filtered_axis.yaxis.tick_right()
        for column, axis in enumerate((raw_axis, filtered_axis)):
            panel = 2 * row + column
            axis.grid(color="gray", linestyle="dashed")
            axis.tick_params(labelsize=15)
            axis.text(
                0.90,
                0.85,
                f"({chr(ord('a') + panel)})",
                transform=axis.transAxes,
                fontsize=15,
            )
    axes[-1, 0].set_xlabel("time (yr)", fontsize=15)
    axes[-1, 1].set_xlabel("time (yr)", fontsize=15)
    handles = [
        Line2D([0], [0], color=MISSION_COLORS[mission], label=f"G{mission:02d}")
        for mission in PAPER_MISSIONS
    ]
    fig.legend(handles=handles, loc="lower center", frameon=False, ncol=8, bbox_to_anchor=(0.5, -0.03))
    return _atomic_save(fig, _figure_path(settings, 2), dpi=dpi)


def _analysis_rerun_command(
    config_path: str | os.PathLike[str],
    *,
    processed_dir: Path | None = None,
    source_dir: Path | None = None,
    wavelet_dir: Path | None = None,
) -> str:
    arguments = [
        "python",
        "scripts/analyze.py",
        "wavelets",
        "--config",
        str(Path(config_path).expanduser()),
    ]
    for flag, value in (
        ("--processed-dir", processed_dir),
        ("--source-dir", source_dir),
        ("--output-dir", wavelet_dir),
    ):
        if value is not None:
            arguments.extend((flag, str(value)))
    arguments.append("--force")
    return " ".join(shlex.quote(argument) for argument in arguments)


def _wavelet_checkpoint_metadata(
    settings: AnalysisSettings,
    time: np.ndarray,
    values: np.ndarray,
    *,
    provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the metadata required for a checkpoint consumed by a figure."""

    expected = {
        "input_sha256": _array_digest(time, values),
        "significance_method": SIGNIFICANCE_METHOD,
        "confidence": settings.confidence,
        "morlet_frequency": settings.morlet_frequency,
        "dj": settings.scale_resolution,
        "j": settings.scale_count,
        "smallest_scale_factor": settings.smallest_scale_factor,
    }
    expected.update(_wavelet_provenance() if provenance is None else provenance)
    return expected


def load_wavelet(
    path: str | os.PathLike[str],
    *,
    expected_metadata: Mapping[str, Any],
    config_path: str | os.PathLike[str],
    processed_dir: Path | None = None,
    source_dir: Path | None = None,
    wavelet_dir: Path | None = None,
) -> dict[str, Any]:
    """Load a wavelet checkpoint valid for the current calculation inputs."""

    source = Path(path)
    rerun = _analysis_rerun_command(
        config_path,
        processed_dir=processed_dir,
        source_dir=source_dir,
        wavelet_dir=wavelet_dir,
    )
    if not source.exists():
        raise FileNotFoundError(
            f"Wavelet checkpoint missing: {source}. Run `{rerun}`, then generate "
            "the figure again."
        )
    with np.load(source, allow_pickle=False) as data:
        required = (
            "time",
            "period",
            "coi",
            "power",
            "local_ratio",
            "global_power",
            "global_threshold",
        )
        missing = [key for key in required if key not in data]
        if missing:
            raise ValueError(
                f"{source} is missing wavelet arrays: {', '.join(missing)}. "
                f"Run `{rerun}`, then generate the figure again."
            )
        invalid_metadata = []
        for key, expected in expected_metadata.items():
            if key not in data:
                invalid_metadata.append(key)
                continue
            stored = data[key]
            stored_value = stored.item() if np.asarray(stored).ndim == 0 else stored
            if str(stored_value) != str(expected):
                invalid_metadata.append(key)
        if invalid_metadata:
            fields = ", ".join(sorted(invalid_metadata))
            raise ValueError(
                f"Wavelet checkpoint does not match the current calculation: {source}. "
                f"Missing or different metadata: {fields}. Run `{rerun}`, then "
                "generate the figure again."
            )
        return {key: data[key].copy() for key in data.files}


def _sunspot_wavelet_inputs(
    monthly: pd.DataFrame,
    frames: Mapping[int, pd.DataFrame],
    settings: AnalysisSettings,
) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Build the exact current inputs to the seven sunspot CWTs."""

    sunspots = quarterly_sunspots(monthly)
    if not sunspots["ssn_mean"].notna().all():
        raise ValueError("SILSO quarterly means contain gaps; rerun the analysis stage")
    sunspots["ssn_bp"] = difference_of_lowpasses(
        sunspots["ssn_mean"].to_numpy(dtype=float),
        sunspots["decyear"].to_numpy(dtype=float),
        short_period_samples=settings.short_period_samples,
        long_period_samples=settings.long_period_samples,
        order=settings.bandpass_order,
    )
    series: dict[str, pd.DataFrame] = {"full": sunspots}
    for name, first_mission, last_mission in SUNSPOT_SPANS:
        first_time, _ = _selected_component(frames[first_mission], "b_m")
        last_time, _ = _selected_component(frames[last_mission], "b_m")
        subset = sunspots.loc[
            (sunspots["decyear"] >= first_time[0])
            & (sunspots["decyear"] <= last_time[-1])
        ]
        if subset.empty:
            raise ValueError(f"SILSO data do not overlap the {name} interval")
        series[name] = subset
    return {
        name: (
            frame["decyear"].to_numpy(dtype=float),
            frame["ssn_bp"].to_numpy(dtype=float),
        )
        for name, frame in series.items()
    }


def _significant_global_periods(result: Mapping[str, np.ndarray]) -> np.ndarray:
    power = np.asarray(result["global_power"], dtype=float)
    threshold = np.asarray(result["global_threshold"], dtype=float)
    period = np.asarray(result["period"], dtype=float)
    peaks, _ = find_peaks(power, height=0.0)
    keep = peaks[(period[peaks] < 5.0) & (power[peaks] > threshold[peaks])]
    return period[keep]


def _plot_wavelet_row(
    wavelet_axis: plt.Axes,
    global_axis: plt.Axes,
    result: Mapping[str, np.ndarray],
    *,
    label: str,
    common_time_limits: tuple[float, float],
    show_xlabels: bool,
    global_xlabel: str,
) -> None:
    time = np.asarray(result["time"], dtype=float)
    period = np.asarray(result["period"], dtype=float)
    log_period = np.log2(period)
    power = np.asarray(result["power"], dtype=float)
    local_ratio = np.asarray(result["local_ratio"], dtype=float)
    coi = np.asarray(result["coi"], dtype=float)
    global_power = np.asarray(result["global_power"], dtype=float)
    global_threshold = np.asarray(result["global_threshold"], dtype=float)

    wavelet_axis.contourf(time, log_period, power, cmap="Blues")
    wavelet_axis.contour(
        time,
        log_period,
        local_ratio,
        levels=[1.0],
        colors="k",
        linestyles="dotted",
        linewidths=1.5,
    )

    # Every polygon ordinate is in log2(period), the coordinate of this axis.
    lower_period = float(period.min())
    upper_log_period = float(np.log2(period.max()))
    coi_log = np.log2(np.clip(coi, lower_period, period.max()))
    wavelet_axis.fill(
        np.concatenate((time, [time[-1], time[0]])),
        np.concatenate((coi_log, [upper_log_period, upper_log_period])),
        color="k",
        alpha=0.3,
        hatch="x",
    )
    wavelet_axis.set_yticks(np.log2(PERIOD_TICKS))
    wavelet_axis.set_yticklabels(PERIOD_TICKS)
    wavelet_axis.set_ylim(np.log2([lower_period, 2.0**2.5]))
    wavelet_axis.set_xlim(common_time_limits)
    wavelet_axis.set_ylabel("period (yr)")
    wavelet_axis.grid(color="gray", linestyle="dashed")
    wavelet_axis.text(0.01, 0.05, label, transform=wavelet_axis.transAxes, fontsize=13)
    wavelet_axis.xaxis.set_ticks_position("both")
    wavelet_axis.yaxis.set_ticks_position("both")
    if show_xlabels:
        wavelet_axis.set_xlabel("time (yr)")
    else:
        wavelet_axis.tick_params(labelbottom=False)

    global_axis.plot(global_threshold, log_period, "r--")
    global_axis.plot(global_power, log_period, "k-", linewidth=1.5)
    significant = _significant_global_periods(result)
    for value in significant:
        global_axis.axhline(np.log2(value), color="blue", linestyle="--")
    global_axis.set_xlim(0, 11)
    global_axis.set_ylim(np.log2([lower_period, 2.0**2.5]))
    global_axis.set_yticks(np.log2(significant))
    global_axis.set_yticklabels(np.round(significant, 1), color="blue")
    global_axis.tick_params(
        top=True,
        labeltop=True,
        bottom=False,
        labelbottom=False,
        right=True,
        labelright=True,
        left=False,
        labelleft=False,
    )
    global_axis.grid(color="gray", linestyle="dashed")
    if show_xlabels:
        global_axis.set_xlabel(global_xlabel)


def make_component_wavelet_figure(
    component: str,
    config_path: str | os.PathLike[str] = DEFAULT_CONFIG,
    *,
    processed_dir: str | os.PathLike[str] | None = None,
    source_dir: str | os.PathLike[str] | None = None,
    wavelet_dir: str | os.PathLike[str] | None = None,
    figures_dir: str | os.PathLike[str] | None = None,
    dpi: int = 600,
) -> Path:
    """Generate one of the three GOES component CWT figures."""

    if component not in COMPONENT_FIGURES:
        raise ValueError(f"unknown component: {component}")
    settings = load_settings(config_path)
    processed = Path(processed_dir) if processed_dir is not None else settings.processed_dir
    source = Path(source_dir) if source_dir is not None else settings.source_dir
    wavelets = Path(wavelet_dir) if wavelet_dir is not None else settings.work_dir / "wavelets"
    if figures_dir is not None:
        settings = replace(settings, figures_dir=Path(figures_dir))
    frames = _load_selected(settings, processed)
    monthly = load_sunspot_monthly(find_sunspot_file(source))
    provenance = _wavelet_provenance()
    results: dict[int, dict[str, Any]] = {}
    for mission in PAPER_MISSIONS:
        time, values = _selected_component(frames[mission], component)
        results[mission] = load_wavelet(
            wavelets / f"wavelet_goes{mission:02d}_{component}.npz",
            expected_metadata=_wavelet_checkpoint_metadata(
                settings, time, values, provenance=provenance
            ),
            config_path=config_path,
            processed_dir=processed,
            source_dir=source,
            wavelet_dir=wavelets,
        )
    common_limits = (
        float(min(frame["decyear"].min() for frame in frames.values())),
        float(max(frame["decyear"].max() for frame in frames.values())),
    )

    fig = plt.figure(figsize=(6, 12))
    fig.subplots_adjust(hspace=0.1, wspace=0.0)
    grid = gridspec.GridSpec(9, 2, width_ratios=[6, 1])
    top = fig.add_subplot(grid[0])
    top.plot(monthly["decyear"], monthly["ssn"], color="k")
    top.set_xlim(common_limits)
    top.set_ylabel("SSN (c/m)")
    top.grid(color="gray", linestyle="dashed")
    top.tick_params(labelbottom=False)
    top.text(0.83, 0.75, "(a) SSN", transform=top.transAxes, fontsize=13)

    for row, mission in enumerate(PAPER_MISSIONS, start=1):
        wavelet_axis = fig.add_subplot(grid[2 * row])
        global_axis = fig.add_subplot(grid[2 * row + 1])
        panel = chr(ord("a") + row)
        _plot_wavelet_row(
            wavelet_axis,
            global_axis,
            results[mission],
            label=f"({panel}) GOES-{mission:02d}",
            common_time_limits=common_limits,
            show_xlabels=row == len(PAPER_MISSIONS),
            global_xlabel=r"Power $(nT)^2$",
        )
    number = COMPONENT_FIGURES[component]
    return _atomic_save(fig, _figure_path(settings, number), dpi=dpi)


def make_figure_6(
    config_path: str | os.PathLike[str] = DEFAULT_CONFIG,
    *,
    processed_dir: str | os.PathLike[str] | None = None,
    source_dir: str | os.PathLike[str] | None = None,
    wavelet_dir: str | os.PathLike[str] | None = None,
    figures_dir: str | os.PathLike[str] | None = None,
    dpi: int = 600,
) -> Path:
    """Generate the full-span and mission-span sunspot CWT comparison."""

    settings = load_settings(config_path)
    processed = Path(processed_dir) if processed_dir is not None else settings.processed_dir
    source = Path(source_dir) if source_dir is not None else settings.source_dir
    wavelets = Path(wavelet_dir) if wavelet_dir is not None else settings.work_dir / "wavelets"
    if figures_dir is not None:
        settings = replace(settings, figures_dir=Path(figures_dir))
    frames = _load_selected(settings, processed)
    monthly = load_sunspot_monthly(find_sunspot_file(source))
    quarterly_path = processed / "selected" / "sunspots_quarterly.csv"
    if not quarterly_path.exists():
        raise FileNotFoundError(
            f"{quarterly_path} is missing. Run `python scripts/analyze.py wavelets`."
        )
    quarterly = _read_timeseries(quarterly_path, ("ssn_mean", "ssn_bp"))
    if "decyear" not in quarterly:
        raise ValueError(f"{quarterly_path} has no decyear column")

    span_labels = (
        ("full", "(b) Full span"),
        ("goes06_07", "(c) GOES-06&07 span"),
        ("goes08", "(d) GOES-08 span"),
        ("goes10", "(e) GOES-10 span"),
        ("goes12", "(f) GOES-12 span"),
        ("goes13_15", "(g) GOES-13&15 span"),
        ("goes17", "(h) GOES-17 span"),
    )
    wavelet_inputs = _sunspot_wavelet_inputs(monthly, frames, settings)
    provenance = _wavelet_provenance()
    results: dict[str, dict[str, Any]] = {}
    for name, _ in span_labels:
        time, values = wavelet_inputs[name]
        results[name] = load_wavelet(
            wavelets / f"wavelet_sunspots_{name}.npz",
            expected_metadata=_wavelet_checkpoint_metadata(
                settings, time, values, provenance=provenance
            ),
            config_path=config_path,
            processed_dir=processed,
            source_dir=source,
            wavelet_dir=wavelets,
        )
    common_limits = (
        float(min(frame["decyear"].min() for frame in frames.values())),
        float(max(frame["decyear"].max() for frame in frames.values())),
    )

    fig = plt.figure(figsize=(6, 12))
    fig.subplots_adjust(hspace=0.1, wspace=0.0)
    grid = gridspec.GridSpec(8, 2, width_ratios=[6, 1])
    top = fig.add_subplot(grid[0])
    top.plot(monthly["decyear"], monthly["ssn"], color="k")
    top.set_xlim(common_limits)
    top.set_ylabel("SSN (c/m)")
    top.grid(color="gray", linestyle="dashed")
    top.tick_params(labelbottom=False)
    top.text(0.01, 0.75, "(a)", transform=top.transAxes, fontsize=13)
    filtered = top.twinx()
    filtered.plot(quarterly["decyear"], quarterly["ssn_bp"], color="tab:red")
    filtered.set_ylim(-50, 50)

    for row, (name, label) in enumerate(span_labels, start=1):
        wavelet_axis = fig.add_subplot(grid[2 * row])
        global_axis = fig.add_subplot(grid[2 * row + 1])
        _plot_wavelet_row(
            wavelet_axis,
            global_axis,
            results[name],
            label=label,
            common_time_limits=common_limits,
            show_xlabels=row == len(span_labels),
            global_xlabel=r"Power$^2$",
        )
    return _atomic_save(fig, _figure_path(settings, 6), dpi=dpi)


def make_figure(
    number: int,
    config_path: str | os.PathLike[str] = DEFAULT_CONFIG,
    **options: Any,
) -> Path:
    """Generate one canonical figure by number."""

    if number == 1:
        allowed = {key: value for key, value in options.items() if key in {"processed_dir", "figures_dir", "dpi"}}
        return make_figure_1(config_path, **allowed)
    if number == 2:
        allowed = {key: value for key, value in options.items() if key in {"processed_dir", "figures_dir", "dpi"}}
        return make_figure_2(config_path, **allowed)
    if number in (3, 4, 5):
        component = {3: "b_g", 4: "b_s", 5: "b_m"}[number]
        return make_component_wavelet_figure(component, config_path, **options)
    if number == 6:
        return make_figure_6(config_path, **options)
    raise ValueError("figure number must be between 1 and 6")


def make_all_figures(
    config_path: str | os.PathLike[str] = DEFAULT_CONFIG,
    **options: Any,
) -> list[Path]:
    """Generate Figures 1–6 in canonical order."""

    return [make_figure(number, config_path, **options) for number in range(1, 7)]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("figure", choices=("1", "2", "3", "4", "5", "6", "all"))
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--processed-dir", type=Path)
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--wavelet-dir", type=Path)
    parser.add_argument("--figures-dir", type=Path)
    parser.add_argument("--dpi", type=int, default=600)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    options = {
        "processed_dir": args.processed_dir,
        "source_dir": args.source_dir,
        "wavelet_dir": args.wavelet_dir,
        "figures_dir": args.figures_dir,
        "dpi": args.dpi,
    }
    if args.figure == "all":
        outputs = make_all_figures(args.config, **options)
    else:
        outputs = [make_figure(int(args.figure), args.config, **options)]
    for output in outputs:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
