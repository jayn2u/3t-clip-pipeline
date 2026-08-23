import socket
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from three_t_clip_pipeline.cli import app

ROOT = Path(__file__).resolve().parents[2]


def test_plan_stdout_is_offline_golden(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden_call(*_args: object, **_kwargs: object) -> None:
        pytest.fail("offline plan attempted an external call")

    monkeypatch.setattr(subprocess, "run", forbidden_call)
    monkeypatch.setattr(socket, "create_connection", forbidden_call)
    result = CliRunner().invoke(app, ["plan", "examples/workload-minimal.yaml", "--output", "-"])

    assert result.exit_code == 0
    assert result.stdout.encode() == (ROOT / "tests/golden/workflow-v1alpha1.yaml").read_bytes()


def test_client_validation_invokes_only_local_wrapper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def local_validator(
        command: list[str],
        *,
        cwd: Path,
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        assert cwd == ROOT
        assert check is False
        assert capture_output is True
        assert text is True
        calls.append(tuple(command))
        return subprocess.CompletedProcess(command, 0, "CLIENT_VALID\n", "")

    monkeypatch.setattr(subprocess, "run", local_validator)
    result = CliRunner().invoke(app, ["validate", "examples/workload-minimal.yaml", "--client"])

    assert result.exit_code == 0
    assert result.stdout == "CLIENT_VALID\n"
    assert len(calls) == 1
    assert calls[0][0] == str(ROOT / "scripts/validate-manifest-local.sh")
    assert calls[0][1].endswith("/workflow.yaml")
