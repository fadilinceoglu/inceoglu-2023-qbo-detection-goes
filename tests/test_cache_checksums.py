"""Integrity checks for released inputs reused by lightweight runs."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from qbo_detection import acquisition as acquire_data
from qbo_detection import preparation as prepare_data


class _NoNetworkSession:
    def get(self, *args, **kwargs):  # pragma: no cover - failure path only
        pytest.fail("a verified cache hit must not make a network request")


def _write_checksum_manifest(path: Path, target: Path) -> None:
    checksum = hashlib.sha256(target.read_bytes()).hexdigest()
    path.write_text(f"{checksum}  {target.name}\n", encoding="utf-8")


def test_released_silso_cache_is_verified_before_reuse(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    target = source_root / "sunspots" / "SN_m_tot_V2.0.txt"
    target.parent.mkdir(parents=True)
    target.write_text("released SILSO bytes\n", encoding="utf-8")
    _write_checksum_manifest(target.parent / "SHA256SUMS", target)

    count = acquire_data.acquire_sunspots(
        _NoNetworkSession(), source_root, {}, timeout=1.0, force=False
    )

    assert count == 1


def test_changed_silso_cache_reports_git_restoration(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    target = source_root / "sunspots" / "SN_m_tot_V2.0.txt"
    target.parent.mkdir(parents=True)
    target.write_text("released SILSO bytes\n", encoding="utf-8")
    _write_checksum_manifest(target.parent / "SHA256SUMS", target)
    target.write_text("changed bytes\n", encoding="utf-8")

    with pytest.raises(RuntimeError) as error:
        acquire_data.acquire_sunspots(
            _NoNetworkSession(), source_root, {}, timeout=1.0, force=False
        )

    message = str(error.value)
    assert "failed SHA-256 verification" in message
    assert "git restore -- data/source/sunspots/" in message


def test_missing_silso_manifest_reports_git_restoration(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    target = source_root / "sunspots" / "SN_m_tot_V2.0.txt"
    target.parent.mkdir(parents=True)
    target.write_text("released SILSO bytes\n", encoding="utf-8")

    with pytest.raises(RuntimeError) as error:
        acquire_data.acquire_sunspots(
            _NoNetworkSession(), source_root, {}, timeout=1.0, force=False
        )

    message = str(error.value)
    assert "checksum manifest is missing" in message
    assert "git restore -- data/source/sunspots/" in message


def test_released_quarterly_cache_is_verified_in_lightweight_mode(tmp_path: Path) -> None:
    processed_root = tmp_path / "processed"
    quarterly = processed_root / "quarterly"
    quarterly.mkdir(parents=True)
    target = quarterly / "goes06_quarterly.csv"
    target.write_text("time,b_g,b_g_std,b_s,b_s_std,b_m,b_m_std\n", encoding="utf-8")
    _write_checksum_manifest(quarterly / "SHA256SUMS", target)

    result = prepare_data.main(
        [
            "all",
            "--source-dir",
            str(tmp_path / "source"),
            "--processed-dir",
            str(processed_root),
            "--missions",
            "6",
            "--released-only",
        ]
    )

    assert result == 0


def test_missing_released_quarterly_fails_without_preparation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    processed_root = tmp_path / "processed"

    def unexpected_preparation(*args, **kwargs) -> None:
        pytest.fail("released-only mode must not enter preparation")

    monkeypatch.setattr(prepare_data, "prepare_minute", unexpected_preparation)
    monkeypatch.setattr(prepare_data, "prepare_quarterly", unexpected_preparation)

    with pytest.raises(FileNotFoundError) as error:
        prepare_data.main(
            [
                "all",
                "--source-dir",
                str(tmp_path / "source"),
                "--processed-dir",
                str(processed_root),
                "--missions",
                "6",
                "--released-only",
            ]
        )

    message = str(error.value)
    assert "goes06_quarterly.csv" in message
    assert "git restore -- data/processed/quarterly" in message
    assert "python scripts/reproduce.py all --full" in message


def test_changed_quarterly_cache_reports_git_restoration(tmp_path: Path) -> None:
    processed_root = tmp_path / "processed"
    quarterly = processed_root / "quarterly"
    quarterly.mkdir(parents=True)
    target = quarterly / "goes06_quarterly.csv"
    target.write_text("released checkpoint\n", encoding="utf-8")
    _write_checksum_manifest(quarterly / "SHA256SUMS", target)
    target.write_text("changed checkpoint\n", encoding="utf-8")

    with pytest.raises(RuntimeError) as error:
        prepare_data.prepare_quarterly_mission(
            processed_root,
            6,
            months=3,
            coverage_fraction=0.75,
            fixed_minimum_count=98_550,
            coverage_mode="published-fixed",
            force=False,
        )

    message = str(error.value)
    assert "failed SHA-256 verification" in message
    assert "git restore -- data/processed/quarterly/goes06_quarterly.csv" in message


def test_repository_quarterly_cache_requires_its_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    processed_root = tmp_path / "processed"
    quarterly = processed_root / "quarterly"
    quarterly.mkdir(parents=True)
    target = quarterly / "goes06_quarterly.csv"
    target.write_text("released checkpoint\n", encoding="utf-8")
    monkeypatch.setattr(prepare_data, "RELEASED_QUARTERLY_DIR", quarterly)

    with pytest.raises(RuntimeError) as error:
        prepare_data.prepare_quarterly_mission(
            processed_root,
            6,
            months=3,
            coverage_fraction=0.75,
            fixed_minimum_count=98_550,
            coverage_mode="published-fixed",
            force=False,
        )

    message = str(error.value)
    assert "checksum manifest is missing" in message
    assert "git restore -- data/processed/quarterly/" in message
