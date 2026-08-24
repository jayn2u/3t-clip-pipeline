from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from typer.testing import CliRunner

from three_t_clip_pipeline.cli import app
from three_t_clip_pipeline.clients import ClientCall, FakeKubernetesClient
from three_t_clip_pipeline.commands import dependencies

if TYPE_CHECKING:
    import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKLOAD = ROOT / "examples/workload-minimal.yaml"


def test_production_read_only_preflight_and_server_dry_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an explicitly named production-class context and a recording client.
    client = FakeKubernetesClient()

    def client_factory(_context: str) -> FakeKubernetesClient:
        return client

    monkeypatch.setattr(dependencies, "kubernetes_client", client_factory)
    runner = CliRunner()

    # When: preflight and authenticated server validation are requested.
    preflight = runner.invoke(
        app,
        ["preflight", "--context", "prod-a", "--environment-class", "production"],
    )
    validation = runner.invoke(
        app,
        [
            "validate",
            str(WORKLOAD),
            "--server",
            "--context",
            "prod-a",
            "--environment-class",
            "production",
        ],
    )

    # Then: only GET/LIST/dry-run PATCH calls occurred and nothing persisted.
    assert preflight.exit_code == 0
    assert preflight.stdout == "READ_ONLY context=prod-a environment=production\n"
    assert validation.exit_code == 0
    assert validation.stdout == "SERVER_VALID READ_ONLY\n"
    assert [call.verb for call in client.calls] == ["GET", "LIST", "PATCH"]
    assert client.persisted_mutations == 0


def test_production_submit_rejected_before_client_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a factory that records any attempted client creation.
    creations: list[str] = []

    def forbidden_factory(context: str) -> FakeKubernetesClient:
        creations.append(context)
        return FakeKubernetesClient()

    monkeypatch.setattr(dependencies, "kubernetes_client", forbidden_factory)

    # When: submit is requested for a production-class environment.
    result = CliRunner().invoke(
        app,
        [
            "submit",
            str(WORKLOAD),
            "--context",
            "prod-a",
            "--environment-class",
            "production",
        ],
    )

    # Then: authorization fails before any external client exists.
    assert result.exit_code == 3
    assert result.stderr == "production_mutation_forbidden\n"
    assert creations == []


def test_secret_flag_rejected_before_client_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a credential canary and a factory that must not be called.
    canary = "CREDENTIAL_CANARY_do_not_emit"
    creations: list[str] = []

    def forbidden_factory(context: str) -> FakeKubernetesClient:
        creations.append(context)
        return FakeKubernetesClient()

    monkeypatch.setattr(dependencies, "kubernetes_client", forbidden_factory)

    # When: a forbidden secret-bearing argv flag is supplied.
    result = CliRunner().invoke(
        app,
        [
            "submit",
            str(WORKLOAD),
            "--context",
            "dev-a",
            "--environment-class",
            "development",
            "--token",
            canary,
        ],
    )

    # Then: the stable rejection contains neither the value nor a client call.
    assert result.exit_code == 3
    assert result.stderr == "secret_flag_forbidden\n"
    assert canary not in result.output
    assert creations == []


def test_only_submit_persists_a_workflow(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Given: one recording client shared across every online command.
    client = FakeKubernetesClient(phases=("Running", "Succeeded"))

    def client_factory(_context: str) -> FakeKubernetesClient:
        return client

    monkeypatch.setattr(dependencies, "kubernetes_client", client_factory)
    evidence_path = tmp_path / "evidence.json"
    runner = CliRunner()

    # When: all read-only commands and one submit are invoked.
    commands = (
        ["preflight", "--context", "dev-a", "--environment-class", "development"],
        [
            "validate",
            str(WORKLOAD),
            "--server",
            "--context",
            "dev-a",
            "--environment-class",
            "development",
        ],
        [
            "status",
            "minimal",
            "--context",
            "dev-a",
            "--environment-class",
            "development",
        ],
        [
            "evidence",
            "minimal",
            "--context",
            "dev-a",
            "--environment-class",
            "development",
            "--output",
            str(evidence_path),
        ],
        [
            "submit",
            str(WORKLOAD),
            "--context",
            "dev-a",
            "--environment-class",
            "development",
            "--watch",
        ],
    )
    results = [runner.invoke(app, command) for command in commands]

    # Then: exactly one CREATE exists and it belongs to submit.
    assert [result.exit_code for result in results] == [0, 0, 0, 0, 0]
    assert [call for call in client.calls if call.verb == "CREATE"] == [
        ClientCall(verb="CREATE", resource="workflows.argoproj.io")
    ]
    assert client.persisted_mutations == 1
    assert results[-1].stdout.endswith("WORKFLOW_SUCCEEDED\n")
    assert evidence_path.read_bytes() == (ROOT / "tests/commands/golden/evidence.json").read_bytes()


def test_help_has_no_secret_bearing_option() -> None:
    # Given: the public root help surface.
    result = CliRunner().invoke(app, ["--help"])

    # When: automation scans for prohibited credential option names.
    normalized = result.stdout.casefold()

    # Then: no secret-bearing flag is advertised.
    assert result.exit_code == 0
    assert all(word not in normalized for word in ("secret", "password", "token", "access-key"))


def test_online_command_requires_explicit_environment_before_client_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an online status command with a context but no environment class.
    creations: list[str] = []

    def forbidden_factory(context: str) -> FakeKubernetesClient:
        creations.append(context)
        return FakeKubernetesClient()

    monkeypatch.setattr(dependencies, "kubernetes_client", forbidden_factory)

    # When: the incomplete command is parsed.
    result = CliRunner().invoke(app, ["status", "minimal", "--context", "dev-a"])

    # Then: parsing fails and the external boundary is untouched.
    assert result.exit_code == 2
    assert creations == []


def test_watch_failure_is_not_reported_as_success(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a Workflow watch whose authoritative terminal phase is Failed.
    client = FakeKubernetesClient(phases=("Running", "Failed"))

    def client_factory(_context: str) -> FakeKubernetesClient:
        return client

    monkeypatch.setattr(dependencies, "kubernetes_client", client_factory)

    # When: submit watches through the terminal phase.
    result = CliRunner().invoke(
        app,
        [
            "submit",
            str(WORKLOAD),
            "--context",
            "dev-a",
            "--environment-class",
            "development",
            "--watch",
        ],
    )

    # Then: the stable failure exit contains no misleading success assertion.
    assert result.exit_code == 5
    assert "WORKFLOW_TERMINAL phase=Failed" in result.stderr
    assert "WORKFLOW_SUCCEEDED" not in result.output
