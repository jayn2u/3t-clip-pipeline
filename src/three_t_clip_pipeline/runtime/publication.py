"""Strict commit-marker publication from verified immutable run state."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from enum import StrEnum, unique
from typing import Annotated, ClassVar, Literal, Protocol, override

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError

from three_t_clip_pipeline.runtime.status import PublicationState, safe_relative_path

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_IMAGE_DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
_RFC3339_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
COMMITTED_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
COMMITTED_SCHEMA_ID = "https://schemas.three-t.dev/committed-v1.schema.json"


class _BoundaryModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True, populate_by_name=True
    )


class ImmutableObject(_BoundaryModel):
    """HEAD-verifiable identity for one immutable run object."""

    key: StrictStr = Field(min_length=1)
    size: Annotated[StrictInt, Field(ge=0)]
    sha256: StrictStr = Field(pattern=_SHA256_PATTERN)


class CommitMarker(_BoundaryModel):
    """Canonical committed-v1 reader boundary."""

    schema_: Literal["committed/v1"] = Field(alias="schema")
    run_uid: StrictStr = Field(alias="runUid", min_length=1)
    workflow_uid: StrictStr = Field(alias="workflowUid", min_length=1)
    workload_image_digest: StrictStr = Field(
        alias="workloadImageDigest", pattern=_IMAGE_DIGEST_PATTERN
    )
    platform_image_digest: StrictStr = Field(
        alias="platformImageDigest", pattern=_IMAGE_DIGEST_PATTERN
    )
    bundle_digest: StrictStr = Field(alias="bundleDigest", pattern=_SHA256_PATTERN)
    objects: tuple[ImmutableObject, ...]
    provenance_key: StrictStr = Field(alias="provenanceKey", min_length=1)
    workload_status: Literal["Succeeded"] = Field(alias="workloadStatus")
    main_finished_at: StrictStr = Field(alias="mainFinishedAt", min_length=1)


class _WorkflowMetadata(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True, strict=True)
    name: StrictStr
    uid: StrictStr


class _WorkflowNode(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="ignore", frozen=True, strict=True, populate_by_name=True
    )
    type_: StrictStr = Field(alias="type")
    template_name: StrictStr | None = Field(default=None, alias="templateName")
    display_name: StrictStr | None = Field(default=None, alias="displayName")
    phase: StrictStr
    finished_at: StrictStr | None = Field(default=None, alias="finishedAt")


class _WorkflowStatus(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True, strict=True)
    nodes: dict[StrictStr, _WorkflowNode]


class _Workflow(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True, strict=True)
    metadata: _WorkflowMetadata
    status: _WorkflowStatus


class PublicationStore(Protocol):
    """Read and conditional-create capabilities required by a finalizer."""

    def get(self, key: str) -> bytes:
        """Read immutable bytes by key."""
        ...

    def head(self, key: str) -> ImmutableObject:
        """Read an immutable object's size and digest."""
        ...

    def create(self, key: str, payload: bytes) -> bool:
        """Conditionally create bytes, returning false on HTTP 412."""
        ...


class FinalizePublicationRequest(_BoundaryModel):
    """Immutable inputs passed to a separate onExit finalizer."""

    run_prefix: StrictStr
    run_uid: StrictStr
    workflow_name: StrictStr
    workflow_uid: StrictStr
    main_node_identity: StrictStr
    workload_image_digest: StrictStr = Field(pattern=_IMAGE_DIGEST_PATTERN)
    platform_image_digest: StrictStr = Field(pattern=_IMAGE_DIGEST_PATTERN)
    bundle_digest: StrictStr = Field(pattern=_SHA256_PATTERN)
    objects: tuple[ImmutableObject, ...]
    provenance: ImmutableObject
    finalizer_started_at: datetime
    publication_state: PublicationState = PublicationState.COMPLETED

    @override
    def model_post_init(self, _context: object) -> None:
        """Reject unsafe keys and ambiguous wall-clock values."""
        _ = safe_relative_path(self.run_prefix)
        _ = safe_relative_path(self.run_uid)
        _ = safe_relative_path(self.provenance.key)
        if self.finalizer_started_at.tzinfo is None:
            message = "finalizer_started_at must be timezone-aware"
            raise ValueError(message)


@unique
class CommitOutcome(StrEnum):
    """Stable finalization outcomes."""

    COMMITTED = "committed"
    ALREADY_COMMITTED = "already_committed"
    COMMIT_COLLISION = "commit_collision"
    REFUSED = "refused"


