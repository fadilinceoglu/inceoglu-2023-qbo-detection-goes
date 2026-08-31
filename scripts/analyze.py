#!/usr/bin/env python3
"""Select the paper intervals, filter them, and calculate wavelet products.

The module is both a command-line program and the analysis API used by
``scripts/reproduce.py``.  The published calculation uses PyCWT's local and
global AR(1) significance tests.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import tempfile
from dataclasses import dataclass
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
from scipy.signal import butter, filtfilt

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.10
    import tomli as tomllib


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = REPOSITORY_ROOT / "data" / "config" / "paper.toml"

ALL_MISSIONS = (1, 2, 3, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17)
PAPER_MISSIONS = (6, 7, 8, 10, 12, 13, 15, 17)
COMPONENTS = ("b_g", "b_s", "b_m")
COMPONENT_LABELS = {
    "b_g": r"$B_{GSMx}$",
    "b_s": r"$B_{GSMy}$",
    "b_m": r"$B_{GSMz}$",
}

DEFAULT_MORLET_FREQUENCY = 6.0
DEFAULT_DJ = 1.0 / 64.0
DEFAULT_J = 640
DEFAULT_S0_FACTOR = 2.0

# Bump the schema version when stored fields or their meanings change. Bump the
# algorithm version when any numerical step changes intentionally.
WAVELET_CACHE_SCHEMA = "qbo_wavelet_npz"
WAVELET_CACHE_VERSION = 1
WAVELET_ALGORITHM = "morlet_cwt_pycwt_ar1_significance"
WAVELET_ALGORITHM_VERSION = 3
SIGNIFICANCE_METHOD = "pycwt-ar1"


@dataclass(frozen=True)
class AnalysisSettings:
    """Resolved values required by the two public analysis stages."""

    repository_root: Path
    source_dir: Path
    processed_dir: Path
    figures_dir: Path
    work_dir: Path
    figure1_row_stride: int
    selected_missions: tuple[int, ...]
    min_continuous_years: float
    bandpass_order: int
    short_period_samples: float
    long_period_samples: float
    morlet_frequency: float
    confidence: float
    scale_resolution: float
    scale_count: int
    smallest_scale_factor: float


def _resolve_path(root: Path, value: str | os.PathLike[str]) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else root / path


def load_settings(config_path: str | os.PathLike[str] = DEFAULT_CONFIG) -> AnalysisSettings:
    """Load and resolve the paper configuration.

    Relative paths are resolved against the repository root, not the caller's
    working directory.  That invariant makes CLI and imported use identical.
    """

    config_file = Path(config_path).expanduser().resolve()
    if not config_file.exists():
        raise FileNotFoundError(
            f"Configuration not found: {config_file}. Use --config to select paper.toml."
        )
    with config_file.open("rb") as stream:
        config = tomllib.load(stream)

    paths = config.get("paths", {})
    preprocessing = config.get("preprocessing", {})
    bandpass = config.get("bandpass", {})
    wavelet = config.get("wavelet", {})
    figures = config.get("figures", {})
    root = REPOSITORY_ROOT

    missions = tuple(
        int(item)
        for item in preprocessing.get("selected_goes_missions", PAPER_MISSIONS)
    )
    if missions != PAPER_MISSIONS:
        raise ValueError(
            "The paper configuration must select GOES 06, 07, 08, 10, 12, "
            "13, 15, and 17, in that order."
        )

    confidence = float(wavelet.get("confidence_level", 0.95))
    if not 0.0 < confidence < 1.0:
        raise ValueError("wavelet.confidence_level must be between zero and one.")

    if bandpass.get("zero_phase", True) is not True:
        raise ValueError("The paper configuration requires zero-phase filtering.")
    short_period_samples = float(bandpass.get("short_period_samples", 4.445))
    long_period_samples = float(bandpass.get("long_period_samples", 18.1))
    if not 2.0 < short_period_samples < long_period_samples:
        raise ValueError("Band-pass sample-domain periods are invalid.")
    if str(wavelet.get("mother", "morlet")).lower() != "morlet":
        raise ValueError("The paper configuration requires the Morlet wavelet.")
    scale_resolution = float(wavelet.get("scale_resolution", DEFAULT_DJ))
    scale_count = int(wavelet.get("scale_count", DEFAULT_J))
    smallest_scale_factor = float(
        wavelet.get("smallest_scale_factor", DEFAULT_S0_FACTOR)
    )
    if scale_resolution <= 0.0 or scale_count < 1 or smallest_scale_factor <= 0.0:
        raise ValueError("Wavelet scale parameters must be positive.")
    figure1_row_stride = int(figures.get("minute_row_stride", 1))
    if figure1_row_stride < 1:
        raise ValueError("figures.minute_row_stride must be positive.")

    return AnalysisSettings(
        repository_root=root,
        source_dir=_resolve_path(root, paths.get("source", "data/source")),
        processed_dir=_resolve_path(root, paths.get("processed", "data/processed")),
        figures_dir=_resolve_path(root, paths.get("figures", "outputs/figures")),
        work_dir=root / "outputs" / "work",
        figure1_row_stride=figure1_row_stride,
        selected_missions=missions,
        min_continuous_years=float(
            preprocessing.get("minimum_continuous_years", 4.5)
        ),
        bandpass_order=int(bandpass.get("order", 5)),
        short_period_samples=short_period_samples,
        long_period_samples=long_period_samples,
        morlet_frequency=float(wavelet.get("morlet_frequency", DEFAULT_MORLET_FREQUENCY)),
        confidence=confidence,
        scale_resolution=scale_resolution,
        scale_count=scale_count,
        smallest_scale_factor=smallest_scale_factor,
    )


def decimal_year(datetimes: Sequence[Any] | pd.Series | pd.Index) -> np.ndarray:
    """Convert datetimes to fractional years using each year's true length."""

    dates = pd.DatetimeIndex(pd.to_datetime(datetimes, errors="raise"))
    starts = pd.DatetimeIndex(
        pd.to_datetime({"year": dates.year, "month": 1, "day": 1})
    )
    ends = pd.DatetimeIndex(
        pd.to_datetime({"year": dates.year + 1, "month": 1, "day": 1})
    )
    elapsed = (dates - starts).total_seconds().astype(float)
    duration = (ends - starts).total_seconds().astype(float)
    return dates.year.to_numpy(dtype=float) + elapsed / duration


