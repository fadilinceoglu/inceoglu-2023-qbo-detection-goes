#!/usr/bin/env python3
"""Prepare canonical GOES minute series and three-month checkpoints.

The script provides two resumable preparation operations:

``minute``
    Reduce high-resolution GSM NetCDF products to one-minute values, convert
    legacy EPN products to GSM with cached TLEs, apply the paper's source-wise
    four-sigma cleaning and mission exclusions, and write one neutral CSV per
    mission under ``data/processed/minute``.
``quarterly``
    Calculate the paper's three-month mean and sample-standard-deviation
    checkpoints under ``data/processed/quarterly``.  Component counts used for
    the coverage decision are retained in ``quarterly_counts`` sidecars.
``all``
    Run both operations, reusing complete outputs and per-NetCDF work chunks.

The complete minute calculation is data intensive.  No work begins merely by
importing this module.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import math
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from collections.abc import Iterable, Iterator, Sequence

import numpy as np
import pandas as pd

try:  # Executed as ``python scripts/prepare_data.py``.
    from coordinates import eci_to_gsm, epn_to_eci, propagate_tle
    from file_integrity import manifest_checksum as _manifest_checksum
    from file_integrity import sha256_file as _sha256
except ImportError:  # Imported as ``scripts.prepare_data``.
    from scripts.coordinates import eci_to_gsm, epn_to_eci, propagate_tle
    from scripts.file_integrity import manifest_checksum as _manifest_checksum
    from scripts.file_integrity import sha256_file as _sha256


CANONICAL_COLUMNS = ["time", "b_g", "b_s", "b_m"]
FIELD_COLUMNS = ["b_g", "b_s", "b_m"]
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
RELEASED_QUARTERLY_DIR = REPOSITORY_ROOT / "data" / "processed" / "quarterly"

# This is the exact source choice in the paper calculation.  For the five
# stacked missions, both source lineages contribute and duplicate timestamps
# remain separate observations in the three-month statistics.
PUBLISHED_SOURCES: dict[int, tuple[str, ...]] = {
    1: ("legacy",),
    2: ("legacy",),
    3: ("legacy",),
    5: ("legacy",),
    6: ("legacy",),
    7: ("legacy",),
    8: ("high_resolution", "legacy"),
    9: ("high_resolution",),
    10: ("high_resolution", "legacy"),
    11: ("high_resolution", "legacy"),
    12: ("high_resolution", "legacy"),
    13: ("high_resolution",),
    14: ("high_resolution", "legacy"),
    15: ("high_resolution",),
    16: ("high_resolution",),
    17: ("high_resolution",),
}


@dataclass(frozen=True)
class TLE:
    epoch: pd.Timestamp
    line1: str
    line2: str


@dataclass
class RunningMoments:
    """Mergeable count/sum/sum-of-squares statistics for three components."""

    count: np.ndarray
    total: np.ndarray
    total_squares: np.ndarray

    @classmethod
    def empty(cls) -> "RunningMoments":
        return cls(
            np.zeros(3, dtype=np.int64),
            np.zeros(3, dtype=np.float64),
            np.zeros(3, dtype=np.float64),
        )

    def update(self, values: np.ndarray) -> None:
        array = np.asarray(values, dtype=float)
        for column in range(3):
            finite = array[np.isfinite(array[:, column]), column]
            self.count[column] += finite.size
            self.total[column] += finite.sum(dtype=np.float64)
            self.total_squares[column] += np.square(finite).sum(dtype=np.float64)

    def mean(self) -> np.ndarray:
        return np.divide(
            self.total,
            self.count,
            out=np.full(3, np.nan, dtype=float),
            where=self.count > 0,
        )

    def standard_deviation(self) -> np.ndarray:
        numerator = self.total_squares - np.divide(
            self.total**2,
            self.count,
            out=np.zeros(3, dtype=float),
            where=self.count > 0,
        )
        variance = np.divide(
            np.maximum(numerator, 0.0),
            self.count - 1,
            out=np.full(3, np.nan, dtype=float),
            where=self.count > 1,
        )
        return np.sqrt(variance)


def _load_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib  # type: ignore[no-redef]
    with path.open("rb") as stream:
        return tomllib.load(stream)


def _nested(config: dict[str, Any], *keys: str, default: Any = None) -> Any:
    value: Any = config
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            return default
        value = value[key]
    return value


def _verify_released_quarterly_checkpoint(target: Path) -> None:
    """Verify a released checkpoint before treating it as a cache hit."""

    manifest = target.parent / "SHA256SUMS"
    is_repository_release = target.parent.resolve() == RELEASED_QUARTERLY_DIR.resolve()
    if not manifest.exists() and not is_repository_release:
        # A user-generated checkpoint outside the released directory has no
        # release checksum contract; its normal cache behavior is unchanged.
        return
    restore = (
        f"git restore -- data/processed/quarterly/{target.name} "
        "data/processed/quarterly/SHA256SUMS"
    )
    try:
        expected = _manifest_checksum(manifest, target.name)
    except (OSError, RuntimeError) as error:
        raise RuntimeError(
            f"Cannot verify released quarterly checkpoint {target}: {error}. "
            f"Restore the checkpoint and checksum manifest with `{restore}`, "
            "then rerun the command."
        ) from error
    actual = _sha256(target)
    if actual != expected:
        raise RuntimeError(
            f"Released quarterly checkpoint failed SHA-256 verification: {target}. "
            f"Expected {expected}, found {actual}. Restore the released checkpoint "
            f"with `{restore}`, then rerun the command."
        )


def _parse_missions(value: str | Sequence[int]) -> list[int]:
    if not isinstance(value, str):
        missions = [int(item) for item in value]
    else:
        missions: list[int] = []
        for token in value.split(","):
            token = token.strip()
            if not token:
                continue
            if "-" in token:
                first_text, last_text = token.split("-", 1)
                first, last = int(first_text), int(last_text)
                if first > last:
                    raise ValueError(f"invalid descending mission range: {token}")
                missions.extend(range(first, last + 1))
            else:
                missions.append(int(token))
    result = sorted(set(missions))
    unsupported = [mission for mission in result if mission not in PUBLISHED_SOURCES]
    if unsupported:
        raise ValueError(
            f"GOES missions {unsupported} are not used by the paper preparation stage"
        )
    return result


def _logical_product(path: Path) -> str:
    return re.sub(
        r"_v\d+(?:[-.]\d+)*(?=\.nc$)", "_v", path.name, flags=re.IGNORECASE
    )


def _netcdf_revision(path: Path) -> tuple[int, ...]:
    """Return the numeric components of a terminal NCEI NetCDF revision."""

    match = re.search(
        r"_v(\d+(?:[-.]\d+)*)(?=\.nc$)", path.name, flags=re.IGNORECASE
    )
    if match is None:
        return ()
    return tuple(int(component) for component in re.split(r"[-.]", match.group(1)))


def _deduplicate_netcdf(paths: Iterable[Path]) -> list[Path]:
    selected: dict[str, tuple[tuple[int, ...], str, Path]] = {}
    for path in paths:
        logical_product = _logical_product(path)
        candidate = (_netcdf_revision(path), path.name, path)
        current = selected.get(logical_product)
        if current is None or candidate[:2] > current[:2]:
            selected[logical_product] = candidate
    return sorted(path for _, _, path in selected.values())


def _file_date(path: Path) -> pd.Timestamp | None:
    for token in re.findall(r"(?<!\d)(\d{8})(?!\d)", path.name):
        try:
            return pd.Timestamp(datetime.strptime(token, "%Y%m%d"))
        except ValueError:
            continue
    return None


def _netcdf_values(variable: Any) -> np.ndarray:
    values = np.asarray(np.ma.filled(variable[:], np.nan), dtype=float)
    for attribute in ("missing_value", "_FillValue"):
        if hasattr(variable, attribute):
            missing = np.asarray(getattr(variable, attribute)).reshape(-1)
            for item in missing:
                values[values == float(item)] = np.nan
    return values


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "time": pd.Series(dtype="datetime64[ns]"),
            "b_g": pd.Series(dtype=float),
            "b_s": pd.Series(dtype=float),
            "b_m": pd.Series(dtype=float),
        }
    )


def read_high_resolution(path: Path) -> pd.DataFrame:
    """Read one Level-2 file and form canonical one-minute GSM values."""

    try:
        from netCDF4 import Dataset
    except ImportError as exc:  # pragma: no cover - runtime dependency
        raise RuntimeError("NetCDF preparation requires the 'netCDF4' package") from exc

    with Dataset(path) as dataset:
        seconds = _netcdf_values(dataset.variables["time"])
        vectors = _netcdf_values(dataset.variables["b_gsm"])
    if vectors.ndim != 2 or vectors.shape[1] < 3:
        raise ValueError(f"{path} has an unexpected b_gsm shape: {vectors.shape}")
    times = pd.Timestamp("2000-01-01T12:00:00") + pd.to_timedelta(seconds, unit="s")
    frame = pd.DataFrame(
        {"time": times, "b_g": vectors[:, 0], "b_s": vectors[:, 1], "b_m": vectors[:, 2]}
    )
    nominal_date = _file_date(path)
    if nominal_date is not None:
        frame = frame.loc[frame["time"].dt.normalize() == nominal_date]
    frame.loc[:, FIELD_COLUMNS] = frame[FIELD_COLUMNS].where(
        frame[FIELD_COLUMNS].between(-1024.0, 1024.0)
    )
    if frame.empty:
        return _empty_frame()
    return (
        frame.sort_values("time", kind="stable")
        .set_index("time")[FIELD_COLUMNS]
        .resample("1min")
        .mean()
        .reset_index()
    )


def _tle_epoch(line1: str) -> pd.Timestamp:
    token = line1[18:32]
    year_short = int(token[:2])
    year = 1900 + year_short if year_short > 50 else 2000 + year_short
    day_of_year = float(token[2:])
    return pd.Timestamp(year=year, month=1, day=1) + pd.to_timedelta(
        day_of_year - 1.0, unit="D"
    )


def read_tles(path: Path) -> list[TLE]:
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(lines) % 2:
        raise ValueError(f"{path} contains an unmatched TLE line")
    result: list[TLE] = []
    seen_dates: set[pd.Timestamp] = set()
    for index in range(0, len(lines), 2):
        line1, line2 = lines[index : index + 2]
        if not line1.startswith("1 ") or not line2.startswith("2 "):
            raise ValueError(f"{path} is not a sequence of two-line element pairs")
        epoch = _tle_epoch(line1)
        epoch_date = epoch.normalize()
        # The paper calculation retained the first element set for each epoch day.
        if epoch_date not in seen_dates:
            result.append(TLE(epoch=epoch, line1=line1, line2=line2))
            seen_dates.add(epoch_date)
    return sorted(result, key=lambda item: item.epoch)


def _nearest_tle(entries: Sequence[TLE], day: pd.Timestamp, maximum_days: float) -> TLE | None:
    if not entries:
        return None
    selected = min(
        entries,
        key=lambda item: (abs(item.epoch.normalize() - day), item.epoch),
    )
    distance = abs((selected.epoch.normalize() - day).total_seconds()) / 86400.0
    return selected if distance <= maximum_days else None


def read_legacy(
    path: Path,
    tles: Sequence[TLE],
    *,
    maximum_tle_distance_days: float,
) -> pd.DataFrame:
    """Read one one-minute EPN file and rotate it into canonical GSM columns."""

    try:
        from netCDF4 import Dataset
    except ImportError as exc:  # pragma: no cover - runtime dependency
        raise RuntimeError("NetCDF preparation requires the 'netCDF4' package") from exc

    with Dataset(path) as dataset:
        time_ms = _netcdf_values(dataset.variables["time_tag"])
        if all(name in dataset.variables for name in ("HE_1", "HP_1", "HN_1")):
            names = ("HE_1", "HP_1", "HN_1")
        else:
            names = ("he", "hp", "hn")
        epn = np.column_stack([_netcdf_values(dataset.variables[name]) for name in names])

    times = pd.to_datetime(time_ms, unit="ms", origin="unix", errors="coerce")
    source = pd.DataFrame(
        {"time": times, "b_e": epn[:, 0], "b_p": epn[:, 1], "b_n": epn[:, 2]}
    ).dropna(subset=["time"])
    nominal_date = _file_date(path)
    if nominal_date is not None and source["time"].dt.normalize().nunique() <= 2:
        source = source.loc[source["time"].dt.normalize() == nominal_date]
    if source.empty:
        return _empty_frame()

    results: list[pd.DataFrame] = []
    for day, daily in source.groupby(source["time"].dt.normalize(), sort=True):
        daily = daily.sort_values("time", kind="stable").reset_index(drop=True)
        tle = _nearest_tle(tles, pd.Timestamp(day), maximum_tle_distance_days)
        if tle is None:
            missing = daily[["time"]].copy()
            missing[FIELD_COLUMNS] = np.nan
            results.append(missing)
            continue
        positions, inclinations, raans = propagate_tle(tle.line1, tle.line2, daily["time"])
        eci = epn_to_eci(
            daily[["b_e", "b_p", "b_n"]].to_numpy(dtype=float),
            positions,
            inclinations,
            raans,
        )
        gsm = eci_to_gsm(daily["time"].tolist(), eci)
        results.append(
            pd.DataFrame(
                {
                    "time": daily["time"].to_numpy(),
                    "b_g": gsm[:, 0],
                    "b_s": gsm[:, 1],
                    "b_m": gsm[:, 2],
                }
            )
        )
    return pd.concat(results, ignore_index=True)[CANONICAL_COLUMNS]


def _write_frame(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    frame.to_csv(
        temporary,
        index=False,
        compression="gzip" if path.suffix == ".gz" else None,
        date_format="%Y-%m-%dT%H:%M:%S",
    )
    temporary.replace(path)


def _chunk_target(work_root: Path, source: str, mission: int, raw: Path) -> Path:
    logical = Path(_logical_product(raw)).stem
    return work_root / source / f"goes{mission:02d}" / f"{logical}.csv.gz"


def extract_high_resolution(
    source_root: Path,
    work_root: Path,
    missions: Sequence[int],
    *,
    force: bool,
) -> None:
    for mission in missions:
        if "high_resolution" not in PUBLISHED_SOURCES[mission]:
            continue
        raw_files = _deduplicate_netcdf(
            (source_root / "high_resolution" / f"goes{mission:02d}").glob("*.nc")
        )
        if not raw_files:
            raise FileNotFoundError(
                f"no high-resolution NetCDF files found for GOES-{mission:02d} under {source_root}"
            )
        for index, raw in enumerate(raw_files, 1):
            target = _chunk_target(work_root, "high_resolution", mission, raw)
            if target.exists() and target.stat().st_size > 0 and not force:
                continue
            frame = read_high_resolution(raw)
            _write_frame(target, frame)
            print(
                f"[highres {index}/{len(raw_files)}] GOES-{mission:02d} "
                f"{raw.name}: {len(frame)} minute row(s)"
            )


def extract_legacy(
    source_root: Path,
    work_root: Path,
    missions: Sequence[int],
    *,
    maximum_tle_distance_days: float,
    force: bool,
) -> None:
    for mission in missions:
        if "legacy" not in PUBLISHED_SOURCES[mission]:
            continue
        raw_files = _deduplicate_netcdf(
            (source_root / "legacy" / f"goes{mission:02d}").glob("*.nc")
        )
        if not raw_files:
            raise FileNotFoundError(
                f"no legacy NetCDF files found for GOES-{mission:02d} under {source_root}"
            )
        tle_path = source_root / "tle" / f"goes{mission:02d}.tle"
        if not tle_path.exists():
            raise FileNotFoundError(
                f"missing {tle_path}; run scripts/acquire_data.py tle first"
            )
        tles = read_tles(tle_path)
        for index, raw in enumerate(raw_files, 1):
            target = _chunk_target(work_root, "legacy", mission, raw)
            if target.exists() and target.stat().st_size > 0 and not force:
                continue
            frame = read_legacy(
                raw,
                tles,
                maximum_tle_distance_days=maximum_tle_distance_days,
            )
            _write_frame(target, frame)
            print(
                f"[legacy {index}/{len(raw_files)}] GOES-{mission:02d} "
                f"{raw.name}: {len(frame)} minute row(s)"
            )


def _source_chunks(work_root: Path, source: str, mission: int) -> list[Path]:
    return sorted((work_root / source / f"goes{mission:02d}").glob("*.csv.gz"))


def _read_chunks(paths: Sequence[Path], *, chunksize: int = 250_000) -> Iterator[pd.DataFrame]:
    for path in paths:
        for frame in pd.read_csv(path, parse_dates=["time"], chunksize=chunksize):
            yield frame[CANONICAL_COLUMNS]


def _moments(paths: Sequence[Path], upper: np.ndarray | None = None) -> RunningMoments:
    result = RunningMoments.empty()
    for frame in _read_chunks(paths):
        values = frame[FIELD_COLUMNS].to_numpy(dtype=float)
        if upper is not None:
            values[values > upper] = np.nan
        result.update(values)
    return result


def source_limits(
    paths: Sequence[Path],
    sigma: float,
    mode: str,
) -> tuple[np.ndarray, np.ndarray]:
    """Calculate the source-wise limits used before mission-source stacking."""

    initial = _moments(paths)
    high = initial.mean() + sigma * initial.standard_deviation()
    if mode == "published-sequential":
        upper_clipped = _moments(paths, upper=high)
        low = upper_clipped.mean() - sigma * upper_clipped.standard_deviation()
    elif mode == "symmetric":
        low = initial.mean() - sigma * initial.standard_deviation()
    else:
        raise ValueError(f"unknown outlier mode: {mode}")
    return low, high


def _apply_exclusions(frame: pd.DataFrame, mission: int, source: str) -> pd.DataFrame:
    time_values = frame["time"]
    keep = pd.Series(True, index=frame.index)
    if mission == 5:
        keep &= ~((time_values >= "1986-01-01") & (time_values < "1986-03-13"))
    elif mission == 11 and source == "legacy":
        keep &= time_values > "2004-01-01"
    elif mission == 15:
        for start, end in (
            ("2015-11-10", "2015-11-13"),
            ("2016-09-06", "2016-09-10"),
            ("2016-09-29", "2016-10-07"),
            ("2016-10-18", "2016-10-20"),
            ("2017-09-05", "2017-09-09"),
        ):
            keep &= ~((time_values >= start) & (time_values <= end))
    elif mission == 16:
        keep &= time_values > "2017-04-12"
    elif mission == 17:
        keep &= ~((time_values >= "2021-11-03") & (time_values < "2021-11-04"))
    return frame.loc[keep].copy()


def _clean_source(
    paths: Sequence[Path],
    mission: int,
    source: str,
    sigma: float,
    outlier_mode: str,
) -> Iterator[pd.DataFrame]:
    low, high = source_limits(paths, sigma, outlier_mode)
    for frame in _read_chunks(paths):
        values = frame[FIELD_COLUMNS].to_numpy(dtype=float)
        values[(values > high) | (values < low)] = np.nan
        frame.loc[:, FIELD_COLUMNS] = values
        yield _apply_exclusions(frame, mission, source)


def _sqlite_insert(
    connection: sqlite3.Connection,
    frame: pd.DataFrame,
    sequence: int,
    *,
    replace: bool,
) -> int:
    rows = frame.sort_values("time", kind="stable")
    statement = (
        "INSERT OR REPLACE INTO observations(time,b_g,b_s,b_m,sequence) VALUES (?,?,?,?,?)"
        if replace
        else "INSERT INTO observations(time,b_g,b_s,b_m,sequence) VALUES (?,?,?,?,?)"
    )

    def values() -> Iterator[tuple[str, float | None, float | None, float | None, int]]:
        nonlocal sequence
        for row in rows.itertuples(index=False):
            sequence += 1
            fields = [None if not np.isfinite(float(value)) else float(value) for value in row[1:4]]
            yield (pd.Timestamp(row.time).isoformat(), fields[0], fields[1], fields[2], sequence)

    connection.executemany(statement, values())
    return sequence


def merge_minute_mission(
    processed_root: Path,
    work_root: Path,
    mission: int,
    *,
    sigma: float,
    outlier_mode: str,
    merge_policy: str,
    force: bool,
) -> Path:
    target = processed_root / "minute" / f"goes{mission:02d}.csv.gz"
    if target.exists() and target.stat().st_size > 0 and not force:
        print(f"[cached] {target}")
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    database = work_root / f"goes{mission:02d}_merge.sqlite3"
    database.parent.mkdir(parents=True, exist_ok=True)
    if database.exists():
        database.unlink()
    connection = sqlite3.connect(database)
    try:
        if merge_policy == "prefer-high-resolution":
            connection.execute(
                "CREATE TABLE observations ("
                "time TEXT PRIMARY KEY, b_g REAL, b_s REAL, b_m REAL, sequence INTEGER)"
            )
            sources = tuple(reversed(PUBLISHED_SOURCES[mission]))
            replace = True
        elif merge_policy == "published-stack":
            connection.execute(
                "CREATE TABLE observations ("
                "time TEXT, b_g REAL, b_s REAL, b_m REAL, sequence INTEGER PRIMARY KEY)"
            )
            sources = PUBLISHED_SOURCES[mission]
            replace = False
        else:
            raise ValueError(f"unknown merge policy: {merge_policy}")

        sequence = 0
        for source in sources:
            paths = _source_chunks(work_root, source, mission)
            if not paths:
                raise FileNotFoundError(
                    f"no prepared {source} chunks for GOES-{mission:02d}; run the minute stage"
                )
            for frame in _clean_source(paths, mission, source, sigma, outlier_mode):
                sequence = _sqlite_insert(
                    connection, frame, sequence, replace=replace
                )
                connection.commit()
        connection.execute("CREATE INDEX observations_time ON observations(time, sequence)")
        connection.commit()

        temporary = target.with_name(target.name + ".tmp")
        with gzip.open(temporary, "wt", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream, lineterminator="\n")
            writer.writerow(CANONICAL_COLUMNS)
            cursor = connection.execute(
                "SELECT time,b_g,b_s,b_m FROM observations ORDER BY time,sequence"
            )
            while True:
                rows = cursor.fetchmany(100_000)
                if not rows:
                    break
                writer.writerows(rows)
        temporary.replace(target)
    finally:
        connection.close()
        if database.exists():
            database.unlink()
    print(f"[written] {target}")
    return target


def prepare_minute(
    source_root: Path,
    processed_root: Path,
    missions: Sequence[int],
    *,
    sigma: float,
    outlier_mode: str,
    merge_policy: str,
    maximum_tle_distance_days: float,
    force: bool,
) -> None:
    work_root = processed_root / ".work" / "minute"
    needed = [
        mission
        for mission in missions
        if force
        or not (processed_root / "minute" / f"goes{mission:02d}.csv.gz").exists()
    ]
    if not needed:
        print("[cached] all requested minute checkpoints")
        return
    extract_high_resolution(source_root, work_root, needed, force=force)
    extract_legacy(
        source_root,
        work_root,
        needed,
        maximum_tle_distance_days=maximum_tle_distance_days,
        force=force,
    )
    for mission in needed:
        merge_minute_mission(
            processed_root,
            work_root,
            mission,
            sigma=sigma,
            outlier_mode=outlier_mode,
            merge_policy=merge_policy,
            force=force,
        )


def _month_index(times: pd.Series) -> np.ndarray:
    return times.dt.year.to_numpy(dtype=np.int64) * 12 + (
        times.dt.month.to_numpy(dtype=np.int64) - 1
    )


def _bin_labels(times: pd.Series, anchor: pd.Timestamp, months: int) -> pd.DatetimeIndex:
    indices = _month_index(times)
    anchor_index = anchor.year * 12 + anchor.month - 1
    delta = indices - anchor_index
    if np.any(delta < 0):
        raise ValueError("quarterly bin anchor is later than an observation")
    steps = np.where(delta == 0, 0, (delta + months - 1) // months)
    end_indices = anchor_index + steps * months
    years = end_indices // 12
    month_numbers = end_indices % 12 + 1
    starts = pd.to_datetime({"year": years, "month": month_numbers, "day": 1})
    return pd.DatetimeIndex(starts + pd.offsets.MonthEnd(0))


def _minimum_time(path: Path) -> pd.Timestamp | None:
    minimum: pd.Timestamp | None = None
    for frame in pd.read_csv(path, usecols=["time"], parse_dates=["time"], chunksize=500_000):
        candidate = frame["time"].min()
        if pd.notna(candidate) and (minimum is None or candidate < minimum):
            minimum = pd.Timestamp(candidate)
    return minimum


def _quarterly_moments(
    path: Path,
    anchor: pd.Timestamp,
    months: int,
) -> dict[pd.Timestamp, RunningMoments]:
    bins: dict[pd.Timestamp, RunningMoments] = {}
    for frame in pd.read_csv(path, parse_dates=["time"], chunksize=500_000):
        frame["_bin"] = _bin_labels(frame["time"], anchor, months)
        for label, group in frame.groupby("_bin", sort=True):
            key = pd.Timestamp(label)
            bins.setdefault(key, RunningMoments.empty()).update(
                group[FIELD_COLUMNS].to_numpy(dtype=float)
            )
    return bins


def _fractional_required(label: pd.Timestamp, months: int, fraction: float) -> int:
    previous = label - pd.DateOffset(months=months)
    previous = previous + pd.offsets.MonthEnd(0)
    full_bin_minutes = int((label - previous).total_seconds() // 60)
    return math.ceil(full_bin_minutes * fraction)


def prepare_quarterly_mission(
    processed_root: Path,
    mission: int,
    *,
    months: int,
    coverage_fraction: float,
    fixed_minimum_count: int,
    coverage_mode: str,
    force: bool,
) -> Path:
    source = processed_root / "minute" / f"goes{mission:02d}.csv.gz"
    target = processed_root / "quarterly" / f"goes{mission:02d}_quarterly.csv"
    count_target = (
        processed_root / "quarterly_counts" / f"goes{mission:02d}_counts.csv"
    )
    if target.exists() and target.stat().st_size > 0 and not force:
        _verify_released_quarterly_checkpoint(target)
        print(f"[cached] {target}")
        return target
    if not source.exists():
        raise FileNotFoundError(f"missing {source}; run the minute stage first")

    anchor = _minimum_time(source)
    output_columns = ["time", "b_g", "b_g_std", "b_s", "b_s_std", "b_m", "b_m_std"]
    count_columns = ["time", "b_g_count", "b_s_count", "b_m_count", "required_count"]
    if anchor is None:
        _write_frame(target, pd.DataFrame(columns=output_columns))
        _write_frame(count_target, pd.DataFrame(columns=count_columns))
        return target
    bins = _quarterly_moments(source, anchor, months)

    # ``resample('3M')`` emits explicit empty bins between the first and last
    # observations.  Preserve those missing rows because downstream continuity
    # selection must see gaps rather than bridge across them.
    first_label = anchor + pd.offsets.MonthEnd(0)
    last_label = max(bins)
    label = pd.Timestamp(first_label)
    while label <= last_label:
        bins.setdefault(label, RunningMoments.empty())
        label = label + pd.DateOffset(months=months)
        label = label + pd.offsets.MonthEnd(0)

    rows: list[dict[str, Any]] = []
    count_rows: list[dict[str, Any]] = []
    for label in sorted(bins):
        moments = bins[label]
        mean = moments.mean()
        standard_deviation = moments.standard_deviation()
        if coverage_mode == "published-fixed":
            required = fixed_minimum_count
        elif coverage_mode == "calendar-fraction":
            required = _fractional_required(label, months, coverage_fraction)
        else:
            raise ValueError(f"unknown coverage mode: {coverage_mode}")
        enough = moments.count[2] >= required
        count_rows.append(
            {
                "time": label,
                "b_g_count": int(moments.count[0]),
                "b_s_count": int(moments.count[1]),
                "b_m_count": int(moments.count[2]),
                "required_count": required,
            }
        )
        if mission == 1 and not enough:
            # GOES-01 omits sub-threshold bins instead of retaining empty rows.
            continue
        if not enough:
            mean[:] = np.nan
            standard_deviation[:] = np.nan
        rows.append(
            {
                "time": label,
                "b_g": mean[0],
                "b_g_std": standard_deviation[0],
                "b_s": mean[1],
                "b_s_std": standard_deviation[1],
                "b_m": mean[2],
                "b_m_std": standard_deviation[2],
            }
        )
    _write_frame(target, pd.DataFrame(rows, columns=output_columns))
    _write_frame(count_target, pd.DataFrame(count_rows, columns=count_columns))
    print(f"[written] {target}")
    return target


def prepare_quarterly(
    processed_root: Path,
    missions: Sequence[int],
    *,
    months: int,
    coverage_fraction: float,
    fixed_minimum_count: int,
    coverage_mode: str,
    force: bool,
) -> None:
    for mission in missions:
        prepare_quarterly_mission(
            processed_root,
            mission,
            months=months,
            coverage_fraction=coverage_fraction,
            fixed_minimum_count=fixed_minimum_count,
            coverage_mode=coverage_mode,
            force=force,
        )


def verify_released_quarterly(
    processed_root: Path,
    missions: Sequence[int],
) -> None:
    """Verify the tracked quarterly boundary without running preparation."""

    targets = [
        processed_root / "quarterly" / f"goes{mission:02d}_quarterly.csv"
        for mission in missions
    ]
    missing = [
        target
        for target in targets
        if not target.exists() or target.stat().st_size == 0
    ]
    if missing:
        formatted = "\n  ".join(str(target) for target in missing)
        raise FileNotFoundError(
            "Lightweight reproduction requires the released quarterly checkpoints, "
            "but these files are missing or empty:\n  "
            f"{formatted}\n"
            "Restore the tracked inputs with `git restore -- data/processed/quarterly`, "
            "or run `python scripts/reproduce.py --full` to rebuild them from the "
            "source-data path."
        )
    for target in targets:
        _verify_released_quarterly_checkpoint(target)
    print("[verified] all requested released quarterly checkpoints")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "stage",
        nargs="?",
        choices=("minute", "quarterly", "all"),
        default="all",
        help="preparation stage to run (default: all)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("data/config/paper.toml"),
        help="TOML calculation configuration",
    )
    parser.add_argument("--source-dir", type=Path, help="override [paths].source")
    parser.add_argument("--processed-dir", type=Path, help="override [paths].processed")
    parser.add_argument("--missions", help="mission list/ranges, for example 6-8,10,12")
    parser.add_argument(
        "--maximum-tle-distance-days",
        type=float,
        help="override [coordinates].maximum_tle_distance_days",
    )
    parser.add_argument(
        "--released-only",
        action="store_true",
        help="verify released quarterly checkpoints without preparing minute data",
    )
    parser.add_argument("--force", action="store_true", help="replace requested checkpoints")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.released_only and args.stage != "all":
        parser.error("--released-only requires the all stage")
    if args.released_only and args.force:
        parser.error("--released-only cannot be combined with --force")
    config = _load_toml(args.config)
    source_root = args.source_dir or Path(
        _nested(config, "paths", "source", default="data/source")
    )
    processed_root = args.processed_dir or Path(
        _nested(config, "paths", "processed", default="data/processed")
    )
    if args.missions:
        missions = _parse_missions(args.missions)
    else:
        configured = set(
            _nested(config, "acquisition", "legacy_missions", default=range(1, 16))
        ) | set(
            _nested(
                config, "acquisition", "high_resolution_missions", default=range(8, 18)
            )
        )
        missions = _parse_missions(sorted(configured - {4}))

    sigma = float(
        _nested(config, "preprocessing", "outlier_standard_deviations", default=4.0)
    )
    outlier_mode = str(
        _nested(config, "preprocessing", "outlier_mode", default="published-sequential")
    )
    merge_policy = str(
        _nested(config, "preprocessing", "merge_policy", default="published-stack")
    )
    months = int(_nested(config, "preprocessing", "bin_months", default=3))
    coverage_fraction = float(
        _nested(config, "preprocessing", "minimum_bin_coverage", default=0.75)
    )
    coverage_mode = str(
        _nested(config, "preprocessing", "coverage_mode", default="published-fixed")
    )
    fixed_minimum_count = int(
        _nested(
            config,
            "preprocessing",
            "minimum_samples_per_bin",
            default=round(131_400 * coverage_fraction),
        )
    )
    maximum_tle_distance_days = (
        args.maximum_tle_distance_days
        if args.maximum_tle_distance_days is not None
        else float(
            _nested(
                config,
                "coordinates",
                "maximum_tle_distance_days",
                default=15.0,
            )
        )
    )
    if (
        sigma <= 0
        or months <= 0
        or maximum_tle_distance_days < 0
        or not 0 < coverage_fraction <= 1
    ):
        raise ValueError(
            "sigma/bin_months must be positive, TLE distance non-negative, and coverage in (0, 1]"
        )
    if coverage_mode == "published-fixed" and not math.isclose(
        coverage_fraction, 0.75
    ):
        raise ValueError(
            "published-fixed mode records the paper's 0.75 coverage criterion "
            "and enforces minimum_samples_per_bin as the executable threshold"
        )

    if args.released_only:
        verify_released_quarterly(processed_root, missions)
        return 0

    # The general ``all`` stage resumes from the deepest available checkpoint.
    # The lightweight orchestrator uses ``--released-only`` above instead, so
    # this fallback into minute preparation is never implicit there.
    if args.stage == "all" and not args.force:
        needed: list[int] = []
        for mission in missions:
            target = (
                processed_root / "quarterly" / f"goes{mission:02d}_quarterly.csv"
            )
            if target.exists() and target.stat().st_size > 0:
                _verify_released_quarterly_checkpoint(target)
            else:
                needed.append(mission)
        if not needed:
            print("[cached] all requested quarterly checkpoints")
            return 0
    else:
        needed = missions

    if args.stage in {"minute", "all"}:
        prepare_minute(
            source_root,
            processed_root,
            needed,
            sigma=sigma,
            outlier_mode=outlier_mode,
            merge_policy=merge_policy,
            maximum_tle_distance_days=maximum_tle_distance_days,
            force=args.force,
        )
    if args.stage in {"quarterly", "all"}:
        prepare_quarterly(
            processed_root,
            needed,
            months=months,
            coverage_fraction=coverage_fraction,
            fixed_minimum_count=fixed_minimum_count,
            coverage_mode=coverage_mode,
            force=args.force,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
