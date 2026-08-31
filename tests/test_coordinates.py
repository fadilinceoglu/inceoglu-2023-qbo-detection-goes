"""Tests for the standalone EPN/ECI rotation algebra."""

from __future__ import annotations

import sys

import numpy as np

from qbo_detection.coordinates import epn_to_eci


def _orbital_positions(
    radius: np.ndarray,
    inclination_degrees: np.ndarray,
    raan_degrees: np.ndarray,
    argument_degrees: np.ndarray,
) -> np.ndarray:
    """Construct position vectors consistent with the transform inputs."""

    inclination = np.deg2rad(inclination_degrees)
    raan = np.deg2rad(raan_degrees)
    argument = np.deg2rad(argument_degrees)
    cos_i, sin_i = np.cos(inclination), np.sin(inclination)
    cos_n, sin_n = np.cos(raan), np.sin(raan)
    cos_u, sin_u = np.cos(argument), np.sin(argument)
    return np.column_stack(
        (
            radius * (cos_n * cos_u - sin_n * sin_u * cos_i),
            radius * (sin_n * cos_u + cos_n * sin_u * cos_i),
            radius * sin_u * sin_i,
        )
    )


def test_epn_known_equatorial_basis() -> None:
    """The E-P-N column ordering is explicit in a simple known geometry."""

    epn_basis = np.eye(3)
    orbit = np.repeat([[42_164.0, 0.0, 0.0]], repeats=3, axis=0)
    inclination = np.zeros(3)
    raan = np.zeros(3)

    eci = epn_to_eci(epn_basis, orbit, inclination, raan)

    expected = np.array(
        [
            [-1.0, 0.0, 0.0],  # Earthward (nadir)
            [0.0, 0.0, 1.0],   # Poleward (orbit-plane normal)
            [0.0, 1.0, 0.0],   # Normal (completes the right-handed frame)
        ]
    )
    np.testing.assert_array_equal(eci, expected)


def test_epn_eci_round_trip_is_inverse_without_spacepy() -> None:
    """Forward and backward rotations recover every finite input vector."""

    inclination = np.array([0.1, 4.0, 12.5, 29.0, 63.0])
    raan = np.array([0.0, 31.0, 122.0, 241.0, 359.0])
    argument = np.array([15.0, 88.0, 179.0, 224.0, 311.0])
    orbit = _orbital_positions(
        np.array([42_164.0, 42_100.0, 41_900.0, 42_250.0, 40_000.0]),
        inclination,
        raan,
        argument,
    )
    epn = np.array(
        [
            [12.5, -7.0, 90.0],
            [-1.0, 2.0, 3.0],
            [0.0, -85.25, 9.5],
            [41.0, 37.0, -22.0],
            [-300.0, 0.125, 18.0],
        ]
    )

    eci = epn_to_eci(epn, orbit, inclination, raan)
    recovered = epn_to_eci(eci, orbit, inclination, raan, backward=True)

    np.testing.assert_allclose(recovered, epn, rtol=2e-15, atol=2e-13)
    np.testing.assert_allclose(
        np.linalg.norm(eci, axis=1), np.linalg.norm(epn, axis=1), rtol=2e-15
    )
    assert "spacepy" not in sys.modules