def _atomic_csv(frame: pd.DataFrame, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    frame.to_csv(temporary, index=False)
    os.replace(temporary, destination)


def _normalise_time_column(frame: pd.DataFrame, source: Path) -> pd.DataFrame:
    aliases = {str(column).strip().lower(): column for column in frame.columns}
    time_name = aliases.get("time") or aliases.get("time_")
    if time_name is None:
        unnamed = [column for column in frame.columns if str(column).startswith("Unnamed:")]
        if unnamed:
            candidate = pd.to_datetime(frame[unnamed[0]], errors="coerce")
            if candidate.notna().all():
                frame = frame.rename(columns={unnamed[0]: "time"})
                time_name = "time"
    if time_name is None:
        raise ValueError(f"{source} has no 'time' column.")
    if time_name != "time":
        frame = frame.rename(columns={time_name: "time"})
    frame = frame.copy()
    frame["time"] = pd.to_datetime(frame["time"], errors="raise")
    frame = frame.sort_values("time", kind="stable").reset_index(drop=True)
    if frame["time"].duplicated().any():
        raise ValueError(f"{source} contains duplicate quarterly timestamps.")
    return frame


def load_quarterly_checkpoint(path: str | os.PathLike[str]) -> pd.DataFrame:
    """Read one neutral CSV checkpoint and validate its analysis columns."""

    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(source)
    frame = _normalise_time_column(pd.read_csv(source), source)
    missing = [column for column in COMPONENTS if column not in frame.columns]
    if missing:
        raise ValueError(f"{source} is missing required columns: {', '.join(missing)}")
    for column in COMPONENTS:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
        std_column = f"{column}_std"
        if std_column in frame:
            frame[std_column] = pd.to_numeric(frame[std_column], errors="coerce")
    return frame


def longest_contiguous_interval(
    values: Sequence[float],
    times: Sequence[float] | None = None,
    *,
    gap_factor: float = 1.5,
) -> tuple[int, int] | None:
    """Return the inclusive bounds of the longest contiguous finite interval.

    A NaN always breaks an interval.  If decimal-year timestamps are supplied,
    a missing quarterly row also breaks it; this prevents absent timestamps
    from being mistaken for continuous observations.  Ties resolve to the
    earliest interval, matching NumPy's first-maximum rule.
    """

    data = np.asarray(values, dtype=float)
    valid = np.isfinite(data)
    if data.ndim != 1:
        raise ValueError("values must be one-dimensional")

    breaks_before = np.zeros(data.size, dtype=bool)
    if times is not None:
        time = np.asarray(times, dtype=float)
        if time.shape != data.shape:
            raise ValueError("times and values must have the same shape")
        valid &= np.isfinite(time)
        differences = np.diff(time)
        positive = differences[differences > 0]
        if positive.size:
            nominal_step = float(np.median(positive))
            breaks_before[1:] = differences > gap_factor * nominal_step

    if not valid.any():
        return None

    intervals: list[tuple[int, int]] = []
    start: int | None = None
    for index, is_valid in enumerate(valid):
        if is_valid and (start is None or not breaks_before[index]):
            if start is None:
                start = index
            continue
        if start is not None:
            intervals.append((start, index - 1))
            start = index if is_valid else None
    if start is not None:
        intervals.append((start, data.size - 1))
    return max(intervals, key=lambda bounds: bounds[1] - bounds[0])


def difference_of_lowpasses(
    values: Sequence[float],
    times: Sequence[float],
    *,
    short_period_samples: float = 4.445,
    long_period_samples: float = 18.1,
    order: int = 5,
) -> np.ndarray:
    """Apply the paper's zero-phase band-pass as two low-pass filters.

    The high-frequency low-pass minus the low-frequency low-pass uses the
    calculation's sample-domain periods 4.445 and 18.1.  At quarterly cadence
    these correspond approximately to 1.1 and 4.5 years.
    """

    data = np.asarray(values, dtype=float)
    time = np.asarray(times, dtype=float)
    if data.ndim != 1 or time.shape != data.shape:
        raise ValueError("times and values must be matching one-dimensional arrays")
    if data.size < 2 or not np.isfinite(data).all() or not np.isfinite(time).all():
        raise ValueError("band-pass input must contain finite values and times")
    differences = np.diff(time)
    if np.any(differences <= 0):
        raise ValueError("band-pass times must be strictly increasing")
    high_normalized_cutoff = 2.0 / short_period_samples
    low_normalized_cutoff = 2.0 / long_period_samples
    if not 0.0 < low_normalized_cutoff < high_normalized_cutoff < 1.0:
        raise ValueError("sample-domain band-pass periods do not define valid cutoffs")

    high_b, high_a = butter(order, high_normalized_cutoff, btype="low")
    low_b, low_a = butter(order, low_normalized_cutoff, btype="low")
    required = 3 * max(len(high_a), len(high_b), len(low_a), len(low_b))
    if data.size <= required:
        raise ValueError(
            f"zero-phase order-{order} filtering needs more than {required} samples; "
            f"received {data.size}"
        )
    return filtfilt(high_b, high_a, data) - filtfilt(low_b, low_a, data)


def select_and_filter_mission(
    frame: pd.DataFrame,
    mission: int,
    *,
    min_continuous_years: float = 4.5,
    short_period_samples: float = 4.445,
    long_period_samples: float = 18.1,
    order: int = 5,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Select and filter the longest qualifying interval of each component."""

    result = frame.copy()
    result["decyear"] = decimal_year(result["time"])
    summary: list[dict[str, Any]] = []

    for component in COMPONENTS:
        selected = f"chosen_{component}"
        selected_std = f"{selected}_std"
        filtered = f"{selected}_bp"
        result[selected] = np.nan
        result[selected_std] = np.nan
        result[filtered] = np.nan

        bounds = longest_contiguous_interval(result[component], result["decyear"])
        record: dict[str, Any] = {
            "mission": mission,
            "component": component,
            "accepted": False,
            "reason": "no_finite_interval",
            "start": "",
            "end": "",
            "duration_years": np.nan,
            "samples": 0,
        }
        if bounds is None:
            summary.append(record)
            continue

        start, stop = bounds
        duration = float(result.loc[stop, "decyear"] - result.loc[start, "decyear"])
        record.update(
            start=result.loc[start, "time"].isoformat(),
            end=result.loc[stop, "time"].isoformat(),
            duration_years=duration,
            samples=stop - start + 1,
        )
        if duration < min_continuous_years:
            record["reason"] = f"shorter_than_{min_continuous_years:g}_years"
            summary.append(record)
            continue

        interval = result.loc[start:stop, component].to_numpy(dtype=float)
        interval_time = result.loc[start:stop, "decyear"].to_numpy(dtype=float)
        try:
            bandpassed = difference_of_lowpasses(
                interval,
                interval_time,
                short_period_samples=short_period_samples,
                long_period_samples=long_period_samples,
                order=order,
            )
        except ValueError as error:
            record["reason"] = f"filter_rejected: {error}"
            summary.append(record)
            continue

        result.loc[start:stop, selected] = interval
        original_std = f"{component}_std"
        if original_std in result:
            result.loc[start:stop, selected_std] = result.loc[start:stop, original_std]
        result.loc[start:stop, filtered] = bandpassed
        record.update(accepted=True, reason="accepted")
        summary.append(record)

    return result, summary


def run_selection(
    config_path: str | os.PathLike[str] = DEFAULT_CONFIG,
    *,
    input_dir: str | os.PathLike[str] | None = None,
    output_dir: str | os.PathLike[str] | None = None,
) -> dict[int, Path]:
    """Create the eight selected-and-filtered mission CSV checkpoints."""

    settings = load_settings(config_path)
    source_dir = (
        Path(input_dir)
        if input_dir is not None
        else settings.processed_dir / "quarterly"
    )
    destination_dir = (
        Path(output_dir)
        if output_dir is not None
        else settings.processed_dir / "selected"
    )
    inputs = {
        mission: source_dir / f"goes{mission:02d}_quarterly.csv"
        for mission in settings.selected_missions
    }
    missing = [str(path) for path in inputs.values() if not path.exists()]
    if missing:
        raise FileNotFoundError(
            "Quarterly checkpoints are missing. Run the quarterly aggregation stage first:\n  "
            + "\n  ".join(missing)
        )

    destinations: dict[int, Path] = {}
    records: list[dict[str, Any]] = []
    for mission, source in inputs.items():
        frame = load_quarterly_checkpoint(source)
        selected, mission_records = select_and_filter_mission(
            frame,
            mission,
            min_continuous_years=settings.min_continuous_years,
            short_period_samples=settings.short_period_samples,
            long_period_samples=settings.long_period_samples,
            order=settings.bandpass_order,
        )
        destination = destination_dir / f"goes{mission:02d}_selected.csv"
        _atomic_csv(selected, destination)
        destinations[mission] = destination
        records.extend(mission_records)
        accepted = sum(bool(item["accepted"]) for item in mission_records)
        print(f"GOES-{mission:02d}: selected {accepted}/{len(COMPONENTS)} components")

    summary = pd.DataFrame.from_records(records)
    _atomic_csv(summary, destination_dir / "selection_summary.csv")
    return destinations


def _require_pycwt() -> Any:
    try:
        import pycwt as wavelet
    except ModuleNotFoundError as error:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "PyCWT is required for the wavelet stage. Install the repository's "
            "locked dependencies, then rerun this command."
        ) from error
    return wavelet


def _installed_pycwt_version() -> str:
    """Return the version of the PyCWT implementation used by this process."""

    wavelet = _require_pycwt()
    try:
        return str(importlib_metadata.version("pycwt"))
    except importlib_metadata.PackageNotFoundError:
        version = getattr(wavelet, "__version__", None)
        if version is None:
            raise RuntimeError("The installed PyCWT implementation has no version metadata.")
        return str(version)


def _analysis_source_sha256() -> str:
    """Identify the exact analysis source that produced a checkpoint."""

    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _pycwt_source_sha256() -> str:
    """Identify the exact PyCWT source tree used by this process."""

    wavelet = _require_pycwt()
    package_file = getattr(wavelet, "__file__", None)
    if package_file is None:
        raise RuntimeError("The installed PyCWT implementation has no source location.")
    package_root = Path(package_file).resolve().parent
    sources = sorted(package_root.rglob("*.py"))
    if not sources:
        raise RuntimeError("The installed PyCWT implementation has no Python sources.")
    digest = hashlib.sha256()
    for source in sources:
        digest.update(source.relative_to(package_root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(source.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _wavelet_provenance() -> dict[str, Any]:
    """Return fields that make a wavelet cache valid for this implementation."""

    return {
        "wavelet_cache_schema": WAVELET_CACHE_SCHEMA,
        "wavelet_cache_version": WAVELET_CACHE_VERSION,
        "wavelet_algorithm": WAVELET_ALGORITHM,
        "wavelet_algorithm_version": WAVELET_ALGORITHM_VERSION,
        "analysis_source_sha256": _analysis_source_sha256(),
        "pycwt_version": _installed_pycwt_version(),
        "pycwt_source_sha256": _pycwt_source_sha256(),
        "significance_method": SIGNIFICANCE_METHOD,
    }


def _standardize(data: np.ndarray) -> np.ndarray:
    centered = data - float(np.mean(data))
    scale = float(np.std(centered, ddof=0))
    if not np.isfinite(scale) or scale == 0.0:
        raise ValueError("wavelet input must have nonzero finite variance")
    return centered / scale


def _pycwt_ar1_from_standardized(data: np.ndarray) -> float:
    """Apply the PyCWT AR(1) estimator with the paper calculation's fallback."""

    wavelet = _require_pycwt()
    try:
        alpha, _, _ = wavelet.ar1(data)
    except Exception:
        alpha = 0.0
    if not np.isfinite(alpha):
        alpha = 0.0
    return float(alpha)


def pycwt_ar1_significance(
    *,
    length: int,
    alpha: float,
    dt: float,
    scales: np.ndarray,
    mother: Any,
    confidence: float = 0.95,
) -> tuple[np.ndarray, np.ndarray]:
    """Calculate the local and global AR(1) thresholds used in the paper.

    This is the direct PyCWT ``wavelet.significance`` calculation: local power
    uses ``sigma_test=0`` and global power uses ``sigma_test=1`` with
    ``dof = N - scales``.
    """

    wavelet = _require_pycwt()
    local_by_scale, _ = wavelet.significance(
        1.0,
        dt,
        scales,
        0,
        alpha,
        significance_level=confidence,
        wavelet=mother,
    )
    global_threshold, _ = wavelet.significance(
        1.0,
        dt,
        scales,
        1,
        alpha,
        significance_level=confidence,
        dof=length - scales,
        wavelet=mother,
    )
    local_threshold = np.ones((1, length)) * np.asarray(local_by_scale)[:, None]
    return local_threshold, np.asarray(global_threshold, dtype=float)


def compute_cwt(
    data: Sequence[float],
    time: Sequence[float],
    *,
    confidence: float = 0.95,
    morlet_frequency: float = DEFAULT_MORLET_FREQUENCY,
    dj: float = DEFAULT_DJ,
    j: int = DEFAULT_J,
    smallest_scale_factor: float = DEFAULT_S0_FACTOR,
) -> dict[str, Any]:
    """Calculate the standardized Morlet CWT and its significance products."""

    values = np.asarray(data, dtype=float)
    years = np.asarray(time, dtype=float)
    if values.ndim != 1 or years.shape != values.shape:
        raise ValueError("wavelet data and time must be matching one-dimensional arrays")
    if values.size < 4 or not np.isfinite(values).all() or not np.isfinite(years).all():
        raise ValueError("wavelet input needs at least four finite samples")
    steps = np.diff(years)
    if np.any(steps <= 0):
        raise ValueError("wavelet times must be strictly increasing")
    dt = float(np.mean(steps))
    standardized = _standardize(values)

    wavelet = _require_pycwt()
    mother = wavelet.Morlet(morlet_frequency)
    s0 = smallest_scale_factor * dt
    coefficients, scales, frequencies, coi, _, _ = wavelet.cwt(
        standardized, dt, dj, s0, j, mother
    )
    power = np.square(np.abs(coefficients))
    period = 1.0 / frequencies
    global_power = power.mean(axis=1)
    alpha = _pycwt_ar1_from_standardized(standardized)
    local_threshold, global_threshold = pycwt_ar1_significance(
        length=values.size,
        alpha=alpha,
        dt=dt,
        scales=scales,
        mother=mother,
        confidence=confidence,
    )

    with np.errstate(divide="ignore", invalid="ignore"):
        local_ratio = power / local_threshold
    result = {
        "time": years,
        "period": period,
        "scales": scales,
        "coi": np.asarray(coi, dtype=float),
        "power": power,
        "local_threshold": local_threshold,
        "local_ratio": local_ratio,
        "global_power": global_power,
        "global_threshold": global_threshold,
        "dt": dt,
        "ar1": alpha,
        "confidence": confidence,
        "significance_method": SIGNIFICANCE_METHOD,
        "morlet_frequency": morlet_frequency,
        "dj": dj,
        "j": j,
        "smallest_scale_factor": smallest_scale_factor,
    }
    result.update(_wavelet_provenance())
    return result


def _array_digest(time: np.ndarray, data: np.ndarray) -> str:
    digest = hashlib.sha256()
    digest.update(np.asarray(time, dtype="<f8").tobytes())
    digest.update(np.asarray(data, dtype="<f8").tobytes())
    return digest.hexdigest()


def _npz_scalar(archive: Mapping[str, Any], key: str) -> Any:
    value = archive[key]
    return value.item() if np.asarray(value).ndim == 0 else value


def _wavelet_is_current(path: Path, metadata: Mapping[str, Any]) -> bool:
    if not path.exists():
        return False
    try:
        with np.load(path, allow_pickle=False) as archive:
            return all(str(_npz_scalar(archive, key)) == str(value) for key, value in metadata.items())
    except (OSError, KeyError, ValueError):
        return False


def save_wavelet_result(
    path: str | os.PathLike[str],
    result: Mapping[str, Any],
    *,
    input_sha256: str,
) -> Path:
    """Write a complete, non-pickle wavelet checkpoint atomically."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(result)
    payload["input_sha256"] = input_sha256
    for key, value in _wavelet_provenance().items():
        payload.setdefault(key, value)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=destination.stem + ".", suffix=".npz", dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        np.savez_compressed(temporary, **payload)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def load_sunspot_monthly(path: str | os.PathLike[str]) -> pd.DataFrame:
    """Read the SILSO monthly total sunspot-number text format."""

    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(source)
    data = pd.read_csv(source, sep=r"\s+", header=None, comment="#")
    if data.shape[1] < 4:
        raise ValueError(f"{source} does not contain the SILSO monthly columns")
    result = pd.DataFrame(
        {
            "year": pd.to_numeric(data.iloc[:, 0], errors="raise").astype(int),
            "month": pd.to_numeric(data.iloc[:, 1], errors="raise").astype(int),
            "decyear": pd.to_numeric(data.iloc[:, 2], errors="raise"),
            "ssn": pd.to_numeric(data.iloc[:, 3], errors="coerce"),
        }
    )
    result.loc[result["ssn"] < 0, "ssn"] = np.nan
    result["time"] = pd.to_datetime(
        {"year": result["year"], "month": result["month"], "day": 1}
    ) + pd.offsets.MonthEnd(0)
    return result


def quarterly_sunspots(monthly: pd.DataFrame) -> pd.DataFrame:
    """Aggregate monthly SILSO values to the three-month analysis grid."""

    indexed = monthly.set_index("time")[["ssn"]]
    try:
        aggregate = indexed.resample("3ME").agg(["mean", "std"])
    except ValueError:  # pandas < 2.2
        aggregate = indexed.resample("3M").agg(["mean", "std"])
    aggregate.columns = ["ssn_mean", "ssn_std"]
    result = aggregate.reset_index()
    result["decyear"] = decimal_year(result["time"])
    return result


def find_sunspot_file(source_dir: str | os.PathLike[str]) -> Path:
    source = Path(source_dir)
    candidates = (
        source / "SN_m_tot_V2.0.txt",
        source / "sunspots" / "SN_m_tot_V2.0.txt",
        source / "silso" / "SN_m_tot_V2.0.txt",
        source / "SILSO" / "SN_m_tot_V2.0.txt",
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "SILSO input is missing. Download SN_m_tot_V2.0.txt and place it at "
        f"{candidates[0]} (or {candidates[1]})."
    )


def _selected_component(frame: pd.DataFrame, component: str) -> tuple[np.ndarray, np.ndarray]:
    column = f"chosen_{component}_bp"
    if column not in frame:
        raise ValueError(f"selected checkpoint has no {column} column")
    mask = frame[column].notna()
    if not mask.any():
        raise ValueError(f"selected checkpoint contains no accepted {component} interval")
    return (
        frame.loc[mask, "decyear"].to_numpy(dtype=float),
        frame.loc[mask, column].to_numpy(dtype=float),
    )


def _calculate_or_reuse(
    *,
    output: Path,
    time: np.ndarray,
    data: np.ndarray,
    label: str,
    settings: AnalysisSettings,
    force: bool,
) -> Path:
    input_hash = _array_digest(time, data)
    metadata = {
        "input_sha256": input_hash,
        "significance_method": SIGNIFICANCE_METHOD,
        "confidence": settings.confidence,
        "morlet_frequency": settings.morlet_frequency,
        "dj": settings.scale_resolution,
        "j": settings.scale_count,
        "smallest_scale_factor": settings.smallest_scale_factor,
    }
    metadata.update(_wavelet_provenance())
    if not force and _wavelet_is_current(output, metadata):
        print(f"{label}: current checkpoint exists; skipping")
        return output
    result = compute_cwt(
        data,
        time,
        confidence=settings.confidence,
        morlet_frequency=settings.morlet_frequency,
        dj=settings.scale_resolution,
        j=settings.scale_count,
        smallest_scale_factor=settings.smallest_scale_factor,
    )
    return save_wavelet_result(output, result, input_sha256=input_hash)


def run_wavelet_analysis(
    config_path: str | os.PathLike[str] = DEFAULT_CONFIG,
    *,
    processed_dir: str | os.PathLike[str] | None = None,
    source_dir: str | os.PathLike[str] | None = None,
    output_dir: str | os.PathLike[str] | None = None,
    force: bool = False,
) -> list[Path]:
    """Calculate every GOES and sunspot CWT checkpoint used in Figures 3–6."""

    settings = load_settings(config_path)
    processed_root = (
        Path(processed_dir) if processed_dir is not None else settings.processed_dir
    )
    processed = processed_root / "selected"
    source = Path(source_dir) if source_dir is not None else settings.source_dir
    destination = (
        Path(output_dir) if output_dir is not None else settings.work_dir / "wavelets"
    )
    selected_paths = {
        mission: processed / f"goes{mission:02d}_selected.csv"
        for mission in settings.selected_missions
    }
    missing = [str(path) for path in selected_paths.values() if not path.exists()]
    if missing:
        raise FileNotFoundError(
            "Selected checkpoints are missing. Run analyze.py select-filter first:\n  "
            + "\n  ".join(missing)
        )
    selected = {
        mission: _normalise_time_column(pd.read_csv(path), path)
        for mission, path in selected_paths.items()
    }
    for frame in selected.values():
        if "decyear" not in frame:
            frame["decyear"] = decimal_year(frame["time"])

    outputs: list[Path] = []
    for mission in settings.selected_missions:
        for component in COMPONENTS:
            time, data = _selected_component(selected[mission], component)
            label = f"GOES-{mission:02d} {component}"
            outputs.append(
                _calculate_or_reuse(
                    output=destination / f"wavelet_goes{mission:02d}_{component}.npz",
                    time=time,
                    data=data,
                    label=label,
                    settings=settings,
                    force=force,
                )
            )

    monthly = load_sunspot_monthly(find_sunspot_file(source))
    sunspots = quarterly_sunspots(monthly)
    valid = sunspots["ssn_mean"].notna()
    if not valid.all():
        raise ValueError("SILSO quarterly means contain gaps; fill or document them before CWT")
    sunspots["ssn_bp"] = difference_of_lowpasses(
        sunspots["ssn_mean"].to_numpy(dtype=float),
        sunspots["decyear"].to_numpy(dtype=float),
        short_period_samples=settings.short_period_samples,
        long_period_samples=settings.long_period_samples,
        order=settings.bandpass_order,
    )
    _atomic_csv(sunspots, processed / "sunspots_quarterly.csv")

    span_specs: tuple[tuple[str, int, int], ...] = (
        ("goes06_07", 6, 7),
        ("goes08", 8, 8),
        ("goes10", 10, 10),
        ("goes12", 12, 12),
        ("goes13_15", 13, 13),
        ("goes17", 17, 17),
    )
    sunspot_series: list[tuple[str, pd.DataFrame]] = [("full", sunspots)]
    for name, first_mission, last_mission in span_specs:
        first_time, _ = _selected_component(selected[first_mission], "b_m")
        last_time, _ = _selected_component(selected[last_mission], "b_m")
        subset = sunspots.loc[
            (sunspots["decyear"] >= first_time[0])
            & (sunspots["decyear"] <= last_time[-1])
        ]
        if subset.empty:
            raise ValueError(f"SILSO data do not overlap the {name} interval")
        sunspot_series.append((name, subset))

    for name, frame in sunspot_series:
        time = frame["decyear"].to_numpy(dtype=float)
        data = frame["ssn_bp"].to_numpy(dtype=float)
        label = f"sunspots {name}"
        outputs.append(
            _calculate_or_reuse(
                output=destination / f"wavelet_sunspots_{name}.npz",
                time=time,
                data=data,
                label=label,
                settings=settings,
                force=force,
            )
        )
    return outputs


def run_analysis(
    config_path: str | os.PathLike[str] = DEFAULT_CONFIG,
    **wavelet_options: Any,
) -> list[Path]:
    """Run interval selection/filtering and then all wavelet calculations."""

    run_selection(config_path)
    return run_wavelet_analysis(config_path, **wavelet_options)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "stage",
        choices=("select-filter", "wavelets", "all"),
        help="analysis stage to run",
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--processed-dir", type=Path)
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--force", action="store_true", help="recalculate current wavelet checkpoints")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.stage in {"select-filter", "all"}:
        selection_input = (
            args.processed_dir / "quarterly" if args.processed_dir is not None else None
        )
        selection_output = (
            args.processed_dir / "selected" if args.processed_dir is not None else None
        )
        run_selection(
            args.config,
            input_dir=selection_input,
            output_dir=selection_output,
        )
    if args.stage in {"wavelets", "all"}:
        run_wavelet_analysis(
            args.config,
            processed_dir=args.processed_dir,
            source_dir=args.source_dir,
            output_dir=args.output_dir,
            force=args.force,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