def commit_marker_bytes(marker: CommitMarker) -> bytes:
    """Serialize a marker with sorted keys, compact separators, and one newline."""
    payload = marker.model_dump(mode="json", by_alias=True)
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()


def committed_schema_bytes() -> bytes:
    """Return deterministic checked-in committed-v1 JSON Schema bytes."""
    schema = CommitMarker.model_json_schema(by_alias=True, mode="validation")
    schema["$schema"] = COMMITTED_SCHEMA_DIALECT
    schema["$id"] = COMMITTED_SCHEMA_ID
    schema["title"] = "committed/v1"
    return (json.dumps(schema, sort_keys=True, indent=2) + "\n").encode()


def parse_commit_marker(payload: bytes) -> CommitMarker:
    """Accept only strict canonical committed-v1 bytes."""
    marker = CommitMarker.model_validate_json(payload)
    if payload != commit_marker_bytes(marker):
        message = "commit marker is not canonical"
        raise ValueError(message)
    return marker


def _parse_finished_at(value: str) -> datetime | None:
    if _RFC3339_PATTERN.fullmatch(value) is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def _eligible_finished_at(
    workflow_payload: bytes, request: FinalizePublicationRequest
) -> str | None:
    try:
        workflow = _Workflow.model_validate_json(workflow_payload)
    except ValidationError:
        return None
    if (
        workflow.metadata.name != request.workflow_name
        or workflow.metadata.uid != request.workflow_uid
    ):
        return None
    candidates = tuple(
        node
        for node in workflow.status.nodes.values()
        if node.type_ == "Pod"
        and node.template_name == request.main_node_identity
        and node.display_name == request.main_node_identity
    )
    if len(candidates) != 1:
        return None
    node = candidates[0]
    if node.phase != "Succeeded" or node.finished_at is None:
        return None
    parsed = _parse_finished_at(node.finished_at)
    if parsed is None or parsed > request.finalizer_started_at:
        return None
    return node.finished_at


def _objects_verified(request: FinalizePublicationRequest, store: PublicationStore) -> bool:
    if len({item.key for item in request.objects}) != len(request.objects):
        return False
    try:
        if any(store.head(item.key) != item for item in request.objects):
            return False
        if store.head(request.provenance.key) != request.provenance:
            return False
    except (KeyError, ValueError):
        return False
    return True


def _existing_marker_outcome(
    request: FinalizePublicationRequest,
    store: PublicationStore,
    marker_key: str,
    payload: bytes,
) -> CommitOutcome:
    try:
        existing_payload = store.get(marker_key)
        existing = parse_commit_marker(existing_payload)
    except (KeyError, ValidationError, ValueError):
        return CommitOutcome.COMMIT_COLLISION
    same_payload = hashlib.sha256(existing_payload).digest() == hashlib.sha256(payload).digest()
    if existing.run_uid == request.run_uid and same_payload:
        return CommitOutcome.ALREADY_COMMITTED
    return CommitOutcome.COMMIT_COLLISION


def finalize_publication(
    request: FinalizePublicationRequest, store: PublicationStore, workflow_key: str
) -> CommitOutcome:
    """Verify Workflow/object state before the sole conditional marker creation."""
    if request.publication_state != PublicationState.COMPLETED:
        return CommitOutcome.REFUSED
    try:
        workflow_payload = store.get(workflow_key)
    except KeyError:
        return CommitOutcome.REFUSED
    main_finished_at = _eligible_finished_at(workflow_payload, request)
    if main_finished_at is None or not _objects_verified(request, store):
        return CommitOutcome.REFUSED
    marker = CommitMarker(
        schema="committed/v1",
        runUid=request.run_uid,
        workflowUid=request.workflow_uid,
        workloadImageDigest=request.workload_image_digest,
        platformImageDigest=request.platform_image_digest,
        bundleDigest=request.bundle_digest,
        objects=tuple(sorted(request.objects, key=lambda item: item.key)),
        provenanceKey=request.provenance.key,
        workloadStatus="Succeeded",
        mainFinishedAt=main_finished_at,
    )
    payload = commit_marker_bytes(marker)
    marker_key = f"{request.run_prefix}/COMMITTED.json"
    if store.create(marker_key, payload):
        return CommitOutcome.COMMITTED
    return _existing_marker_outcome(request, store, marker_key, payload)
