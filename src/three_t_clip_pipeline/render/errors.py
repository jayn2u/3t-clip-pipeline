"""Typed renderer validation failures."""

from pathlib import Path
from typing import override


class SchemaLockError(Exception):
    """A packaged schema is missing, invalid, or differs from its lock."""

    code: str
    path: Path

    def __init__(self, *, code: str, path: Path) -> None:
        """Initialize a schema failure with its stable code and local path."""
        super().__init__(code, path)
        self.code = code
        self.path = path

    @override
    def __str__(self) -> str:
        return f"{self.code}: {self.path}"


class ClientValidationError(Exception):
    """The packaged local manifest validator could not validate a manifest."""

    code: str
    detail: str

    def __init__(self, *, code: str, detail: str) -> None:
        """Initialize a client-validation failure with stable context."""
        super().__init__(code, detail)
        self.code = code
        self.detail = detail

    @override
    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"
