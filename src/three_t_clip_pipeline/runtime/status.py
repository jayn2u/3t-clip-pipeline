"""Runtime status boundary models."""

from __future__ import annotations

import json
from enum import StrEnum, unique
from pathlib import Path, PurePosixPath
from typing import Annotated, ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, StringConstraints

Sha256 = Annotated[StrictStr, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
INIT_MANIFEST_NAME: Final = "init.json"
PUBLICATION_MANIFEST_NAME: Final = "publication.json"
COMMIT_MARKER_NAME: Final = "COMMITTED.json"


def safe_relative_path(value: str) -> str:
    """Parse a path that cannot escape a task-owned root lexically."""
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or "\\" in value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        message = "path must be a safe relative POSIX path"
        raise ValueError(message)
    return value


def owned_destination(root: Path, relative: str) -> Path:
    """Resolve a safe destination without following a pre-existing escape symlink."""
    destination = root / safe_relative_path(relative)
    if not destination.resolve(strict=False).is_relative_to(root.resolve()):
        message = "destination must remain inside its task-owned root"
        raise ValueError(message)
    return destination


def init_manifest_key(run_prefix: str, run_uid: str) -> str:
    """Return the immutable per-attempt initialization manifest key."""
    staging = f"{safe_relative_path(run_prefix)}/.staging/{safe_relative_path(run_uid)}"
    return f"{staging}/{INIT_MANIFEST_NAME}"


def publication_manifest_key(run_prefix: str, run_uid: str) -> str:
    """Return the immutable per-attempt publication manifest key."""
    staging = f"{safe_relative_path(run_prefix)}/.staging/{safe_relative_path(run_uid)}"
    return f"{staging}/{PUBLICATION_MANIFEST_NAME}"


def commit_marker_key(run_prefix: str) -> str:
    """Return the sole reader-visible marker key."""
    return f"{safe_relative_path(run_prefix)}/{COMMIT_MARKER_NAME}"


class RuntimeModel(BaseModel):
    """Apply strict immutable parsing to runtime trust-boundary data."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True, populate_by_name=True
    )


def runtime_json_bytes(model: RuntimeModel) -> bytes:
    """Serialize one strict runtime model as canonical newline-terminated JSON."""
    payload = model.model_dump(mode="json", by_alias=True)
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()


@unique
class MainStatus(StrEnum):
    """Stable main-container terminal outcomes consumed by the finalizer."""

    SUCCEEDED = "Succeeded"
    FAILED = "Failed"
    ERROR = "Error"
    CANCELLED = "Cancelled"
    TIMED_OUT = "TimedOut"


@unique
class PublicationState(StrEnum):
    """Terminal publisher outcomes."""

    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


@unique
class MarkerResult(StrEnum):
    """Conditional marker-write outcomes."""

    CREATED = "created"
    ALREADY_EXISTS = "already_exists"
    COLLISION = "collision"
    REFUSED = "refused"


class ObjectRecord(RuntimeModel):
    """Immutable size and checksum identity for one runtime object."""

    key: StrictStr = Field(min_length=1)
    size: Annotated[StrictInt, Field(ge=0)]
    sha256: Sha256


class InitManifest(RuntimeModel):
    """Uploaded proof that initialization completed before main started."""

    schema_: Literal["runtime-init/v1"] = Field(default="runtime-init/v1", alias="schema")
    state: Literal["completed"] = "completed"
    objects: tuple[ObjectRecord, ...]


class PublicationManifest(RuntimeModel):
    """Publisher terminal state and immutable uploaded-object inventory."""

    schema_: Literal["runtime-publication/v1"] = Field(
        default="runtime-publication/v1", alias="schema"
    )
    publication_state: PublicationState = Field(alias="publicationState")
    objects: tuple[ObjectRecord, ...]
    diagnostics: tuple[StrictStr, ...] = ()


class CommitMarker(RuntimeModel):
    """Minimal Task 5 marker handoff expanded by the publication service."""

    schema_: Literal["runtime-commit/v1"] = Field(default="runtime-commit/v1", alias="schema")
    run_uid: StrictStr = Field(alias="runUid", min_length=1)
    objects: tuple[ObjectRecord, ...]


class CacheObjectPayload(RuntimeModel):
    """One verified cache object in an init command request."""

    s3_uri: StrictStr = Field(alias="s3Uri", min_length=1)
    sha256: StrictStr = Field(alias="inventorySha256", pattern=r"^[0-9a-f]{64}$")
    destination: StrictStr = Field(min_length=1)
    read_only: bool = Field(default=True, alias="readOnly", strict=True)


class InitPayload(RuntimeModel):
    """JSON boundary consumed by the bundle-init command."""

    bucket: StrictStr = Field(min_length=1)
    run_prefix: StrictStr = Field(alias="runPrefix", min_length=1)
    run_uid: StrictStr = Field(alias="runUid", min_length=1)
    bundle_uri: StrictStr = Field(alias="bundleUri", min_length=1)
    bundle_sha256: StrictStr = Field(alias="bundleSha256", pattern=r"^[0-9a-f]{64}$")
    cache_objects: tuple[CacheObjectPayload, ...] = Field(default=(), alias="cacheObjects")


class PublisherPayload(RuntimeModel):
    """JSON boundary consumed by the native publisher sidecar."""

    bucket: StrictStr = Field(min_length=1)
    run_prefix: StrictStr = Field(alias="runPrefix", min_length=1)
    run_uid: StrictStr = Field(alias="runUid", min_length=1)
    required_paths: tuple[StrictStr, ...] = Field(alias="requiredPaths")
    publication_final_retry_seconds: StrictInt = Field(alias="publicationFinalRetrySeconds", gt=0)
    termination_grace_seconds: StrictInt = Field(alias="terminationGraceSeconds", gt=0)


class FinalizePayload(RuntimeModel):
    """JSON boundary consumed by the separate onExit finalizer pod."""

    bucket: StrictStr = Field(min_length=1)
    run_prefix: StrictStr = Field(alias="runPrefix", min_length=1)
    run_uid: StrictStr = Field(alias="runUid", min_length=1)
    main_status: MainStatus = Field(alias="mainStatus")
    required_paths: tuple[StrictStr, ...] = Field(alias="requiredPaths")
