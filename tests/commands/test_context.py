from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from three_t_clip_pipeline.cli import app
from three_t_clip_pipeline.clients import FakeKubernetesClient
from three_t_clip_pipeline.commands import dependencies

ROOT = Path(__file__).resolve().parents[2]
WORKLOAD = ROOT / "examples/workload-minimal.yaml"


@pytest.mark.parametrize("blank_context", ["", "   ", "\t"])
@pytest.mark.parametrize(
    "command",
    [
        ["preflight", "--context", "{context}", "--environment-class", "development"],
        [
            "validate",
            str(WORKLOAD),
            "--server",
            "--context",
            "{context}",
            "--environment-class",
            "development",
        ],
        [
            "submit",
            str(WORKLOAD),
            "--context",
            "{context}",
            "--environment-class",
            "development",
        ],
        [
            "status",
            "minimal",
            "--context",
            "{context}",
            "--environment-class",
            "development",
        ],
        [
            "evidence",
            "minimal",
            "--context",
            "{context}",
            "--environment-class",
            "development",
            "--output",
            "evidence.json",
        ],
    ],
)
def test_blank_context_rejected_before_client_creation(
    monkeypatch: pytest.MonkeyPatch,
    command: list[str],
    blank_context: str,
) -> None:
    # Given: every online command receives an empty or whitespace-only context.
    creations: list[str] = []

    def forbidden_factory(context: str) -> FakeKubernetesClient:
        creations.append(context)
        return FakeKubernetesClient()

    monkeypatch.setattr(dependencies, "kubernetes_client", forbidden_factory)
    argv = [blank_context if value == "{context}" else value for value in command]

    # When: the CLI parses the invalid explicit context.
    result = CliRunner().invoke(app, argv)

    # Then: input fails stably before any external client exists.
    assert result.exit_code == 2
    assert result.stderr == "context_required\n"
    assert creations == []
