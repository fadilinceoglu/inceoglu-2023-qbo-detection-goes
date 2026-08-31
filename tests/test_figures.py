"""Small data-loading tests for the public figure routines."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import make_figures


def _wavelet_arrays() -> dict[str, np.ndarray]:
    return {
        "time": np.array([2000.0, 2000.25]),
        "period": np.array([1.0, 2.0]),
        "coi": np.array([1.0, 1.0]),
        "power": np.ones((2, 2)),
        "local_ratio": np.ones((2, 2)),
        "global_power": np.ones(2),
        "global_threshold": np.full(2, 2.0),
    }


CURRENT_METADATA = {
    "input_sha256": "a" * 64,
    "confidence": 0.95,
    "analysis_source_sha256": "b" * 64,
    "pycwt_version": "0.4.0b0",
    "pycwt_source_sha256": "c" * 64,
    "wavelet_algorithm_version": 3,
}


def test_paper_config_plots_every_retained_minute_row() -> None:
    settings = make_figures.load_settings(make_figures.DEFAULT_CONFIG)

    assert settings.figure1_row_stride == 1


def test_minute_plot_stride_continues_across_csv_chunks(tmp_path) -> None:
    source = pd.DataFrame(
        {
            "time": pd.date_range("2020-01-01", periods=11, freq="min"),
            "b_g": np.arange(11, dtype=float),
            "b_s": np.arange(11, dtype=float) + 100.0,
            "b_m": np.arange(11, dtype=float) - 100.0,
        }
    )
    path = tmp_path / "minute.csv"
    source.to_csv(path, index=False)

    selected = make_figures._read_minute_for_plot(
        path, row_stride=4, chunksize=3
    )
    expected = source.iloc[[0, 4, 8]].reset_index(drop=True)

    pd.testing.assert_frame_equal(selected, expected)


def test_wavelet_checkpoint_metadata_uses_current_input_and_settings() -> None:
    settings = make_figures.load_settings(make_figures.DEFAULT_CONFIG)
    time = np.array([2000.0, 2000.25, 2000.5])
    values = np.array([1.0, -2.0, 3.0])
    provenance = {
        "analysis_source_sha256": "d" * 64,
        "pycwt_version": "0.4.0b0",
        "pycwt_source_sha256": "e" * 64,
    }

    metadata = make_figures._wavelet_checkpoint_metadata(
        settings, time, values, provenance=provenance
    )

    assert metadata["input_sha256"] == make_figures._array_digest(time, values)
    assert metadata["confidence"] == settings.confidence
    assert metadata["morlet_frequency"] == settings.morlet_frequency
    assert metadata["dj"] == settings.scale_resolution
    assert metadata["j"] == settings.scale_count
    assert metadata["smallest_scale_factor"] == settings.smallest_scale_factor
    assert metadata["analysis_source_sha256"] == "d" * 64
    assert metadata["pycwt_version"] == "0.4.0b0"
    assert metadata["pycwt_source_sha256"] == "e" * 64


def test_wavelet_loader_accepts_current_checkpoint(tmp_path) -> None:
    checkpoint = tmp_path / "wavelet.npz"
    np.savez(checkpoint, **_wavelet_arrays(), **CURRENT_METADATA)

    loaded = make_figures.load_wavelet(
        checkpoint,
        expected_metadata=CURRENT_METADATA,
        config_path=tmp_path / "paper.toml",
    )

    np.testing.assert_array_equal(loaded["power"], np.ones((2, 2)))


def test_wavelet_loader_rejects_missing_checkpoint_metadata(tmp_path) -> None:
    checkpoint = tmp_path / "wavelet.npz"
    stored = dict(CURRENT_METADATA)
    stored.pop("analysis_source_sha256")
    np.savez(checkpoint, **_wavelet_arrays(), **stored)

    with pytest.raises(ValueError) as error:
        make_figures.load_wavelet(
            checkpoint,
            expected_metadata=CURRENT_METADATA,
            config_path=tmp_path / "paper.toml",
        )

    assert "analysis_source_sha256" in str(error.value)


@pytest.mark.parametrize(
    "field,replacement",
    (
        ("input_sha256", "f" * 64),
        ("confidence", 0.90),
        ("analysis_source_sha256", "0" * 64),
        ("pycwt_version", "different"),
        ("pycwt_source_sha256", "1" * 64),
        ("wavelet_algorithm_version", 2),
    ),
)
def test_wavelet_loader_rejects_noncurrent_checkpoint(
    tmp_path, field: str, replacement: object
) -> None:
    checkpoint = tmp_path / "wavelet.npz"
    stored = {**CURRENT_METADATA, field: replacement}
    np.savez(checkpoint, **_wavelet_arrays(), **stored)
    config = tmp_path / "paper.toml"

    with pytest.raises(ValueError) as error:
        make_figures.load_wavelet(
            checkpoint,
            expected_metadata=CURRENT_METADATA,
            config_path=config,
        )

    message = str(error.value)
    assert field in message
    assert (
        f"python scripts/analyze.py wavelets --config {config} --force" in message
    )
