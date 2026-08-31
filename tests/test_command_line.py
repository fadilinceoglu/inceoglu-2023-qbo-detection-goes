"""Import-free syntax and command-line help checks."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import prepare_data
from conftest import REPOSITORY_ROOT, SCRIPTS_DIR


def test_all_public_scripts_compile() -> None:
    for path in sorted(SCRIPTS_DIR.glob("*.py")):
        compile(path.read_text(encoding="utf-8"), str(path), "exec")


def test_published_fixed_coverage_fraction_is_not_silently_inert(
    tmp_path: Path,
) -> None:
    source = (REPOSITORY_ROOT / "data/config/paper.toml").read_text(encoding="utf-8")
    changed = source.replace("minimum_bin_coverage = 0.75", "minimum_bin_coverage = 0.70")
    config = tmp_path / "paper.toml"
    config.write_text(changed, encoding="utf-8")

    with pytest.raises(ValueError, match="paper's 0.75 coverage criterion"):
        prepare_data.main(["all", "--config", str(config)])


CLI_NAMES = ["acquire_data.py", "prepare_data.py", "analyze.py", "make_figures.py"]
if (SCRIPTS_DIR / "reproduce.py").exists():
    CLI_NAMES.append("reproduce.py")


@pytest.mark.parametrize("script_name", CLI_NAMES)
def test_cli_help(script_name: str, tmp_path: Path) -> None:
    environment = os.environ.copy()
    environment["MPLCONFIGDIR"] = str(tmp_path / "matplotlib")
    completed = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / script_name), "--help"],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "usage:" in completed.stdout.lower()
