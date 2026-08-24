from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from three_t_clip_pipeline.clients import FakeKubernetesClient, KubectlClient
from three_t_clip_pipeline.clients.errors import ClientExecutionError


def test_kubectl_client_uses_argv_and_explicit_context(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given: a runner that records argv without invoking a shell.
    calls: list[tuple[str, ...]] = []

    def run(command: list[str], **kwargs: bool | str | int) -> subprocess.CompletedProcess[str]:
        assert kwargs["shell"] is False
        calls.append(tuple(command))
        return subprocess.CompletedProcess(command, 0, '{"metadata":{"uid":"u"}}', "")

    monkeypatch.setattr(subprocess, "run", run)
    manifest = tmp_path / "workflow.yaml"
    _ = manifest.write_text("kind: Workflow\n")

    # When: server dry-run is executed.
    client = KubectlClient(context="named-context")
    client.server_dry_run(manifest)

    # Then: argv pins context, field manager, and server-side dry-run.
    assert calls == [
        (
            "kubectl",
            "--context",
            "named-context",
            "apply",
            "--server-side",
            "--dry-run=server",
            "--field-manager=three-t-pipeline-plan",
            "-f",
            str(manifest),
            "-o",
            "json",
        )
    ]


@pytest.mark.parametrize("phase", ["Succeeded", "Failed", "Error"])
def test_watch_accepts_only_terminal_phases(phase: str) -> None:
    # Given: a fake stream ending in a terminal Argo phase.
    client = FakeKubernetesClient(phases=("Pending", "Running", phase))

    # When: the workflow is watched.
    observed = tuple(client.watch_workflow("workflow-a"))

    # Then: the terminal phase is preserved without inferred success.
    assert observed[-1] == phase


def test_watch_timeout_has_stable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: an argv runner whose bounded watch reaches its deadline.
    def timeout(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=["kubectl"], timeout=600)

    monkeypatch.setattr(subprocess, "run", timeout)

    # When: the real client watch boundary is invoked.
    with pytest.raises(ClientExecutionError) as captured:
        _ = KubectlClient(context="dev-a").watch_workflow("workflow-a")

    # Then: automation receives the stable client timeout code.
    assert str(captured.value) == "client_timeout"


def test_watch_interrupt_is_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: an operator interruption during the watch subprocess.
    def interrupted(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise KeyboardInterrupt

    monkeypatch.setattr(subprocess, "run", interrupted)

    # When: the watch boundary is interrupted.
    with pytest.raises(KeyboardInterrupt):
        _ = KubectlClient(context="dev-a").watch_workflow("workflow-a")

    # Then: no success or client-failure result is synthesized.
