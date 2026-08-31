"""Repository locations shared by the QBO reproduction package."""

from __future__ import annotations

from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPOSITORY_ROOT / "data" / "config" / "paper.toml"


def resolve_repository_path(value: str | Path) -> Path:
    """Resolve a configured path against the checkout, never the process CWD."""

    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (REPOSITORY_ROOT / path).resolve()


__all__ = ["DEFAULT_CONFIG", "REPOSITORY_ROOT", "resolve_repository_path"]
