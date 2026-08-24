"""Typed command operations with explicit read and mutation boundaries."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Final

from three_t_clip_pipeline.evidence import sanitize_evidence

if TYPE_CHECKING:
    from three_t_clip_pipeline.clients import KubernetesClient

_CACHE_DIRECTORY: Final = ".cache"
_SUCCEEDED: Final = "Succeeded"
_FAILED_PHASES: Final = frozenset(("Failed", "Error"))


def _manifest_file(rendered: bytes) -> tuple[TemporaryDirectory[str], Path]:
    cache = Path.cwd() / _CACHE_DIRECTORY
    cache.mkdir(parents=True, exist_ok=True)
    temporary = TemporaryDirectory(prefix="3t-online-", dir=cache)
    manifest = Path(temporary.name) / "workflow.yaml"
    _ = manifest.write_bytes(rendered)
    return temporary, manifest


def preflight(client: KubernetesClient) -> None:
    """Perform the documented authenticated read-only inventory."""
    client.inventory()


def server_validate(client: KubernetesClient, rendered: bytes) -> None:
    """Perform an authenticated server-side dry-run without persistence."""
    temporary, manifest = _manifest_file(rendered)
    with temporary:
        client.server_dry_run(manifest)


def submit(client: KubernetesClient, rendered: bytes, *, watch: bool) -> tuple[str, str | None]:
    """Create one Workflow and optionally observe its terminal phase."""
    temporary, manifest = _manifest_file(rendered)
    with temporary:
        name = client.create_workflow(manifest)
    if not watch:
        return name, None
    phases = client.watch_workflow(name)
    if not phases:
        return name, "Unknown"
    return name, phases[-1]


def status(client: KubernetesClient, workflow_name: str) -> str:
    """Read the authoritative Workflow phase."""
    return client.workflow_phase(workflow_name)


def write_evidence(client: KubernetesClient, workflow_name: str, output: Path) -> None:
    """Write deterministic permit-listed evidence without secret material."""
    sanitized = sanitize_evidence(client.workflow_evidence(workflow_name))
    output.parent.mkdir(parents=True, exist_ok=True)
    _ = output.write_text(
        json.dumps(sanitized, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def is_successful_phase(phase: str) -> bool:
    """Return whether a terminal phase is authoritative success."""
    return phase == _SUCCEEDED


def is_failed_phase(phase: str) -> bool:
    """Return whether a terminal phase is authoritative failure."""
    return phase in _FAILED_PHASES
