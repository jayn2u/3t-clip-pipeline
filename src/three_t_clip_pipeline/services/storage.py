"""Verified object download primitives."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, override
from uuid import uuid4

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class ObjectMetadata:
    """Remote immutable object identity."""

    size: int
    sha256: str | None


class StorageClient(Protocol):
    """Narrow storage capability without delete or mirror operations."""

    def head(self, uri: str, timeout_seconds: float) -> ObjectMetadata:
        """Read immutable object metadata."""
        ...

    def download(self, uri: str, destination: Path, timeout_seconds: float) -> None:
        """Download an object to a caller-owned staged path."""
        ...


@dataclass(frozen=True, slots=True)
class DownloadError(Exception):
    """A staged download did not match its immutable declaration."""

    uri: str
    code: str

    @override
    def __str__(self) -> str:
        return f"{self.code}: {self.uri}"


def download_verified(
    client: StorageClient,
    uri: str,
    destination: Path,
    expected: ObjectMetadata,
    *,
    timeout_seconds: float = 15.0,
) -> ObjectMetadata:
    """Download beside the destination and atomically promote after verification."""
    remote = client.head(uri, min(timeout_seconds, 15.0))
    if remote.sha256 is None:
        raise DownloadError(uri, "missing_sha256_metadata")
    if remote != expected:
        raise DownloadError(uri, "metadata_mismatch")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staged = destination.with_name(f".{destination.name}.{uuid4().hex}.partial")
    try:
        client.download(uri, staged, min(timeout_seconds, 15.0))
        body = staged.read_bytes()
        actual = ObjectMetadata(size=len(body), sha256=hashlib.sha256(body).hexdigest())
        if actual != expected:
            raise DownloadError(uri, "payload_mismatch")
        _ = staged.replace(destination)
    finally:
        staged.unlink(missing_ok=True)
    return expected
