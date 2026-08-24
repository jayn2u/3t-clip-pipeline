"""Verify collector provenance and parse trusted machine outputs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING

from pydantic import ValidationError

from three_t_clip_pipeline.readiness.models import (
    EXPECTED_DESCRIPTORS,
    DirectoryStatOutput,
    EvidenceKind,
    FindmntOutput,
    InventoryOutput,
    KubernetesPvOutput,
    ManifestDiffOutput,
    QuiescenceOutput,
    ReadinessBundle,
    ReadinessCheck,
    RollbackOutput,
    ServerDryRunOutput,
    VerifiedSource,
)

if TYPE_CHECKING:
    from pathlib import Path

type CollectorPayload = (
    KubernetesPvOutput
    | FindmntOutput
    | DirectoryStatOutput
    | InventoryOutput
    | QuiescenceOutput
    | ManifestDiffOutput
    | ServerDryRunOutput
    | RollbackOutput
)


@dataclass(frozen=True, slots=True)
class VerifiedCollector:
    """Payload paired with verified provenance."""

    source: VerifiedSource
    payload: CollectorPayload


@dataclass(frozen=True, slots=True)
class RejectedCollector:
    """Checks explaining why a collector could not be trusted."""

    checks: tuple[ReadinessCheck, ...]


def _check(code: str, passed: bool) -> ReadinessCheck:
    return ReadinessCheck(code=code, passed=passed)


type PayloadParser = Callable[[bytes], CollectorPayload]
PAYLOAD_PARSERS: dict[EvidenceKind, PayloadParser] = {
    EvidenceKind.KUBERNETES_PV: KubernetesPvOutput.model_validate_json,
    EvidenceKind.FINDMNT: FindmntOutput.model_validate_json,
    EvidenceKind.DIRECTORY_STAT: DirectoryStatOutput.model_validate_json,
    EvidenceKind.BACKUP_INVENTORY: InventoryOutput.model_validate_json,
    EvidenceKind.RESTORE_INVENTORY: InventoryOutput.model_validate_json,
    EvidenceKind.QUIESCENCE: QuiescenceOutput.model_validate_json,
    EvidenceKind.MANIFEST_DIFF: ManifestDiffOutput.model_validate_json,
    EvidenceKind.SERVER_DRY_RUN: ServerDryRunOutput.model_validate_json,
    EvidenceKind.ROLLBACK: RollbackOutput.model_validate_json,
}


def load_collector(
    bundle: ReadinessBundle, kind: EvidenceKind, bundle_directory: Path
) -> VerifiedCollector | RejectedCollector:
    """Verify one required source before parsing its captured payload."""
    slug = kind.value.replace("-", "_")
    matches = tuple(source for source in bundle.sources if source.source_kind is kind)
    present = len(matches) > 0
    unique = len(matches) == 1
    initial = (_check(f"{slug}_source_present", present), _check(f"{slug}_source_unique", unique))
    if not unique:
        return RejectedCollector(checks=initial)
    record = matches[0]
    descriptor_ok = record.source_descriptor == EXPECTED_DESCRIPTORS[kind]
    exit_ok = record.exit_code == 0
    provenance = (
        *initial,
        _check(f"{slug}_source_descriptor", descriptor_ok),
        _check(f"{slug}_collector_exit_zero", exit_ok),
    )
    if not descriptor_ok or not exit_ok:
        return RejectedCollector(checks=provenance)
    try:
        root = bundle_directory.resolve(strict=True)
        captured = (root / record.captured_file).resolve(strict=True)
        confined = captured.is_relative_to(root)
        content = captured.read_bytes() if confined else b""
    except OSError:
        return RejectedCollector(
            checks=(*provenance, _check(f"{slug}_captured_file_readable", passed=False))
        )
    readable = (*provenance, _check(f"{slug}_captured_file_readable", confined))
    digest_ok = confined and sha256(content).hexdigest() == record.content_sha256
    hashed = (*readable, _check(f"{slug}_content_sha256", digest_ok))
    if not digest_ok:
        return RejectedCollector(checks=hashed)
    try:
        payload = PAYLOAD_PARSERS[kind](content)
    except ValidationError:
        return RejectedCollector(checks=(*hashed, _check(f"{slug}_payload_valid", passed=False)))
    return VerifiedCollector(
        source=VerifiedSource(
            sourceKind=kind,
            sourceDescriptor=record.source_descriptor,
            contentSha256=record.content_sha256,
        ),
        payload=payload,
    )
