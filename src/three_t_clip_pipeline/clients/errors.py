"""Typed errors for external client execution."""

from dataclasses import dataclass
from typing import override


@dataclass(frozen=True, slots=True)
class ClientExecutionError(Exception):
    """A bounded argv-only client invocation failed."""

    code: str
    detail: str

    @override
    def __str__(self) -> str:
        """Return only the redaction-safe stable code."""
        return self.code
