"""Portable bundle and verified-cache invariants."""

from __future__ import annotations

import hashlib
import tarfile
from pathlib import Path
from typing import final

import pytest

from three_t_clip_pipeline.services.bundle import create_bundle
from three_t_clip_pipeline.services.cache import CacheEntry, hydrate_cache
from three_t_clip_pipeline.services.storage import DownloadError, ObjectMetadata


@final
class Storage:
    """Minimal storage implementation for observable verified downloads."""

    def __init__(self, payload: bytes, digest: str | None) -> None:
        self.payload = payload
        self.digest = digest

    def head(self, uri: str, timeout_seconds: float) -> ObjectMetadata:
        del uri, timeout_seconds
        return ObjectMetadata(size=len(self.payload), sha256=self.digest)

    def download(self, uri: str, destination: Path, timeout_seconds: float) -> None:
        del uri, timeout_seconds
        _ = destination.write_bytes(self.payload)


@pytest.mark.parametrize("kept_name", ["job.py", "config/job.yaml", "assets/tokenizer.bin"])
def test_bundle_is_deterministic_and_excludes_nonportable_paths(
    tmp_path: Path, kept_name: str
) -> None:
    # Given
    source = tmp_path / "consumer"
    kept = source / kept_name
    kept.parent.mkdir(parents=True)
    _ = kept.write_text("portable\n", encoding="utf-8")
    for excluded in (".git", "results", "cache", ".credentials", ".platform"):
        path = source / excluded
        path.mkdir()
        _ = (path / "private").write_text("private\n", encoding="utf-8")
    (source / "external-link").symlink_to(tmp_path / "outside")

    # When
    first = create_bundle(source, tmp_path / "first.tar.gz")
    second = create_bundle(source, tmp_path / "second.tar.gz")

    # Then
    assert first.sha256 == second.sha256
    assert first.size == second.size
    with tarfile.open(first.path, "r:gz") as archive:
        assert archive.getnames() == [kept_name]


@pytest.mark.parametrize(
    ("bucket", "destination"),
    [("object-a", "models/item.bin"), ("object-b", "indexes/shard.bin")],
)
def test_inventory_hydration_uses_generic_store_and_atomic_destination(
    tmp_path: Path, bucket: str, destination: str
) -> None:
    # Given
    payload = f"payload:{bucket}".encode()
    digest = hashlib.sha256(payload).hexdigest()
    target = tmp_path / destination
    target.parent.mkdir(parents=True)
    _ = target.write_bytes(b"previous")

    # When
    hydrated = hydrate_cache(
        (CacheEntry(f"s3://{bucket}/immutable.bin", destination, len(payload), digest),),
        tmp_path,
        Storage(payload, digest),
    )

    # Then
    assert hydrated[0].path == target
    assert target.read_bytes() == payload
    assert not tuple(tmp_path.rglob("*.partial"))


@pytest.mark.parametrize("digest", [None, "f" * 64])
def test_unverified_inventory_preserves_existing_destination(
    tmp_path: Path, digest: str | None
) -> None:
    # Given
    payload = b"candidate"
    expected = hashlib.sha256(payload).hexdigest()
    target = tmp_path / "cache/object.bin"
    target.parent.mkdir()
    _ = target.write_bytes(b"trusted")

    # When / Then
    with pytest.raises(DownloadError):
        _ = hydrate_cache(
            (CacheEntry("s3://store/object", "cache/object.bin", len(payload), expected),),
            tmp_path,
            Storage(payload, digest),
        )
    assert target.read_bytes() == b"trusted"
