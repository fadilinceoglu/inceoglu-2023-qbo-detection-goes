"""Revision-selection checks for downloaded and prepared NetCDF products."""

from __future__ import annotations

from pathlib import Path

import acquire_data
import prepare_data


def test_acquisition_selects_numerically_highest_netcdf_revision() -> None:
    links = [
        ("goes17_20230101_v9.nc", "https://example.test/v9"),
        ("goes17_20230101_v10.nc", "https://example.test/v10"),
        ("goes17_20230101_v2.nc", "https://example.test/v2"),
        ("goes16_20230101_v1-9-9.nc", "https://example.test/v1-9-9"),
        ("goes16_20230101_v1-10-0.nc", "https://example.test/v1-10-0"),
    ]

    selected = dict(acquire_data._deduplicate_links(reversed(links)))

    assert selected == {
        "goes16_20230101_v1-10-0.nc": "https://example.test/v1-10-0",
        "goes17_20230101_v10.nc": "https://example.test/v10",
    }


def test_preparation_selects_numerically_highest_netcdf_revision(
    tmp_path: Path,
) -> None:
    candidates = [
        tmp_path / "goes17_20230101_v9.nc",
        tmp_path / "goes17_20230101_v10.nc",
        tmp_path / "goes17_20230101_v2.nc",
        tmp_path / "goes16_20230101_v1.9.9.nc",
        tmp_path / "goes16_20230101_v1.10.0.nc",
    ]

    selected = prepare_data._deduplicate_netcdf(reversed(candidates))

    assert [path.name for path in selected] == [
        "goes16_20230101_v1.10.0.nc",
        "goes17_20230101_v10.nc",
    ]
