"""Acquire the authoritative GOES inputs and cache Space-Track TLEs.

Downloads are bounded by explicit UTC dates and mission lists from
``data/config/paper.toml`` or command-line overrides.  Existing complete files
are reused, partial HTTP downloads resume through Range requests, and TLS
certificate verification remains enabled.  Space-Track credentials are read
only from ``SPACETRACK_IDENTITY`` and ``SPACETRACK_PASSWORD``.

This is the data-intensive part of the calculation; use a short, bounded
``--start``/``--end`` interval for an installation check.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from collections.abc import Iterable, Sequence
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin, urlsplit

from .config import DEFAULT_CONFIG, resolve_repository_path
from .io import manifest_checksum as _manifest_checksum
from .io import sha256_file as _sha256


LEGACY_BASE_URL = (
    "https://www.ncei.noaa.gov/data/goes-space-environment-monitor/access/avg/"
)
HIGH_RESOLUTION_BASE_URLS = {
    **{
        mission: (
            "https://www.ncei.noaa.gov/data/goes-space-environment-monitor/"
            f"access/science/mag/goes{mission:02d}/magn-l2-hires/"
        )
        for mission in range(8, 16)
    },
    **{
        mission: (
            "https://data.ngdc.noaa.gov/platforms/solar-space-observing-satellites/"
            f"goes/goes{mission:02d}/l2/data/magn-l2-hires/"
        )
        for mission in (16, 17)
    },
}
NORAD_CATALOG_IDS = {
    1: "08366",
    2: "10061",
    3: "10953",
    4: "11964",
    5: "12472",
    6: "14050",
    7: "17561",
    8: "23051",
    9: "23581",
    10: "24786",
    11: "26352",
    12: "26871",
    13: "29155",
    14: "35491",
    15: "36411",
    16: "41866",
    17: "43226",
}
USER_AGENT = "inceoglu-2023-qbo-detection-goes/1.0 (scientific reproduction)"
SUNSPOT_URL = "https://www.sidc.be/SILSO/DATA/SN_m_tot_V2.0.txt"
SUNSPOT_FIRST_MONTH = (1970, 1)
SUNSPOT_LAST_MONTH = (2023, 6)


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self.links.append(href)


def _load_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.9/3.10
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


def _parse_date(value: str | date, label: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f"{label} must use YYYY-MM-DD; received {value!r}") from exc


def _parse_missions(value: str | Sequence[int] | None) -> list[int]:
    if value is None:
        return []
    if not isinstance(value, str):
        missions = [int(item) for item in value]
    else:
        missions = []
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
    invalid = [mission for mission in result if mission not in NORAD_CATALOG_IDS]
    if invalid:
        raise ValueError(f"unsupported GOES mission numbers: {invalid}")
    return result


def _iter_months(start: date, end: date) -> Iterable[tuple[int, int]]:
    cursor = start.replace(day=1)
    last = end.replace(day=1)
    while cursor <= last:
        yield cursor.year, cursor.month
        cursor = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)


def _filename_span(name: str) -> tuple[date, date] | None:
    candidates = re.findall(r"(?<!\d)(\d{8})(?!\d)", name)
    dates: list[date] = []
    for candidate in candidates:
        try:
            dates.append(datetime.strptime(candidate, "%Y%m%d").date())
        except ValueError:
            continue
    return (min(dates), max(dates)) if dates else None


def _logical_product(name: str) -> str:
    """Collapse NCEI version suffixes so only one revision is downloaded."""

    return re.sub(r"_v\d+(?:[-.]\d+)*(?=\.nc$)", "_v", name, flags=re.IGNORECASE)


def _netcdf_revision(name: str) -> tuple[int, ...]:
    """Return the numeric components of a terminal NCEI NetCDF revision."""

    match = re.search(r"_v(\d+(?:[-.]\d+)*)(?=\.nc$)", name, flags=re.IGNORECASE)
    if match is None:
        return ()
    return tuple(int(component) for component in re.split(r"[-.]", match.group(1)))


def _directory_files(session: Any, url: str, timeout: float) -> list[tuple[str, str]]:
    response = session.get(url, timeout=timeout)
    if response.status_code == 404:
        return []
    response.raise_for_status()
    parser = _LinkParser()
    parser.feed(response.text)
    files: list[tuple[str, str]] = []
    for href in parser.links:
        name = Path(unquote(urlsplit(href).path)).name
        if name and name not in {".", ".."}:
            files.append((name, urljoin(url, href)))
    return files


def _deduplicate_links(links: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    selected: dict[str, tuple[tuple[int, ...], str, str]] = {}
    for name, url in links:
        logical_product = _logical_product(name)
        candidate = (_netcdf_revision(name), name, url)
        current = selected.get(logical_product)
        if current is None or candidate > current:
            selected[logical_product] = candidate
    return sorted((name, url) for _, name, url in selected.values())


def _verify_released_sunspot_input(target: Path) -> None:
    """Refuse to reuse a changed or unverifiable released SILSO input."""

    manifest = target.parent / "SHA256SUMS"
    restore = (
        "git restore -- data/source/sunspots/SN_m_tot_V2.0.txt "
        "data/source/sunspots/SHA256SUMS"
    )
    try:
        expected = _manifest_checksum(manifest, target.name)
    except (OSError, RuntimeError) as error:
        raise RuntimeError(
            f"Cannot verify the released SILSO input {target}: {error}. "
            f"Restore the released file and checksum manifest with `{restore}`, "
            "then rerun the command."
        ) from error
    actual = _sha256(target)
    if actual != expected:
        raise RuntimeError(
            f"Released SILSO input failed SHA-256 verification: {target}. "
            f"Expected {expected}, found {actual}. Restore the released input with "
            f"`{restore}`, then rerun the command."
        )


def verify_released_sunspot(source_root: Path) -> Path:
    """Verify and return the exact tracked SILSO input used by analysis."""

    target = Path(source_root) / "sunspots" / "SN_m_tot_V2.0.txt"
    if not target.is_file():
        raise FileNotFoundError(
            f"Released SILSO input is missing: {target}. Restore it with "
            "`git restore -- data/source/sunspots`, then rerun the command."
        )
    _verify_released_sunspot_input(target)
    return target


def _download(
    session: Any,
    url: str,
    target: Path,
    *,
    timeout: float,
    force: bool,
) -> tuple[str, int, str]:
    """Download atomically, resuming a non-empty ``.part`` file when possible."""

    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size > 0 and not force:
        return "cached", target.stat().st_size, _sha256(target)

    partial = target.with_name(target.name + ".part")
    if force and partial.exists():
        partial.unlink()
    offset = partial.stat().st_size if partial.exists() else 0
    headers = {"Range": f"bytes={offset}-"} if offset else {}
    response = session.get(url, headers=headers, stream=True, timeout=timeout)
    if response.status_code == 416 and offset:
        # The remote object may have changed; restart instead of guessing that
        # the partial byte count represents a complete file.
        partial.unlink()
        offset = 0
        response = session.get(url, stream=True, timeout=timeout)
    response.raise_for_status()

    resumed = offset > 0 and response.status_code == 206
    mode = "ab" if resumed else "wb"
    with partial.open(mode) as stream:
        for block in response.iter_content(chunk_size=1024 * 1024):
            if block:
                stream.write(block)
        stream.flush()
        os.fsync(stream.fileno())

    expected = response.headers.get("Content-Length")
    if expected is not None:
        expected_size = int(expected) + (offset if resumed else 0)
        if partial.stat().st_size != expected_size:
            raise IOError(
                f"incomplete download for {url}: expected {expected_size} bytes, "
                f"received {partial.stat().st_size}"
            )
    partial.replace(target)
    return "downloaded", target.stat().st_size, _sha256(target)


def _load_manifest(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as stream:
        records = json.load(stream)
    return {str(record["url"]): record for record in records}


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
    temporary.replace(path)


def _record_download(
    manifest: dict[str, dict[str, Any]],
    *,
    url: str,
    path: Path,
    source_root: Path,
    mission: int,
    product: str,
    status: str,
    size: int,
    sha256: str,
) -> None:
    manifest[url] = {
        "mission": mission,
        "path": path.relative_to(source_root).as_posix(),
        "product": product,
        "sha256": sha256,
        "size": size,
        "status": status,
        "url": url,
    }


def acquire_legacy(
    session: Any,
    source_root: Path,
    missions: Sequence[int],
    start: date,
    end: date,
    manifest: dict[str, dict[str, Any]],
    *,
    timeout: float,
    force: bool,
    delay: float,
) -> int:
    """Download one-minute EPN files from the GOES 1--15 archive."""

    count = 0
    for year, month in _iter_months(start, end):
        for mission in missions:
            if not 1 <= mission <= 15:
                continue
            directory = urljoin(
                LEGACY_BASE_URL,
                f"{year:04d}/{month:02d}/goes{mission:02d}/netcdf/",
            )
            links = [
                item
                for item in _directory_files(session, directory, timeout)
                if item[0].lower().endswith(".nc")
                and "magneto" in item[0].lower()
                and "1m" in item[0].lower()
            ]
            for name, url in _deduplicate_links(links):
                span = _filename_span(name)
                if span is not None and (span[1] < start or span[0] > end):
                    continue
                target = source_root / "legacy" / f"goes{mission:02d}" / name
                status, size, digest = _download(
                    session, url, target, timeout=timeout, force=force
                )
                _record_download(
                    manifest,
                    url=url,
                    path=target,
                    source_root=source_root,
                    mission=mission,
                    product="legacy-one-minute-epn",
                    status=status,
                    size=size,
                    sha256=digest,
                )
                count += 1
                print(f"[{status}] GOES-{mission:02d} {name}")
                if delay:
                    time.sleep(delay)
    return count


def acquire_high_resolution(
    session: Any,
    source_root: Path,
    missions: Sequence[int],
    start: date,
    end: date,
    manifest: dict[str, dict[str, Any]],
    *,
    timeout: float,
    force: bool,
    delay: float,
) -> int:
    """Download Level-2 high-resolution GSM files from the GOES 8--17 archive."""

    count = 0
    for mission in missions:
        if mission not in HIGH_RESOLUTION_BASE_URLS:
            continue
        for year, month in _iter_months(start, end):
            directory = urljoin(
                HIGH_RESOLUTION_BASE_URLS[mission], f"{year:04d}/{month:02d}/"
            )
            links = [
                item
                for item in _directory_files(session, directory, timeout)
                if item[0].lower().endswith(".nc")
            ]
            for name, url in _deduplicate_links(links):
                span = _filename_span(name)
                if span is not None and (span[1] < start or span[0] > end):
                    continue
                target = source_root / "high_resolution" / f"goes{mission:02d}" / name
                status, size, digest = _download(
                    session, url, target, timeout=timeout, force=force
                )
                _record_download(
                    manifest,
                    url=url,
                    path=target,
                    source_root=source_root,
                    mission=mission,
                    product="level2-high-resolution-gsm",
                    status=status,
                    size=size,
                    sha256=digest,
                )
                count += 1
                print(f"[{status}] GOES-{mission:02d} {name}")
                if delay:
                    time.sleep(delay)
    return count


def _valid_tle_text(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return len(lines) >= 2 and len(lines) % 2 == 0 and all(
        line.startswith("1 ") and lines[index * 2 + 1].startswith("2 ")
        for index, line in enumerate(lines[::2])
    )


def acquire_tles(
    source_root: Path,
    missions: Sequence[int],
    start: date,
    end: date,
    *,
    force: bool,
) -> int:
    """Cache historical TLEs for deterministic, offline EPN conversion."""

    targets = [mission for mission in missions if 1 <= mission <= 15]
    missing = [
        mission
        for mission in targets
        if force
        or not (source_root / "tle" / f"goes{mission:02d}.tle").exists()
        or not _valid_tle_text(
            (source_root / "tle" / f"goes{mission:02d}.tle").read_text(
                encoding="utf-8"
            )
        )
    ]
    if not missing:
        print("[cached] all requested TLE catalogs")
        return len(targets)

    identity = os.environ.get("SPACETRACK_IDENTITY")
    password = os.environ.get("SPACETRACK_PASSWORD")
    if not identity or not password:
        raise RuntimeError(
            "Space-Track credentials are required for uncached TLEs. Set "
            "SPACETRACK_IDENTITY and SPACETRACK_PASSWORD in the environment."
        )
    try:
        from spacetrack import SpaceTrackClient
        from spacetrack.operators import inclusive_range
    except ImportError as exc:  # pragma: no cover - dependency checked at runtime
        raise RuntimeError("TLE acquisition requires the 'spacetrack' package") from exc

    client = SpaceTrackClient(identity, password)
    # Padding permits the conversion stage to select the nearest element set at
    # either study boundary while retaining its strict maximum-distance check.
    query_start = start - timedelta(days=30)
    query_end = end + timedelta(days=30)
    tle_root = source_root / "tle"
    tle_root.mkdir(parents=True, exist_ok=True)
    for mission in missing:
        text = client.tle(
            norad_cat_id=NORAD_CATALOG_IDS[mission],
            epoch=inclusive_range(query_start, query_end),
            orderby="epoch asc",
            format="tle",
        )
        if not isinstance(text, str) or not _valid_tle_text(text):
            raise RuntimeError(f"Space-Track returned no valid TLE pairs for GOES-{mission:02d}")
        target = tle_root / f"goes{mission:02d}.tle"
        temporary = target.with_suffix(".tle.tmp")
        temporary.write_text(text.rstrip() + "\n", encoding="utf-8")
        temporary.replace(target)
        metadata = {
            "mission": mission,
            "norad_catalog_id": NORAD_CATALOG_IDS[mission],
            "query_end": query_end.isoformat(),
            "query_start": query_start.isoformat(),
            "retrieved_utc": datetime.now(timezone.utc).isoformat(),
            "sha256": _sha256(target),
            "source": "https://www.space-track.org/",
        }
        _write_json(tle_root / f"goes{mission:02d}.json", metadata)
        print(f"[downloaded] GOES-{mission:02d} TLE catalog")
    return len(targets)


def acquire_sunspots(
    session: Any,
    source_root: Path,
    manifest: dict[str, dict[str, Any]],
    *,
    timeout: float,
    force: bool,
) -> int:
    """Retain the bounded monthly SILSO series consumed by the paper calculation."""

    target = source_root / "sunspots" / "SN_m_tot_V2.0.txt"
    if target.exists() and target.stat().st_size > 0 and not force:
        _verify_released_sunspot_input(target)
        print(f"[cached] {target}")
        return 1

    response = session.get(SUNSPOT_URL, timeout=timeout)
    response.raise_for_status()
    selected: list[str] = []
    for raw_line in response.text.splitlines():
        fields = raw_line.split()
        if len(fields) < 6:
            continue
        try:
            month = (int(fields[0]), int(fields[1]))
            # Validate the remaining numerical columns before publishing the row.
            [float(value) for value in fields[2:6]]
        except ValueError:
            continue
        if SUNSPOT_FIRST_MONTH <= month <= SUNSPOT_LAST_MONTH:
            selected.append(
                f"{fields[0]:>4} {int(fields[1]):02d} {fields[2]:>8} "
                f"{fields[3]:>6} {fields[4]:>5} {fields[5]:>5}"
            )
    expected_months = (
        (SUNSPOT_LAST_MONTH[0] - SUNSPOT_FIRST_MONTH[0]) * 12
        + SUNSPOT_LAST_MONTH[1]
        - SUNSPOT_FIRST_MONTH[1]
        + 1
    )
    if len(selected) != expected_months:
        raise RuntimeError(
            f"SILSO returned {len(selected)} bounded monthly rows; expected {expected_months}"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text("\n".join(selected) + "\n", encoding="utf-8")
    temporary.replace(target)
    manifest[SUNSPOT_URL] = {
        "path": target.relative_to(source_root).as_posix(),
        "product": "monthly-total-sunspot-number-v2",
        "sha256": _sha256(target),
        "size": target.stat().st_size,
        "status": "downloaded",
        "url": SUNSPOT_URL,
    }
    print(f"[downloaded] {target}")
    return 1


def _range_from_config(
    args: argparse.Namespace,
    config: dict[str, Any],
    prefix: str,
) -> tuple[date, date] | None:
    configured_start = _nested(config, "acquisition", f"{prefix}_start")
    configured_end = _nested(config, "acquisition", f"{prefix}_end")
    if configured_start is None or configured_end is None:
        start_value, end_value = args.start, args.end
    else:
        start_value, end_value = configured_start, configured_end
    if start_value is None or end_value is None:
        raise ValueError(
            f"the {prefix.replace('_', ' ')} date range must be explicit in the config "
            "or supplied with --start and --end"
        )
    start = _parse_date(start_value, "start date")
    end = _parse_date(end_value, "end date")
    if args.start is not None:
        # Command-line bounds restrict the configured paper interval; they do
        # not make a product family exist outside its documented coverage.
        start = max(start, _parse_date(args.start, "start date"))
        end = min(end, _parse_date(args.end, "end date"))
    if start > end:
        return None
    return start, end


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "stage",
        nargs="?",
        choices=(
            "legacy",
            "highres",
            "high-resolution",
            "tle",
            "tles",
            "sunspots",
            "all",
        ),
        default="all",
        help="input family to acquire (default: all)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="TOML calculation configuration (default: data/config/paper.toml)",
    )
    parser.add_argument("--source-dir", type=Path, help="override [paths].source")
    parser.add_argument("--start", help="override start date for a bounded run (YYYY-MM-DD)")
    parser.add_argument("--end", help="override end date for a bounded run (YYYY-MM-DD)")
    parser.add_argument(
        "--missions",
        help="comma-separated missions and ranges, for example 6-8,10,12",
    )
    parser.add_argument("--timeout", type=float, default=90.0, help="HTTP timeout in seconds")
    parser.add_argument(
        "--request-delay",
        type=float,
        default=0.1,
        help="polite delay after each NCEI file request in seconds",
    )
    parser.add_argument("--force", action="store_true", help="replace existing requested files")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if (args.start is None) != (args.end is None):
        parser.error("--start and --end must be supplied together")

    config = _load_toml(args.config)
    source_root = resolve_repository_path(
        args.source_dir
        or _nested(config, "paths", "source", default="data/source")
    )
    requested = _parse_missions(args.missions) if args.missions else []
    legacy_missions = requested or _parse_missions(
        _nested(config, "acquisition", "legacy_missions", default=range(1, 16))
    )
    high_resolution_missions = requested or _parse_missions(
        _nested(
            config,
            "acquisition",
            "high_resolution_missions",
            default=range(8, 18),
        )
    )
    legacy_range = (
        _range_from_config(args, config, "legacy")
        if args.stage in {"legacy", "tle", "tles", "all"}
        else None
    )
    high_range = (
        _range_from_config(args, config, "high_resolution")
        if args.stage in {"highres", "high-resolution", "all"}
        else None
    )

    try:
        import requests
    except ImportError as exc:  # pragma: no cover - dependency checked at runtime
        raise RuntimeError("data acquisition requires the 'requests' package") from exc
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    manifest_path = source_root / "manifest.json"
    manifest = _load_manifest(manifest_path)
    files = 0

    if args.stage in {"legacy", "all"} and legacy_range is not None:
        legacy_start, legacy_end = legacy_range
        files += acquire_legacy(
            session,
            source_root,
            legacy_missions,
            legacy_start,
            legacy_end,
            manifest,
            timeout=args.timeout,
            force=args.force,
            delay=args.request_delay,
        )
        _write_json(manifest_path, sorted(manifest.values(), key=lambda item: item["url"]))
    if args.stage in {"highres", "high-resolution", "all"} and high_range is not None:
        high_start, high_end = high_range
        files += acquire_high_resolution(
            session,
            source_root,
            high_resolution_missions,
            high_start,
            high_end,
            manifest,
            timeout=args.timeout,
            force=args.force,
            delay=args.request_delay,
        )
        _write_json(manifest_path, sorted(manifest.values(), key=lambda item: item["url"]))
    if args.stage in {"tle", "tles", "all"} and legacy_range is not None:
        legacy_start, legacy_end = legacy_range
        files += acquire_tles(
            source_root,
            legacy_missions,
            legacy_start,
            legacy_end,
            force=args.force,
        )
    if args.stage in {"sunspots", "all"}:
        # A full rerun must consume the exact released SILSO input already in
        # the repository.  Replacing it with a later provider revision requires
        # the explicit, narrow ``sunspots --force`` command.
        refresh_sunspots = args.force and args.stage == "sunspots"
        files += acquire_sunspots(
            session,
            source_root,
            manifest,
            timeout=args.timeout,
            force=refresh_sunspots,
        )
        _write_json(manifest_path, sorted(manifest.values(), key=lambda item: item["url"]))

    print(f"Acquisition complete: {files} requested file/catalog item(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
