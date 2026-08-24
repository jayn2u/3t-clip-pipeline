"""Shared workload render boundary for online commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, override

from pydantic import ValidationError

from three_t_clip_pipeline.contract import load_workload, validation_codes
from three_t_clip_pipeline.contract.io import WorkloadReadError, WorkloadYamlError
from three_t_clip_pipeline.render import render_workflow_bytes

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class CommandInputError(Exception):
    """Stable invalid-workload detail for an online command."""

    detail: str

    @override
    def __str__(self) -> str:
        """Return the stable input detail."""
        return self.detail


def render_workload_path(path: Path) -> bytes:
    """Load, validate, and deterministically render one workload."""
    try:
        workload = load_workload(path)
    except ValidationError as error:
        raise CommandInputError(detail=",".join(validation_codes(error))) from error
    except (WorkloadReadError, WorkloadYamlError) as error:
        raise CommandInputError(detail=f"input_error: {error}") from error
    return render_workflow_bytes(workload)
