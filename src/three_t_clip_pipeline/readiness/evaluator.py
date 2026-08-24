"""Derive cutover readiness only from verified collector payloads."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING

from three_t_clip_pipeline.readiness.collectors import (
    RejectedCollector,
    VerifiedCollector,
    load_collector,
)
from three_t_clip_pipeline.readiness.models import (
    SCHEMA,
    DirectoryStatOutput,
    EvidenceKind,
    FindmntOutput,
    InventoryOutput,
    KubernetesPvOutput,
    ManifestDiffOutput,
    QuiescenceOutput,
    ReadinessBundle,
    ReadinessCheck,
    ReadinessReport,
    RollbackOutput,
    ServerDryRunOutput,
)

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class CollectedPayloads:
    """Typed optional payloads after source verification."""

    kubernetes: KubernetesPvOutput | None
    findmnt: FindmntOutput | None
    directory: DirectoryStatOutput | None
    backup: InventoryOutput | None
    restore: InventoryOutput | None
    quiescence: QuiescenceOutput | None
    manifest_diff: ManifestDiffOutput | None
    dry_run: ServerDryRunOutput | None
    rollback: RollbackOutput | None


def _check(code: str, passed: bool) -> ReadinessCheck:
    return ReadinessCheck(code=code, passed=passed)


def _inventory_rows(inventory: InventoryOutput) -> tuple[tuple[str, int, str], ...]:
    return tuple(sorted((item.key, item.size, item.sha256) for item in inventory.objects))


def _inventory_digest(inventory: InventoryOutput) -> str:
    encoded = json.dumps(_inventory_rows(inventory), separators=(",", ":")).encode()
    return sha256(encoded).hexdigest()


def _inventory_pair(
    verified: tuple[VerifiedCollector, ...],
) -> tuple[InventoryOutput | None, InventoryOutput | None]:
    backup: InventoryOutput | None = None
    restore: InventoryOutput | None = None
    for item in verified:
        if not isinstance(item.payload, InventoryOutput):
            continue
        if item.source.source_kind is EvidenceKind.BACKUP_INVENTORY:
            backup = item.payload
        if item.source.source_kind is EvidenceKind.RESTORE_INVENTORY:
            restore = item.payload
    return backup, restore


def _collect_payloads(verified: tuple[VerifiedCollector, ...]) -> CollectedPayloads:
    kubernetes: KubernetesPvOutput | None = None
    findmnt: FindmntOutput | None = None
    directory: DirectoryStatOutput | None = None
    quiescence: QuiescenceOutput | None = None
    manifest_diff: ManifestDiffOutput | None = None
    dry_run: ServerDryRunOutput | None = None
    rollback: RollbackOutput | None = None
    for item in verified:
        payload = item.payload
        match payload:  # noqa: MATCH_OK
            case KubernetesPvOutput():
                kubernetes = payload
            case FindmntOutput():
                findmnt = payload
            case DirectoryStatOutput():
                directory = payload
            case InventoryOutput():
                continue
            case QuiescenceOutput():
                quiescence = payload
            case ManifestDiffOutput():
                manifest_diff = payload
            case ServerDryRunOutput():
                dry_run = payload
            case RollbackOutput():
                rollback = payload
    backup, restore = _inventory_pair(verified)
    return CollectedPayloads(
        kubernetes=kubernetes,
        findmnt=findmnt,
        directory=directory,
        backup=backup,
        restore=restore,
        quiescence=quiescence,
        manifest_diff=manifest_diff,
        dry_run=dry_run,
        rollback=rollback,
    )


def _domain_checks(
    verified: tuple[VerifiedCollector, ...], evaluated_at: datetime
) -> tuple[ReadinessCheck, ...]:
    collected = _collect_payloads(verified)
    annotations = (
        collected.kubernetes.metadata.annotations if collected.kubernetes is not None else None
    )
    owner = annotations.owner if annotations is not None else ""
    intended_device = annotations.intended_device if annotations is not None else ""
    max_age = annotations.max_backup_age_seconds if annotations is not None else -1
    uid = collected.kubernetes.metadata.uid if collected.kubernetes is not None else ""
    path = collected.kubernetes.spec.local.path if collected.kubernetes is not None else ""
    mount = collected.findmnt.filesystems[0] if collected.findmnt is not None else None
    backup_rows = _inventory_rows(collected.backup) if collected.backup is not None else ()
    restore_rows = _inventory_rows(collected.restore) if collected.restore is not None else ()
    backup_fresh = (
        collected.backup is not None
        and collected.backup.captured_at.utcoffset() is not None
        and 0 <= (evaluated_at - collected.backup.captured_at).total_seconds() <= max_age
    )
    return (
        _check("resource_uid_present", bool(uid)),
        _check("resource_spec_present", bool(path)),
        _check("managed_fields_present", collected.kubernetes is not None),
        _check("current_owner_present", bool(owner)),
        _check(
            "host_path_existing_directory",
            collected.directory is not None
            and collected.directory.exists
            and collected.directory.path == path,
        ),
        _check("host_path_matches_spec", mount is not None and mount.target == path),
        _check(
            "host_path_intended_device",
            mount is not None and bool(intended_device) and mount.source == intended_device,
        ),
        _check("backup_inventory_present", collected.backup is not None),
        _check("restore_inventory_present", collected.restore is not None),
        _check("backup_count_matches", bool(backup_rows) and len(backup_rows) == len(restore_rows)),
        _check(
            "backup_bytes_match",
            bool(backup_rows)
            and sum(row[1] for row in backup_rows) == sum(row[1] for row in restore_rows),
        ),
        _check(
            "backup_sha256_matches",
            collected.backup is not None
            and collected.restore is not None
            and _inventory_digest(collected.backup) == _inventory_digest(collected.restore),
        ),
        _check("backup_fresh", backup_fresh),
        _check(
            "writers_quiesced",
            collected.quiescence is not None
            and collected.quiescence.resource_uid == uid
            and collected.quiescence.active_writers == 0,
        ),
        _check(
            "immutable_fields_unchanged",
            collected.manifest_diff is not None
            and collected.manifest_diff.resource_uid == uid
            and not collected.manifest_diff.immutable_changes,
        ),
        _check(
            "ownership_clear",
            collected.manifest_diff is not None
            and collected.manifest_diff.resource_uid == uid
            and not collected.manifest_diff.ownership_conflicts,
        ),
        _check(
            "server_dry_run_succeeded",
            collected.dry_run is not None
            and collected.kubernetes is not None
            and collected.dry_run.metadata.name == collected.kubernetes.metadata.name
            and collected.dry_run.spec == collected.kubernetes.spec,
        ),
        _check(
            "rollback_owner_present",
            collected.rollback is not None
            and collected.rollback.resource_uid == uid
            and bool(collected.rollback.owner),
        ),
        _check(
            "rollback_commands_present",
            collected.rollback is not None
            and collected.quiescence is not None
            and collected.rollback.freeze_marker == collected.quiescence.marker
            and bool(collected.rollback.commands),
        ),
    )


def evaluate_readiness(
    bundle: ReadinessBundle, bundle_path: Path, evaluated_at: datetime | None = None
) -> ReadinessReport:
    """Recompute trust and readiness without executing collector descriptors."""
    outcomes = tuple(load_collector(bundle, kind, bundle_path.parent) for kind in EvidenceKind)
    source_checks: list[ReadinessCheck] = []
    verified: list[VerifiedCollector] = []
    for outcome in outcomes:
        match outcome:  # noqa: MATCH_OK
            case VerifiedCollector():
                verified.append(outcome)
            case RejectedCollector():
                source_checks.extend(outcome.checks)
    domain = _domain_checks(tuple(verified), evaluated_at or datetime.now(tz=UTC))
    checks = tuple(source_checks) + domain
    failed = tuple(check.code for check in checks if not check.passed)
    return ReadinessReport(
        schema=SCHEMA,
        ready=not failed and len(verified) == len(EvidenceKind),
        checks=checks,
        failedChecks=failed,
        verifiedSources=tuple(item.source for item in verified),
    )


def rejected_bundle_report(code: str) -> ReadinessReport:
    """Return a machine-readable fail-closed report for invalid boundary data."""
    check = _check(code, passed=False)
    return ReadinessReport(
        schema=SCHEMA,
        ready=False,
        checks=(check,),
        failedChecks=(code,),
        verifiedSources=(),
    )
