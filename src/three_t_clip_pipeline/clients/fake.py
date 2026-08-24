"""Deterministic recording client for command verification."""

from __future__ import annotations

from typing import TYPE_CHECKING, final

if TYPE_CHECKING:
    from pathlib import Path

    from pydantic import JsonValue

from three_t_clip_pipeline.clients.types import ClientCall


@final
class FakeKubernetesClient:
    """Mutable fake whose purpose is recording externally visible calls."""

    def __init__(self, *, phases: tuple[str, ...] = ("Succeeded",)) -> None:
        """Create an empty call ledger with deterministic watch phases."""
        self.calls: list[ClientCall] = []
        self.persisted_mutations: int = 0
        self._phases: tuple[str, ...] = phases

    def inventory(self) -> None:
        """Record one GET and one LIST."""
        self.calls.extend(
            (
                ClientCall(verb="GET", resource="namespaces"),
                ClientCall(verb="LIST", resource="workflows.argoproj.io"),
            )
        )

    def server_dry_run(self, manifest: Path) -> None:
        """Record a non-persisting PATCH."""
        _ = manifest
        self.calls.append(ClientCall(verb="PATCH", resource="workflows.argoproj.io?dryRun=All"))

    def create_workflow(self, manifest: Path) -> str:
        """Record the sole persistent CREATE capability."""
        _ = manifest
        self.calls.append(ClientCall(verb="CREATE", resource="workflows.argoproj.io"))
        self.persisted_mutations += 1
        return "minimal"

    def workflow_phase(self, name: str) -> str:
        """Record a GET and return the configured final phase."""
        _ = name
        self.calls.append(ClientCall(verb="GET", resource="workflows.argoproj.io"))
        return self._phases[-1]

    def watch_workflow(self, name: str) -> tuple[str, ...]:
        """Record a watch GET and return configured phases."""
        _ = name
        self.calls.append(ClientCall(verb="GET", resource="workflows.argoproj.io?watch=true"))
        return self._phases

    def workflow_evidence(self, name: str) -> JsonValue:
        """Record a GET and return permit-list-compatible evidence."""
        _ = name
        self.calls.append(ClientCall(verb="GET", resource="workflows.argoproj.io"))
        return {
            "metadata": {"uid": "123e4567-e89b-12d3-a456-426614174000"},
            "status": {"phase": self._phases[-1]},
            "images": [],
            "objects": [],
            "marker": {"status": "unknown"},
        }
