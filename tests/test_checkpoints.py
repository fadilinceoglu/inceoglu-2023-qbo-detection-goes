"""Tests for the released compact quarterly checkpoints."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd
import pytest

from qbo_detection import analysis as analyze
from conftest import REPOSITORY_ROOT


QUARTERLY_DIR = REPOSITORY_ROOT / "data" / "processed" / "quarterly"
EXPECTED_COLUMNS = ["time", "b_g", "b_g_std", "b_s", "b_s_std", "b_m", "b_m_std"]
ALL_MISSIONS = (1, 2, 3, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17)
EXPECTED_SELECTIONS = {
    6: ("1984-08-31", "1992-11-30", 34),
    7: ("1987-06-30", "1993-03-31", 24),
    8: ("1995-10-31", "2003-04-30", 31),
    10: ("1998-05-31", "2006-05-31", 33),
    12: ("2003-02-28", "2010-08-31", 31),
    13: ("2010-08-31", "2017-11-30", 30),
    15: ("2013-04-30", "2017-10-31", 19),
    17: ("2018-06-30", "2022-12-31", 19),
}


@pytest.mark.parametrize("mission", ALL_MISSIONS)
def test_quarterly_checkpoint_schema(mission: int) -> None:
    path = QUARTERLY_DIR / f"goes{mission:02d}_quarterly.csv"
    frame = pd.read_csv(path)

    assert frame.columns.tolist() == EXPECTED_COLUMNS
    if frame.empty:
        assert mission == 1
        return
    times = pd.to_datetime(frame["time"], errors="raise")
    assert times.is_monotonic_increasing
    assert not times.duplicated().any()
    for column in EXPECTED_COLUMNS[1:]:
        pd.to_numeric(frame[column], errors="raise")


def test_quarterly_checkpoint_checksums() -> None:
    manifest = QUARTERLY_DIR / "SHA256SUMS"
    for line in manifest.read_text(encoding="utf-8").splitlines():
        expected, filename = line.split(maxsplit=1)
        path = QUARTERLY_DIR / filename.lstrip("* ")
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected


@pytest.mark.parametrize("mission", tuple(EXPECTED_SELECTIONS))
def test_paper_selection_spans(mission: int) -> None:
    path = QUARTERLY_DIR / f"goes{mission:02d}_quarterly.csv"
    frame = analyze.load_quarterly_checkpoint(path)
    selected, records = analyze.select_and_filter_mission(frame, mission)
    expected_start, expected_end, expected_samples = EXPECTED_SELECTIONS[mission]

    assert {record["component"] for record in records} == set(analyze.COMPONENTS)
    for record in records:
        assert record["accepted"] is True
        assert record["reason"] == "accepted"
        assert record["start"][:10] == expected_start
        assert record["end"][:10] == expected_end
        assert record["samples"] == expected_samples
        assert record["duration_years"] >= 4.5

        chosen = selected[f"chosen_{record['component']}"]
        assert int(chosen.notna().sum()) == expected_samples
