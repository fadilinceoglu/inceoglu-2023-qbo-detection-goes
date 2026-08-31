"""Regression tests for the lightweight and full calculation runner."""

from __future__ import annotations

from typing import Any

import pytest

import reproduce


def _capture_runs(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []

    def capture(script: str, *arguments: Any) -> None:
        calls.append((script, *(str(argument) for argument in arguments)))

    monkeypatch.setattr(reproduce, "_run", capture)
    return calls


def test_default_force_does_not_enter_raw_preparation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _capture_runs(monkeypatch)

    assert reproduce.main(["--force"]) == 0

    preparation = [call for call in calls if call[0] == "prepare_data.py"]
    assert len(preparation) == 1
    assert preparation[0][1] == "all"
    assert "--released-only" in preparation[0]
    assert "minute" not in preparation[0]
    assert "--force" not in preparation[0]


def test_full_force_dispatches_acquisition_and_raw_preparation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _capture_runs(monkeypatch)

    assert reproduce.main(["--full", "--force"]) == 0

    assert calls[0][0:2] == ("acquire_data.py", "all")
    preparation = [call for call in calls if call[0] == "prepare_data.py"]
    assert [call[1] for call in preparation] == ["minute", "quarterly"]
    assert all("--force" in call for call in preparation)
    assert all("--released-only" not in call for call in preparation)
    generated_figures = [
        int(call[1]) for call in calls if call[0] == "make_figures.py"
    ]
    assert generated_figures == [1, 2, 3, 4, 5, 6]


@pytest.mark.parametrize(
    "arguments",
    (
        ["--full", "--start", "2020-01-01", "--end", "2020-01-03"],
        ["--full", "--missions", "6"],
    ),
)
def test_subset_full_chain_is_rejected_before_dispatch(
    arguments: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _capture_runs(monkeypatch)

    with pytest.raises(SystemExit) as error:
        reproduce.main(arguments)

    assert error.value.code == 2
    assert calls == []


def test_explicit_acquisition_accepts_bounded_subset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _capture_runs(monkeypatch)

    result = reproduce.main(
        [
            "--stage",
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
    assert call[0:2] == ("acquire_data.py", "all")
    assert call[call.index("--start") + 1] == "2020-01-01"
    assert call[call.index("--end") + 1] == "2020-01-03"
    assert call[call.index("--missions") + 1] == "6"
