"""Shared paths and imports for the lightweight reproduction tests."""

from __future__ import annotations

import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPOSITORY_ROOT / "scripts"
SOURCE_DIR = REPOSITORY_ROOT / "src"

# Exercise the installable package directly without requiring an editable
# installation in the source checkout.
sys.path.insert(0, str(SOURCE_DIR))
