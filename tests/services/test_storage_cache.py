from __future__ import annotations

import hashlib
from pathlib import Path
from typing import final

import pytest

from three_t_clip_pipeline.services.cache import CacheEntry, hydrate_cache
from three_t_clip_pipeline.services.storage import DownloadError, ObjectMetadata


@final
class Storage:
    def __init__(self, payload: bytes, metadata_digest: str | None = None) -> None:
        self.payload = payload
        self.metadata_digest = metadata_digest

    def head(self, uri: str, timeout_seconds: float) -> ObjectMetadata:
        del uri, timeout_seconds
        return ObjectMetadata(
            size=len(self.payload),
            sha256=self.metadata_digest,
        )

    def download(self, uri: str, destination: Path, timeout_seconds: float) -> None:
        del uri, timeout_seconds
        _ = destination.write_bytes(self.payload)


def test_cache_hydration_atomically_replaces_verified_file(tmp_path: Path) -> None:
    payload = b"new-cache"
    digest = hashlib.sha256(payload).hexdigest()
    destination = tmp_path / "cache" / "item.bin"
    destination.parent.mkdir()
    _ = destination.write_bytes(b"old-cache")

    hydrated = hydrate_cache(
        (CacheEntry("s3://bucket/item", "item.bin", len(payload), digest),),
        tmp_path / "cache",
        Storage(payload, digest),
    )

    assert destination.read_bytes() == payload
    assert hydrated[0].path == destination
    assert not tuple(destination.parent.glob("*.partial"))


@pytest.mark.parametrize("metadata_digest", [None, "0" * 64])
def test_cache_rejects_missing_or_corrupt_metadata(
    tmp_path: Path, metadata_digest: str | None
) -> None:
    payload = b"cache"
    digest = hashlib.sha256(payload).hexdigest()

    with pytest.raises(DownloadError):
        _ = hydrate_cache(
            (CacheEntry("s3://bucket/item", "item.bin", len(payload), digest),),
            tmp_path,
            Storage(payload, metadata_digest),
        )

    assert not (tmp_path / "item.bin").exists()


def test_cache_rejects_path_traversal(tmp_path: Path) -> None:
    payload = b"cache"

    with pytest.raises(ValueError, match="safe relative"):
        _ = hydrate_cache(
            (
                CacheEntry(
                    "s3://bucket/item",
                    "../escape",
                    len(payload),
                    hashlib.sha256(payload).hexdigest(),
                ),
            ),
            tmp_path,
            Storage(payload),
        )
