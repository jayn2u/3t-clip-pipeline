"""Frozen trust-boundary models for cutover collector evidence."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - Pydantic resolves this model field at runtime.
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, StringConstraints, field_validator

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
NonEmpty = Annotated[str, StringConstraints(min_length=1)]
SCHEMA: Final = "three-t-pipeline/cutover-readiness-v2"


class JsonDocument(RootModel[JsonValue]):
    """Typed syntax-only JSON document used before semantic bundle parsing."""


class FrozenModel(BaseModel):
    """Reject unknown fields and prevent evidence mutation after parsing."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, populate_by_name=True
    )


class EvidenceKind(StrEnum):
    """Allowed read-only collector output classes."""

    KUBERNETES_PV = "kubernetes-pv"
    FINDMNT = "findmnt"
    DIRECTORY_STAT = "directory-stat"
    BACKUP_INVENTORY = "backup-inventory"
    RESTORE_INVENTORY = "restore-inventory"
    QUIESCENCE = "quiescence"
    MANIFEST_DIFF = "manifest-diff"
    SERVER_DRY_RUN = "server-dry-run"
    ROLLBACK = "rollback"


EXPECTED_DESCRIPTORS: Final[dict[EvidenceKind, str]] = {
    EvidenceKind.KUBERNETES_PV: (
        "kubectl --context lab-a get persistentvolume three-t-local-data -o json | "
        "jq strict readiness projection"
    ),
    EvidenceKind.FINDMNT: (
        "findmnt --target /srv/three-t-pipeline --json --output SOURCE,TARGET,FSTYPE"
    ),
    EvidenceKind.DIRECTORY_STAT: "stat --format=%F /srv/three-t-pipeline",
    EvidenceKind.BACKUP_INVENTORY: "mc find legacy/results --json normalized inventory",
    EvidenceKind.RESTORE_INVENTORY: "mc find rehearsal/results --json normalized inventory",
    EvidenceKind.QUIESCENCE: "kubernetes/minio active-writer collector JSON",
    EvidenceKind.MANIFEST_DIFF: "immutable/ownership comparator JSON",
    EvidenceKind.SERVER_DRY_RUN: (
        "kubectl --context lab-a apply --server-side --dry-run=server "
        "-f proposed-pv-minio.yaml -o json | jq strict readiness projection"
    ),
    EvidenceKind.ROLLBACK: "operator-owned rollback artifact JSON",
}


class EvidenceRecord(FrozenModel):
    """Provenance for one already-captured collector output file."""

    source_kind: EvidenceKind = Field(alias="sourceKind")
    source_descriptor: NonEmpty = Field(alias="sourceDescriptor")
    exit_code: int = Field(alias="exitCode")
    content_sha256: Sha256 = Field(alias="contentSha256")
    captured_file: NonEmpty = Field(alias="capturedFile")

    @field_validator("captured_file")
    @classmethod
    def captured_file_is_relative(cls, value: str) -> str:
        """Confine evidence reads to the directory containing the bundle."""
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts:
            message = "capturedFile must be a confined relative path"
            raise ValueError(message)
        return value


class ReadinessBundle(FrozenModel):
    """Complete set of collector provenance records for one decision."""

    schema_: Literal["three-t-pipeline/cutover-readiness-v2"] = Field(alias="schema")
    sources: tuple[EvidenceRecord, ...] = ()


class ManagedField(FrozenModel):
    """One Kubernetes field-manager observation."""

    manager: NonEmpty
    operation: Literal["Apply", "Update"]


class PvAnnotations(FrozenModel):
    """Allowlisted non-secret PV expectations used by readiness."""

    owner: NonEmpty = Field(alias="three-t.dev/owner")
    intended_device: NonEmpty = Field(alias="three-t.dev/intended-device")
    max_backup_age_seconds: int = Field(alias="three-t.dev/max-backup-age-seconds", gt=0)


