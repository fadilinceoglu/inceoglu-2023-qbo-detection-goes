"""Shared paths and imports for the lightweight reproduction tests."""

from __future__ import annotations

import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPOSITORY_ROOT / "scripts"

# The public scripts are deliberately usable both as command-line programs and
# as small importable analysis modules.  Import them exactly as direct script
# execution does, without requiring a package installation.
sys.path.insert(0, str(SCRIPTS_DIR))
