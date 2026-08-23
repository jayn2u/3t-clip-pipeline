from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Final, override
from urllib.parse import urlsplit

from pydantic import ValidationError

from three_t_clip_pipeline.runtime.status import (
    CommitMarker,
    MarkerResult,
    ObjectRecord,
    PublicationManifest,
    PublicationState,
    commit_marker_key,
    publication_manifest_key,
    runtime_json_bytes,
)

_SHA256_METADATA: Final = "sha256"


@dataclass(frozen=True, slots=True)
class FakeS3PreconditionFailedError(Exception):
    key: str

    @override
    def __str__(self) -> str:
        return f"conditional create failed for {self.key}"


@dataclass(frozen=True, slots=True)
class FakeS3TimeoutError(Exception):
    key: str

    @override
    def __str__(self) -> str:
        return f"operation timed out for {self.key}"


@dataclass(frozen=True, slots=True)
class FakeS3CancelledError(Exception):
    key: str

    @override
    def __str__(self) -> str:
        return f"operation cancelled for {self.key}"


@dataclass(frozen=True, slots=True)
class FakeS3ChecksumMismatchError(Exception):
    key: str

    @override
    def __str__(self) -> str:
        return f"checksum mismatch for {self.key}"


@dataclass(frozen=True, slots=True)
class _StoredObject:
    body: bytes
    metadata: tuple[tuple[str, str], ...]


class FakeS3:
    """Deterministic in-memory S3 semantics for portable conformance tests."""

    def __init__(self) -> None:
        self._objects: dict[str, _StoredObject] = {}
        self._corrupt_gets: set[str] = set()
        self._timeout_puts: set[str] = set()
        self._cancelled_puts: set[str] = set()
        self.put_calls: int = 0
        self.get_calls: int = 0
        self.head_calls: int = 0
        self.conditional_marker_attempts: int = 0

    def seed(self, key: str, body: bytes, metadata: dict[str, str] | None = None) -> None:
        self._objects[key] = _StoredObject(body, tuple(sorted((metadata or {}).items())))

    def corrupt_get(self, key: str) -> None:
        self._corrupt_gets.add(key)

    def delay_put_until_timeout(self, key: str) -> None:
        self._timeout_puts.add(key)

    def cancel_put(self, key: str) -> None:
        self._cancelled_puts.add(key)

    def put(
        self,
        key: str,
        body: bytes,
        *,
        metadata: dict[str, str] | None = None,
        if_none_match: bool = False,
    ) -> None:
        self.put_calls += 1
        if key.endswith("/COMMITTED.json"):
            self.conditional_marker_attempts += 1
        if key in self._timeout_puts:
            self._timeout_puts.remove(key)
            raise FakeS3TimeoutError(key)
        if key in self._cancelled_puts:
            self._cancelled_puts.remove(key)
            raise FakeS3CancelledError(key)
        if if_none_match and key in self._objects:
            raise FakeS3PreconditionFailedError(key)
        self._objects[key] = _StoredObject(body, tuple(sorted((metadata or {}).items())))

    def get(self, key: str) -> bytes:
        self.get_calls += 1
        stored = self._objects[key]
        if key in self._corrupt_gets:
            return stored.body + b"corrupt"
        return stored.body

    def head(self, key: str) -> ObjectRecord:
        self.head_calls += 1
        stored = self._objects[key]
        metadata = dict(stored.metadata)
        digest = metadata.get(_SHA256_METADATA)
        if digest is None:
            digest = hashlib.sha256(stored.body).hexdigest()
        return ObjectRecord(key=key, size=len(stored.body), sha256=digest)

    def object_keys(self) -> tuple[str, ...]:
        return tuple(sorted(self._objects))

    def contains(self, key: str) -> bool:
        return key in self._objects

    def marker_count(self, run_prefix: str) -> int:
        return int(f"{run_prefix}/COMMITTED.json" in self._objects)


class FakeS3RuntimeStore:
    """Runtime store adapter backed by FakeS3's observable object state."""

    def __init__(self, s3: FakeS3) -> None:
        self.s3: FakeS3 = s3

    def download_verified(self, source_uri: str, destination: Path, sha256: str) -> ObjectRecord:
        parsed = urlsplit(source_uri)
        key = f"{parsed.netloc}/{parsed.path.lstrip('/')}"
        content = self.s3.get(key)
        digest = hashlib.sha256(content).hexdigest()
        if digest != sha256:
            raise FakeS3ChecksumMismatchError(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        _ = destination.write_bytes(content)
        return ObjectRecord(key=destination.as_posix(), size=len(content), sha256=digest)

    def upload_immutable(self, source: Path, key: str) -> ObjectRecord:
        content = source.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        self.s3.put(key, content, metadata={_SHA256_METADATA: digest}, if_none_match=True)
        return ObjectRecord(key=key, size=len(content), sha256=digest)

    def put_manifest(self, key: str, payload: bytes) -> None:
        self.s3.put(key, payload, if_none_match=True)

    def get_manifest(self, key: str) -> bytes:
        return self.s3.get(key)

    def head(self, key: str) -> ObjectRecord:
        return self.s3.head(key)

    def create_marker(self, key: str, payload: bytes) -> MarkerResult:
        try:
            self.s3.put(key, payload, if_none_match=True)
        except FakeS3PreconditionFailedError:
            try:
                existing = CommitMarker.model_validate_json(self.s3.get(key))
                candidate = CommitMarker.model_validate_json(payload)
            except ValidationError:
                return MarkerResult.COLLISION
            existing_digest = hashlib.sha256(self.s3.get(key)).digest()
            candidate_digest = hashlib.sha256(payload).digest()
            if existing.run_uid == candidate.run_uid and existing_digest == candidate_digest:
                return MarkerResult.ALREADY_EXISTS
            return MarkerResult.COLLISION
        return MarkerResult.CREATED

    def write_terminal_publication(self, run_prefix: str, run_uid: str, diagnostic: str) -> None:
        state = PublicationState.CANCELLED if diagnostic == "cancelled" else PublicationState.FAILED
        manifest = PublicationManifest(
            publicationState=state, objects=(), diagnostics=(diagnostic,)
        )
        self.put_manifest(
            publication_manifest_key(run_prefix, run_uid), runtime_json_bytes(manifest)
        )

    def is_committed(self, run_prefix: str, run_uid: str) -> bool:
        marker_key = commit_marker_key(run_prefix)
        try:
            marker = CommitMarker.model_validate_json(self.s3.get(marker_key))
        except (KeyError, ValidationError):
            return False
        if marker.run_uid != run_uid or len({item.key for item in marker.objects}) != len(
            marker.objects
        ):
            return False
        payload_keys = {
            key
            for key in self.s3.object_keys()
            if key.startswith(f"{run_prefix}/") and "/.staging/" not in key and key != marker_key
        }
        if payload_keys != {item.key for item in marker.objects}:
            return False
        try:
            return all(
                self.head(item.key) == item
                and hashlib.sha256(self.s3.get(item.key)).hexdigest() == item.sha256
                for item in marker.objects
            )
        except KeyError:
            return False