class KubernetesMetadata(FrozenModel):
    """Kubernetes metadata required for identity and ownership checks."""

    name: NonEmpty
    uid: NonEmpty
    annotations: PvAnnotations
    managed_fields: tuple[ManagedField, ...] = Field(alias="managedFields", min_length=1)


class LocalVolume(FrozenModel):
    """Existing local-volume path selected by the PV."""

    path: NonEmpty


class PersistentVolumeSpec(FrozenModel):
    """PV spec fields used by readiness."""

    local: LocalVolume


class KubernetesPvOutput(FrozenModel):
    """Machine JSON emitted by a read-only Kubernetes PV GET."""

    api_version: Literal["v1"] = Field(alias="apiVersion")
    kind: Literal["PersistentVolume"]
    metadata: KubernetesMetadata
    spec: PersistentVolumeSpec


class FindmntFilesystem(FrozenModel):
    """One findmnt filesystem row."""

    source: NonEmpty
    target: NonEmpty
    fstype: NonEmpty


class FindmntOutput(FrozenModel):
    """Machine JSON emitted by findmnt."""

    filesystems: tuple[FindmntFilesystem, ...] = Field(min_length=1)


class DirectoryStatOutput(FrozenModel):
    """Machine record proving that a path already exists as a directory."""

    path: NonEmpty
    file_type: Literal["Directory"] = Field(alias="fileType")
    exists: Literal[True]


class InventoryObject(FrozenModel):
    """One normalized object inventory entry."""

    key: NonEmpty
    size: int = Field(ge=0)
    sha256: Sha256


class InventoryOutput(FrozenModel):
    """Timestamped normalized object inventory."""

    captured_at: datetime = Field(alias="capturedAt")
    objects: tuple[InventoryObject, ...]


class QuiescenceOutput(FrozenModel):
    """Machine record binding writer count and freeze marker to the PV UID."""

    resource_uid: NonEmpty = Field(alias="resourceUid")
    active_writers: int = Field(alias="activeWriters", ge=0)
    marker: NonEmpty


class ManifestDiffOutput(FrozenModel):
    """Machine comparator output for immutable fields and ownership."""

    resource_uid: NonEmpty = Field(alias="resourceUid")
    immutable_changes: tuple[str, ...] = Field(alias="immutableChanges")
    ownership_conflicts: tuple[str, ...] = Field(alias="ownershipConflicts")


class DryRunMetadata(FrozenModel):
    """Identity emitted by Kubernetes server-side dry-run."""

    name: NonEmpty


class ServerDryRunOutput(FrozenModel):
    """Kubernetes object returned only after successful server-side dry-run."""

    api_version: Literal["v1"] = Field(alias="apiVersion")
    kind: Literal["PersistentVolume"]
    metadata: DryRunMetadata
    spec: PersistentVolumeSpec


class RollbackOutput(FrozenModel):
    """Separately captured rollback ownership and command artifact."""

    resource_uid: NonEmpty = Field(alias="resourceUid")
    freeze_marker: NonEmpty = Field(alias="freezeMarker")
    owner: NonEmpty
    commands: tuple[NonEmpty, ...] = Field(min_length=1)


class ReadinessCheck(FrozenModel):
    """Machine-readable outcome for one mandatory readiness proof."""

    code: str
    passed: bool


class VerifiedSource(FrozenModel):
    """Collector source whose file and provenance passed verification."""

    source_kind: EvidenceKind = Field(alias="sourceKind")
    source_descriptor: str = Field(alias="sourceDescriptor")
    content_sha256: Sha256 = Field(alias="contentSha256")


class ReadinessReport(FrozenModel):
    """Fail-closed decision with no infrastructure mutation capability."""

    schema_: Literal["three-t-pipeline/cutover-readiness-v2"] = Field(alias="schema")
    ready: bool
    checks: tuple[ReadinessCheck, ...]
    failed_checks: tuple[str, ...] = Field(alias="failedChecks")
    verified_sources: tuple[VerifiedSource, ...] = Field(alias="verifiedSources")
    mutation_calls: Literal[0] = Field(default=0, alias="mutationCalls")
