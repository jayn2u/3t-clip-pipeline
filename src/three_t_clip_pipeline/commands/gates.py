"""Pure authorization checks evaluated before external client creation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import override

from three_t_clip_pipeline.commands.models import EnvironmentClass, Operation


@dataclass(frozen=True, slots=True)
class AuthorizationDeniedError(Exception):
    """A selected operation is forbidden for its explicit environment class."""

    code: str

    @override
    def __str__(self) -> str:
        """Return the stable authorization code."""
        return self.code


def authorize(operation: Operation, environment: EnvironmentClass) -> None:
    """Reject persistent production mutation before constructing clients."""
    match operation:
        case Operation.SUBMIT if environment is EnvironmentClass.PRODUCTION:
            raise AuthorizationDeniedError(code="production_mutation_forbidden")
        case (
            Operation.PREFLIGHT
            | Operation.SERVER_VALIDATE
            | Operation.SUBMIT
            | Operation.STATUS
            | Operation.EVIDENCE
        ):
            return
