#!/usr/bin/env python3
"""Coordinate operations needed by the legacy GOES magnetometer pipeline.

The EPN-to-ECI rotation follows the GOES-R Magnetometer Alternate Coordinate
Systems algorithm used by the paper calculation.  Its implementation heritage
is the SWPC ``magcor`` processing (L. Matheson) and the Loto'aniu ``goesMag``
C++ transformation.  Only the equations required by this study are retained
here.  The final GEI/ECI-to-GSM conversion is delegated to SpacePy, whose
time-dependent coordinate implementation is independently maintained.

Coordinate conventions
----------------------
``EPN`` input columns are Earthward (nadir), Poleward (normal to the orbital
plane), and Normal (completing the right-handed system).  The orbital state
vectors are kilometres and kilometres per second, as returned by SGP4.
Magnetic-field units are unchanged by every rotation.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone

import numpy as np


def _vectors(values: np.ndarray, name: str) -> np.ndarray:
    """Return *values* as an ``(n, 3)`` float array, preserving missing data."""

    result = np.asarray(np.ma.filled(values, np.nan), dtype=float)
    if result.ndim != 2 or result.shape[1] != 3:
        raise ValueError(f"{name} must have shape (n, 3); received {result.shape}")
    return result


def epn_to_eci(
    vectors: np.ndarray,
    orbit_eci: np.ndarray,
    inclination_degrees: np.ndarray,
    raan_degrees: np.ndarray,
    *,
    backward: bool = False,
) -> np.ndarray:
    """Rotate magnetic vectors between EPN and ECI/GEI coordinates.

    Parameters
    ----------
    vectors
        EPN vectors when ``backward=False`` and ECI vectors otherwise.
    orbit_eci
        SGP4 position vectors in ECI coordinates.
    inclination_degrees, raan_degrees
        Osculating inclination and right ascension of the ascending node for
        every position.  These are derived from each propagated state rather
        than copied from the mean elements in the selected TLE.
    backward
        Reverse the transformation (ECI to EPN) when true.

    Notes
    -----
    The calculation calls the input frame E-P-N but evaluates its matrix
    in the right-handed E-N-P ordering.  The explicit column reordering below
    is therefore part of the scientific transform, not a cosmetic operation.
    """

    field = _vectors(vectors, "vectors")
    position = _vectors(orbit_eci, "orbit_eci")
    inclination = np.asarray(inclination_degrees, dtype=float).reshape(-1)
    raan = np.asarray(raan_degrees, dtype=float).reshape(-1)
    n_rows = field.shape[0]
    if position.shape[0] != n_rows or inclination.size != n_rows or raan.size != n_rows:
        raise ValueError("vectors, orbit_eci, inclination, and RAAN must have equal lengths")

    inclination = np.deg2rad(inclination)
    raan = np.deg2rad(raan)
    cos_i = np.cos(inclination)
    sin_i = np.sin(inclination)
    cos_node = np.cos(raan)
    sin_node = np.sin(raan)

    radius = np.linalg.norm(position, axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        cos_w = (position[:, 0] * cos_node + position[:, 1] * sin_node) / radius
    # Round-off can otherwise put a valid cosine infinitesimally outside [-1, 1].
    cos_w = np.clip(cos_w, -1.0, 1.0)
    sin_w = np.sqrt(np.maximum(0.0, 1.0 - cos_w**2))
    sin_w[position[:, 2] < 0.0] *= -1.0

    rotation = np.empty((n_rows, 3, 3), dtype=float)
    rotation[:, 0, 0] = -cos_node * cos_w + sin_node * sin_w * cos_i
    rotation[:, 0, 1] = -cos_node * sin_w - sin_node * cos_w * cos_i
    rotation[:, 0, 2] = sin_node * sin_i
    rotation[:, 1, 0] = -sin_node * cos_w - cos_node * sin_w * cos_i
    rotation[:, 1, 1] = -sin_node * sin_w + cos_node * cos_w * cos_i
    rotation[:, 1, 2] = -cos_node * sin_i
    rotation[:, 2, 0] = -sin_i * sin_w
    rotation[:, 2, 1] = sin_i * cos_w
    rotation[:, 2, 2] = cos_i

    if backward:
        enp = np.einsum("nji,nj->ni", rotation, field)
        return enp[:, [0, 2, 1]]

    enp = field[:, [0, 2, 1]]
    return np.einsum("nij,nj->ni", rotation, enp)


def _naive_utc(value: object) -> datetime:
    """Normalize a pandas/Python timestamp to the naive UTC form SpacePy expects."""

    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if not isinstance(value, datetime):
        value = np.datetime64(value).astype("datetime64[us]").astype(datetime)
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def eci_to_gsm(times: Sequence[object], vectors_eci: np.ndarray) -> np.ndarray:
    """Rotate ECI/GEI vectors to GSM using SpacePy at the supplied UTC times."""

    vectors = _vectors(vectors_eci, "vectors_eci")
    if len(times) != vectors.shape[0]:
        raise ValueError("times and vectors_eci must have equal lengths")
    if not len(times):
        return vectors.copy()

    try:
        import spacepy.coordinates as spcoords
        import spacepy.time as sptime
    except ImportError as exc:  # pragma: no cover - depends on optional runtime
        raise RuntimeError(
            "Legacy EPN conversion requires SpacePy. Install the project's full dependencies."
        ) from exc

    coordinates = spcoords.Coords(vectors, "GEI", "car")
    coordinates.ticks = sptime.Ticktock([_naive_utc(value) for value in times], "UTC")
    gsm = coordinates.convert("GSM", "car")
    result = np.column_stack((gsm.x, gsm.y, gsm.z)).astype(float, copy=False)

    # SpacePy does not consistently carry an input mask through every backend.
    missing_rows = ~np.isfinite(vectors).all(axis=1)
    result[missing_rows] = np.nan
    return result


def propagate_tle(
    line1: str,
    line2: str,
    times: Sequence[object],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Propagate a TLE and return ECI position, inclination, and RAAN arrays.

    The use of WGS-72 and conversion of each propagated state to osculating
    elements intentionally matches the paper calculation.
    """

    try:
        from sgp4.earth_gravity import wgs72
        from sgp4.ext import rv2coe
        from sgp4.io import twoline2rv
    except ImportError as exc:  # pragma: no cover - depends on optional runtime
        raise RuntimeError(
            "Legacy EPN conversion requires sgp4. Install the project's full dependencies."
        ) from exc

    orbit = twoline2rv(line1, line2, wgs72)
    count = len(times)
    positions = np.full((count, 3), np.nan, dtype=float)
    inclinations = np.full(count, np.nan, dtype=float)
    raans = np.full(count, np.nan, dtype=float)
    gravitational_parameter = 398600.4418  # km^3 s^-2

    for index, raw_time in enumerate(times):
        time = _naive_utc(raw_time)
        position, velocity = orbit.propagate(
            time.year,
            time.month,
            time.day,
            time.hour,
            time.minute,
            time.second + time.microsecond / 1_000_000.0,
        )
        positions[index] = position
        elements = rv2coe(position, velocity, gravitational_parameter)
        inclinations[index] = np.rad2deg(elements[3])
        raans[index] = np.rad2deg(elements[4])

    return positions, inclinations, raans


__all__ = ["eci_to_gsm", "epn_to_eci", "propagate_tle"]
