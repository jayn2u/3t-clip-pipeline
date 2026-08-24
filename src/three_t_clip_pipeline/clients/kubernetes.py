"""Argv-only kubectl implementation of the Kubernetes boundary."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from pydantic import JsonValue, TypeAdapter, ValidationError

from three_t_clip_pipeline.clients.errors import ClientExecutionError

if TYPE_CHECKING:
    from pathlib import Path

_JSON: Final = TypeAdapter(dict[str, JsonValue])
_TIMEOUT_SECONDS: Final = 30
_WATCH_TIMEOUT_SECONDS: Final = 600


@dataclass(frozen=True, slots=True)
class KubectlClient:
    """Kubernetes CLI adapter pinned to one explicit context."""

    context: str
    binary: str = "kubectl"

    def _run(self, args: tuple[str, ...], *, timeout: int = _TIMEOUT_SECONDS) -> str:
        command = [self.binary, "--context", self.context, *args]
        try:
            completed = subprocess.run(  # noqa: S603
                command,
                check=False,
                capture_output=True,
                text=True,
                shell=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as error:
            raise ClientExecutionError(code="client_timeout", detail=str(error)) from error
        if completed.returncode != 0:
            raise ClientExecutionError(
                code="client_command_failed",
                detail=completed.stderr.strip(),
            )
        return completed.stdout

    def _json(self, args: tuple[str, ...]) -> dict[str, JsonValue]:
        try:
            return _JSON.validate_json(self._run(args))
        except ValidationError as error:
            raise ClientExecutionError(code="client_output_invalid", detail=str(error)) from error

    def inventory(self) -> None:
        """Read the namespace and list Workflows."""
        _ = self._run(("get", "namespace", "three-t-pipeline", "-o", "name"))
        _ = self._run(("get", "workflow", "-n", "three-t-pipeline", "-o", "name"))

    def server_dry_run(self, manifest: Path) -> None:
        """Apply the manifest with server dry-run and a fixed field manager."""
        _ = self._json(
            (
                "apply",
                "--server-side",
                "--dry-run=server",
                "--field-manager=three-t-pipeline-plan",
                "-f",
                str(manifest),
                "-o",
                "json",
            )
        )

    def create_workflow(self, manifest: Path) -> str:
        """Create one Workflow from a rendered manifest."""
        raw = self._run(
            (
                "create",
                "-f",
                str(manifest),
                "-o",
                "jsonpath={.metadata.name}",
            )
        ).strip()
        if not raw:
            raise ClientExecutionError(code="workflow_name_missing", detail="empty name")
        return raw

    def workflow_phase(self, name: str) -> str:
        """Read one Workflow's authoritative phase."""
        return self._run(
            (
                "get",
                "workflow",
                name,
                "-n",
                "three-t-pipeline",
                "-o",
                "jsonpath={.status.phase}",
            )
        ).strip()

    def watch_workflow(self, name: str) -> tuple[str, ...]:
        """Run a bounded Workflow watch and return emitted phases."""
        output = self._run(
            (
                "get",
                "workflow",
                name,
                "-n",
                "three-t-pipeline",
                "--watch-only",
                "-o",
                "jsonpath={.status.phase}{'\\n'}",
            ),
            timeout=_WATCH_TIMEOUT_SECONDS,
        )
        return tuple(phase for line in output.splitlines() if (phase := line.strip()))

    def workflow_evidence(self, name: str) -> JsonValue:
        """Read one complete Workflow object for later sanitization."""
        return self._json(("get", "workflow", name, "-n", "three-t-pipeline", "-o", "json"))
