"""Regression tests for direct lightweight and full pipeline dispatch."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable, Sequence

import pytest

from qbo_detection import analysis
from qbo_detection import cli
from qbo_detection.pipeline import PipelineOptions, ReproductionPipeline


def _capture_runs(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []

    def capture(
        name: str,
        entrypoint: Callable[[Sequence[str] | None], int],
        arguments: list[str],
    ) -> None:
        module = entrypoint.__module__.rsplit(".", 1)[-1]
        calls.append((module, *arguments))

    monkeypatch.setattr(ReproductionPipeline, "_run_stage", staticmethod(capture))
    return calls


def test_default_force_does_not_enter_raw_preparation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _capture_runs(monkeypatch)

    assert cli.main(["--force"]) == 0

    preparation = [call for call in calls if call[0] == "preparation"]
    assert len(preparation) == 1
    assert preparation[0][1] == "all"
    assert "--released-only" in preparation[0]
    assert "minute" not in preparation[0]
    assert "--force" not in preparation[0]


def test_full_force_dispatches_acquisition_and_raw_preparation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _capture_runs(monkeypatch)

    assert cli.main(["all", "--full", "--force"]) == 0

    assert calls[0][0:2] == ("acquisition", "all")
    preparation = [call for call in calls if call[0] == "preparation"]
    assert [call[1] for call in preparation] == ["minute", "quarterly"]
    assert all("--force" in call for call in preparation)
    assert all("--released-only" not in call for call in preparation)
    generated_figures = [
        int(call[1]) for call in calls if call[0] == "figures"
    ]
    assert generated_figures == [1, 2, 3, 4, 5, 6]


@pytest.mark.parametrize(
    "arguments",
    (
        ["all", "--full", "--start", "2020-01-01", "--end", "2020-01-03"],
        ["all", "--full", "--missions", "6"],
    ),
)
def test_subset_full_chain_is_rejected_before_dispatch(
    arguments: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _capture_runs(monkeypatch)

    with pytest.raises(SystemExit) as error:
        cli.main(arguments)

    assert error.value.code == 2
    assert calls == []


def test_explicit_acquisition_accepts_bounded_subset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _capture_runs(monkeypatch)

    result = cli.main(
        [
            "acquire",
            "--start",
            "2020-01-01",
            "--end",
            "2020-01-03",
            "--missions",
            "6",
        ]
    )

    assert result == 0
    assert len(calls) == 1
    call = calls[0]
    assert call[0:2] == ("acquisition", "all")
    assert call[call.index("--start") + 1] == "2020-01-01"
    assert call[call.index("--end") + 1] == "2020-01-03"
    assert call[call.index("--missions") + 1] == "6"


def test_lightweight_pipeline_verifies_silso_before_analysis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "source"
    sunspots = source_root / "sunspots"
    sunspots.mkdir(parents=True)
    target = sunspots / "SN_m_tot_V2.0.txt"
    target.write_text("changed SILSO bytes\n", encoding="utf-8")
    expected = hashlib.sha256(b"released SILSO bytes\n").hexdigest()
    (sunspots / "SHA256SUMS").write_text(
        f"{expected}  {target.name}\n", encoding="utf-8"
    )

    config_text = Path(PipelineOptions().config).read_text(encoding="utf-8")
    config_text = config_text.replace(
        'source = "data/source"', f'source = "{source_root}"'
    )
    config = tmp_path / "paper.toml"
    config.write_text(config_text, encoding="utf-8")

    monkeypatch.setattr(ReproductionPipeline, "prepare", lambda self, *, source_data: None)
    monkeypatch.setattr(
        ReproductionPipeline,
        "render_figures",
        lambda self, *, include_figure_1, figure_number=None: pytest.fail(
            "figures must not run after a failed input preflight"
        ),
    )
    pipeline = ReproductionPipeline(PipelineOptions(config=config))

    assert pipeline.status()["sunspot_input"] is False
    with pytest.raises(RuntimeError, match="failed SHA-256 verification"):
        pipeline.all(full=False)


def test_status_requires_quarterly_checksum_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quarterly = tmp_path / "processed" / "quarterly"
    quarterly.mkdir(parents=True)
    target = quarterly / "goes06_quarterly.csv"
    target.write_text("released checkpoint\n", encoding="utf-8")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    (quarterly / "SHA256SUMS").write_text(
        f"{digest}  {target.name}\n", encoding="utf-8"
    )
    monkeypatch.setattr(analysis, "ALL_MISSIONS", (6,))
    pipeline = ReproductionPipeline(
        PipelineOptions(processed_dir=tmp_path / "processed")
    )

    assert pipeline.status()["released_quarterly"] is True
    target.write_text("changed checkpoint\n", encoding="utf-8")
    assert pipeline.status()["released_quarterly"] is False
