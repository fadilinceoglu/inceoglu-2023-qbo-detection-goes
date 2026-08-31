"""Small, dependency-free helpers for released-file integrity checks."""

from __future__ import annotations

import hashlib
from pathlib import Path


def sha256_file(path: Path) -> str:
    """Return the lowercase SHA-256 digest of a file."""

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def manifest_checksum(manifest_path: Path, filename: str) -> str:
    """Return one validated digest from a standard ``SHA256SUMS`` file."""

    if not manifest_path.exists():
        raise RuntimeError(f"checksum manifest is missing: {manifest_path}")
    matches: list[str] = []
    for line_number, raw_line in enumerate(
        manifest_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line:
            continue
        fields = line.split(maxsplit=1)
        if len(fields) != 2:
            raise RuntimeError(
                f"malformed checksum line {line_number} in {manifest_path}"
            )
        checksum, listed_name = fields
        listed_name = listed_name.lstrip("*")
        try:
            valid_checksum = (
                len(checksum) == 64 and len(bytes.fromhex(checksum)) == 32
            )
        except ValueError:
            valid_checksum = False
        if not valid_checksum:
            raise RuntimeError(
                f"invalid SHA-256 on line {line_number} in {manifest_path}"
            )
        if listed_name == filename:
            matches.append(checksum.lower())
    if len(matches) != 1:
        qualifier = "no" if not matches else "multiple"
        raise RuntimeError(
            f"{qualifier} checksum entries for {filename} in {manifest_path}"
        )
    return matches[0]
