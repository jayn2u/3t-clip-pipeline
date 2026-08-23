"""Workload file boundary parsing."""

from __future__ import annotations

from typing import TYPE_CHECKING, override

import yaml
from pydantic import JsonValue, TypeAdapter, ValidationError

from three_t_clip_pipeline.contract.models import Workload

if TYPE_CHECKING:
    from pathlib import Path

_JSON_VALUE_ADAPTER: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)


class WorkloadReadError(Exception):
    """A workload file could not be read."""

    path: Path
    reason: str

    def __init__(self, *, path: Path, reason: str) -> None:
        """Initialize typed file-read failure context."""
        super().__init__(path, reason)
        self.path = path
        self.reason = reason

    @override
    def __str__(self) -> str:
        """Render the file and read failure."""
        return f"{self.path}: {self.reason}"


class WorkloadYamlError(Exception):
    """A workload file is not valid YAML data."""

    path: Path
    reason: str

    def __init__(self, *, path: Path, reason: str) -> None:
        """Initialize typed YAML failure context."""
        super().__init__(path, reason)
        self.path = path
        self.reason = reason

    @override
    def __str__(self) -> str:
        """Render the file and YAML parse failure."""
        return f"{self.path}: {self.reason}"


def load_workload(path: Path) -> Workload:
    """Read and validate a YAML or JSON workload at the trust boundary."""
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as error:
        raise WorkloadReadError(path=path, reason=str(error)) from error
    try:
        raw: JsonValue = _JSON_VALUE_ADAPTER.validate_python(yaml.safe_load(source))
    except yaml.YAMLError as error:
        raise WorkloadYamlError(path=path, reason=str(error)) from error
    return Workload.model_validate(raw)


def validation_codes(error: ValidationError) -> tuple[str, ...]:
    """Return deterministic ordered Pydantic error codes for CLI consumers."""
    return tuple(dict.fromkeys(item["type"] for item in error.errors()))
