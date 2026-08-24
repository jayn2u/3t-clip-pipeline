"""Generic cache inventory hydration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from three_t_clip_pipeline.runtime.status import owned_destination, safe_relative_path
from three_t_clip_pipeline.services.storage import (
    ObjectMetadata,
    StorageClient,
    download_verified,
)

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class CacheEntry:
    """One immutable object declared by a generic cache inventory."""

    s3_uri: str
    destination: str
    size: int
    sha256: str

    def __post_init__(self) -> None:
        """Reject destinations outside the task-owned cache root."""
        _ = safe_relative_path(self.destination)


@dataclass(frozen=True, slots=True)
class HydratedCacheEntry:
    """A verified cache path and its immutable identity."""

    path: Path
    metadata: ObjectMetadata


def hydrate_cache(
    entries: tuple[CacheEntry, ...], root: Path, client: StorageClient
) -> tuple[HydratedCacheEntry, ...]:
    """Hydrate declared objects without deleting or mirroring unrelated cache state."""
    hydrated: list[HydratedCacheEntry] = []
    for entry in entries:
        destination = owned_destination(root, entry.destination)
        expected = ObjectMetadata(size=entry.size, sha256=entry.sha256)
        metadata = download_verified(client, entry.s3_uri, destination, expected)
        hydrated.append(HydratedCacheEntry(path=destination, metadata=metadata))
    return tuple(hydrated)
