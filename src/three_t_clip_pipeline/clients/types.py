"""Typed Kubernetes client boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path

    from pydantic import JsonValue


@dataclass(frozen=True, slots=True)
class ClientCall:
    """One externally observable Kubernetes API operation."""

    verb: str
    resource: str


class KubernetesClient(Protocol):
    """Narrow capabilities used by the operator CLI."""

    def inventory(self) -> None:
        """Read required namespace and Workflow inventory."""
        ...

    def server_dry_run(self, manifest: Path) -> None:
        """Submit a non-persisting server-side dry-run PATCH."""
        ...

    def create_workflow(self, manifest: Path) -> str:
        """Persist one Workflow and return its server name."""
        ...

    def workflow_phase(self, name: str) -> str:
        """Read one Workflow phase."""
        ...

    def watch_workflow(self, name: str) -> tuple[str, ...]:
        """Observe Workflow phases through a bounded watch."""
        ...

    def workflow_evidence(self, name: str) -> JsonValue:
        """Read raw Workflow evidence for sanitization."""
        ...
