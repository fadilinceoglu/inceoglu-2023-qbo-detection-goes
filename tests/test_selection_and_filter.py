"""Tests for interval selection and the paper's sample-domain filter."""

from __future__ import annotations

import numpy as np
from scipy.signal import butter, filtfilt

import analyze


def test_longest_interval_breaks_at_nan_and_resolves_ties_earliest() -> None:
    values = np.array([1.0, 2.0, np.nan, 3.0, 4.0, np.nan, 5.0, 6.0])

    assert analyze.longest_contiguous_interval(values) == (0, 1)
    assert analyze.longest_contiguous_interval(np.full(4, np.nan)) is None


def test_longest_interval_breaks_at_missing_quarter() -> None:
    values = np.arange(7.0)
    times = np.array([2000.00, 2000.25, 2000.50, 2001.25, 2001.50, 2001.75, 2002.00])

    # The 0.75-year jump starts a new interval even though every value is finite.
    assert analyze.longest_contiguous_interval(values, times) == (3, 6)


def test_difference_of_lowpasses_uses_exact_paper_cutoffs() -> None:
    samples = np.arange(512.0)
    values = (
        np.sin(2.0 * np.pi * samples / 8.0)
        + 0.35 * np.sin(2.0 * np.pi * samples / 40.0)
        + 0.25 * np.sin(2.0 * np.pi * samples / 3.0)
    )

    actual = analyze.difference_of_lowpasses(values, samples)
    high_b, high_a = butter(5, 2.0 / 4.445, btype="low")
    low_b, low_a = butter(5, 2.0 / 18.1, btype="low")
    expected = filtfilt(high_b, high_a, values) - filtfilt(low_b, low_a, values)

    np.testing.assert_allclose(actual, expected, rtol=0.0, atol=0.0)


def test_difference_of_lowpasses_retains_the_qbo_band() -> None:
    samples = np.arange(1024.0)
    passband = np.sin(2.0 * np.pi * samples / 8.0)
    slow = np.sin(2.0 * np.pi * samples / 40.0)
    fast = np.sin(2.0 * np.pi * samples / 3.0)
    output = analyze.difference_of_lowpasses(passband + slow + fast, samples)
    central = slice(100, -100)

    def fitted_amplitude(reference: np.ndarray) -> float:
        x = reference[central]
        return float(np.dot(output[central], x) / np.dot(x, x))

    assert fitted_amplitude(passband) > 0.95
    assert abs(fitted_amplitude(slow)) < 0.05
    assert abs(fitted_amplitude(fast)) < 0.05
