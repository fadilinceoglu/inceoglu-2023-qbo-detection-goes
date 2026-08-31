"""Focused checks for the PyCWT AR(1) wavelet significance calculation."""

from __future__ import annotations

import numpy as np
import pytest

import analyze


def test_paper_config_uses_the_single_pycwt_ar1_method() -> None:
    with analyze.DEFAULT_CONFIG.open("rb") as stream:
        config = analyze.tomllib.load(stream)

    assert analyze.SIGNIFICANCE_METHOD == "pycwt-ar1"
    assert config["wavelet"]["confidence_level"] == 0.95
    assert config["wavelet"]["morlet_frequency"] == 6.0


def test_published_significance_matches_direct_pycwt() -> None:
    wavelet = pytest.importorskip(
        "pycwt", reason="PyCWT is an optional analysis dependency"
    )
    time = np.arange(64, dtype=float) / 4.0
    values = np.sin(2.0 * np.pi * time / 2.4) + 0.2 * np.cos(
        2.0 * np.pi * time / 4.1
    )
    confidence = 0.95
    dj = 0.125
    j = 16
    smallest_scale_factor = 2.0

    result = analyze.compute_cwt(
        values,
        time,
        confidence=confidence,
        dj=dj,
        j=j,
        smallest_scale_factor=smallest_scale_factor,
    )

    standardized = (values - values.mean()) / values.std(ddof=0)
    dt = float(np.mean(np.diff(time)))
    mother = wavelet.Morlet(analyze.DEFAULT_MORLET_FREQUENCY)
    coefficients, scales, _, _, _, _ = wavelet.cwt(
        standardized,
        dt,
        dj,
        smallest_scale_factor * dt,
        j,
        mother,
    )
    try:
        alpha, _, _ = wavelet.ar1(standardized)
    except Exception:
        alpha = 0.0
    expected_local_by_scale, _ = wavelet.significance(
        1.0,
        dt,
        scales,
        0,
        alpha,
        significance_level=confidence,
        wavelet=mother,
    )
    expected_global, _ = wavelet.significance(
        1.0,
        dt,
        scales,
        1,
        alpha,
        significance_level=confidence,
        dof=values.size - scales,
        wavelet=mother,
    )
    expected_local = np.ones((1, values.size)) * expected_local_by_scale[:, None]

    np.testing.assert_array_equal(result["power"], np.abs(coefficients) ** 2)
    np.testing.assert_array_equal(result["local_threshold"], expected_local)
    np.testing.assert_array_equal(result["global_threshold"], expected_global)
    np.testing.assert_array_equal(
        result["local_ratio"], result["power"] / expected_local
    )
    assert result["significance_method"] == analyze.SIGNIFICANCE_METHOD
    assert result["wavelet_cache_schema"] == analyze.WAVELET_CACHE_SCHEMA
    assert result["wavelet_cache_version"] == analyze.WAVELET_CACHE_VERSION
    assert result["wavelet_algorithm"] == analyze.WAVELET_ALGORITHM
    assert result["wavelet_algorithm_version"] == analyze.WAVELET_ALGORITHM_VERSION
    assert result["analysis_source_sha256"] == analyze._analysis_source_sha256()
    assert result["pycwt_version"] == analyze._installed_pycwt_version()
    assert result["pycwt_source_sha256"] == analyze._pycwt_source_sha256()


def test_wavelet_checkpoint_provenance_controls_cache_reuse(tmp_path) -> None:
    pytest.importorskip("pycwt", reason="PyCWT is an optional analysis dependency")
    destination = tmp_path / "wavelet.npz"
    input_sha256 = "a" * 64
    analyze.save_wavelet_result(
        destination,
        {"power": np.ones((2, 3), dtype=float)},
        input_sha256=input_sha256,
    )

    provenance = analyze._wavelet_provenance()
    expected = {"input_sha256": input_sha256, **provenance}
    assert analyze._wavelet_is_current(destination, expected)

    with np.load(destination, allow_pickle=False) as checkpoint:
        for key, value in provenance.items():
            assert checkpoint[key].item() == value

    for key, replacement in (
        ("wavelet_cache_version", analyze.WAVELET_CACHE_VERSION + 1),
        ("wavelet_algorithm_version", analyze.WAVELET_ALGORITHM_VERSION + 1),
        ("analysis_source_sha256", "b" * 64),
        ("pycwt_version", provenance["pycwt_version"] + ".different"),
        ("pycwt_source_sha256", "c" * 64),
        ("significance_method", "different-method"),
    ):
        changed = {**expected, key: replacement}
        assert not analyze._wavelet_is_current(destination, changed)
