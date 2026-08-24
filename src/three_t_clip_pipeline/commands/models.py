"""Closed command authorization and exit-code models."""

from enum import IntEnum, StrEnum, unique


@unique
class EnvironmentClass(StrEnum):
    """Explicit trust class for a named Kubernetes context."""

    EPHEMERAL = "ephemeral"
    DEVELOPMENT = "development"
    PRODUCTION = "production"


@unique
class Operation(StrEnum):
    """Online operation selected before client construction."""

    PREFLIGHT = "preflight"
    SERVER_VALIDATE = "server_validate"
    SUBMIT = "submit"
    STATUS = "status"
    EVIDENCE = "evidence"


@unique
class ExitCode(IntEnum):
    """Stable process outcomes for automation."""

    INPUT_INVALID = 2
    AUTHORIZATION_DENIED = 3
    CLIENT_FAILED = 4
    WORKFLOW_FAILED = 5
