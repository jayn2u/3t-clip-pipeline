"""Explicit Kubernetes context parsing at the CLI trust boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, NewType, override

KubernetesContext = NewType("KubernetesContext", str)
_CONTROL_END: Final = 32
_DELETE_CHARACTER: Final = 127


@dataclass(frozen=True, slots=True)
class ContextRequiredError(Exception):
    """The explicit Kubernetes context is blank or contains controls."""

    @override
    def __str__(self) -> str:
        """Return the stable input error code."""
        return "context_required"


def parse_context(value: str) -> KubernetesContext:
    """Parse a nonblank explicit context without normalizing its identity."""
    has_control = any(
        ord(character) < _CONTROL_END or ord(character) == _DELETE_CHARACTER for character in value
    )
    if not value.strip() or has_control:
        raise ContextRequiredError
    return KubernetesContext(value)
