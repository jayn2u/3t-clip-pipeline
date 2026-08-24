from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.readiness.fixtures import BundleOptions, ready_payloads, write_bundle
from three_t_clip_pipeline.readiness.models import EvidenceKind, JsonValue, ReadinessReport


def _run_cli(bundle_path: Path) -> tuple[subprocess.CompletedProcess[str], ReadinessReport]:
    completed = subprocess.run(
        [sys.executable, "-m", "three_t_clip_pipeline.readiness", str(bundle_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    return completed, ReadinessReport.model_validate_json(completed.stdout)


def _inventory(*, size: int = 64, digest: str = "a", captured_at: str | None = None) -> JsonValue:
    return {
        "capturedAt": captured_at or datetime.now(tz=UTC).isoformat(),
        "objects": [{"key": "results/a.json", "size": size, "sha256": digest * 64}],
    }


def test_ready_when_every_collector_file_and_domain_proof_matches(tmp_path: Path) -> None:
    # Given: a complete bundle of independently hashed machine collector files.
    bundle_path = write_bundle(tmp_path)

    # When: the public CLI independently reads and evaluates the bundle.
    completed, report = _run_cli(bundle_path)

    # Then: all nine sources are verified and readiness is mutation-free.
    assert completed.returncode == 0
    assert report.ready is True
    assert report.failed_checks == ()
    assert report.mutation_calls == 0
    assert len(report.verified_sources) == len(EvidenceKind)


def test_cli_rejects_forged_snapshot_without_collector_provenance(tmp_path: Path) -> None:
    # Given: the formerly accepted self-consistent operator snapshot.
    snapshot_path = tmp_path / "forged-snapshot.json"
    _ = snapshot_path.write_text(
        json.dumps(
            {
                "schema": "three-t-pipeline/cutover-readiness-v1",
                "resource": {"uid": "fake", "spec": {"local": {}}, "owner": "fake"},
                "ready": True,
                "mutationCalls": 0,
            }
        ),
        encoding="utf-8",
    )

    # When: the public boundary parses the unproven claims.
    completed, report = _run_cli(snapshot_path)

    # Then: semantic boundary rejection is a fail-closed readiness result.
    assert completed.returncode == 1
    assert report.ready is False
    assert report.failed_checks == ("bundle_valid",)
    assert report.mutation_calls == 0


@pytest.mark.parametrize(
    ("options", "failed_check"),
    [
        (
            BundleOptions(omitted=frozenset({EvidenceKind.KUBERNETES_PV})),
            "kubernetes_pv_source_present",
        ),
        (
            BundleOptions(
                overrides={
                    EvidenceKind.KUBERNETES_PV: {
                        "apiVersion": "v1",
                        "kind": "PersistentVolume",
                        "metadata": {"name": "three-t-local-data"},
                        "spec": {"local": {"path": "/srv/three-t-pipeline"}},
                    }
                }
            ),
            "kubernetes_pv_payload_valid",
        ),
        (
            BundleOptions(
                overrides={
                    EvidenceKind.FINDMNT: {
                        "filesystems": [
                            {
                                "source": "/dev/sdb1",
                                "target": "/srv/three-t-pipeline",
                                "fstype": "ext4",
                            }
                        ]
                    }
                }
            ),
            "host_path_intended_device",
        ),
        (
            BundleOptions(
                overrides={
                    EvidenceKind.DIRECTORY_STAT: {
                        "path": "/srv/three-t-pipeline",
                        "fileType": "DirectoryOrCreate",
                        "exists": True,
                    }
                }
            ),
            "directory_stat_payload_valid",
        ),
        (
            BundleOptions(omitted=frozenset({EvidenceKind.BACKUP_INVENTORY})),
            "backup_inventory_source_present",
        ),
        (
            BundleOptions(omitted=frozenset({EvidenceKind.RESTORE_INVENTORY})),
            "restore_inventory_source_present",
        ),
        (
            BundleOptions(
                overrides={
                    EvidenceKind.BACKUP_INVENTORY: _inventory(
                        captured_at="2000-01-01T00:00:00+00:00"
                    )
                }
            ),
            "backup_fresh",
        ),
        (
            BundleOptions(overrides={EvidenceKind.BACKUP_INVENTORY: _inventory(size=63)}),
            "backup_bytes_match",
        ),
        (
            BundleOptions(overrides={EvidenceKind.BACKUP_INVENTORY: _inventory(digest="c")}),
            "backup_sha256_matches",
        ),
        (
            BundleOptions(
                overrides={
                    EvidenceKind.QUIESCENCE: {
                        "resourceUid": "pv-uid-123",
                        "activeWriters": 1,
                        "marker": "freeze-123",
                    }
                }
            ),
            "writers_quiesced",
        ),
        (
            BundleOptions(
                overrides={
                    EvidenceKind.MANIFEST_DIFF: {
                        "resourceUid": "pv-uid-123",
                        "immutableChanges": ["spec.local.path"],
                        "ownershipConflicts": [],
                    }
                }
            ),
            "immutable_fields_unchanged",
        ),
        (
            BundleOptions(
                overrides={
                    EvidenceKind.MANIFEST_DIFF: {
                        "resourceUid": "pv-uid-123",
                        "immutableChanges": [],
                        "ownershipConflicts": ["spec.claimRef"],
                    }
                }
            ),
            "ownership_clear",
        ),
        (
            BundleOptions(exit_codes={EvidenceKind.SERVER_DRY_RUN: 1}),
            "server_dry_run_collector_exit_zero",
        ),
        (
            BundleOptions(
                overrides={
                    EvidenceKind.ROLLBACK: {
                        "resourceUid": "pv-uid-123",
                        "freezeMarker": "freeze-123",
                        "owner": "",
                        "commands": [],
                    }
                }
            ),
            "rollback_payload_valid",
        ),
    ],
    ids=[
        "missing_kubernetes_collector",
        "missing_kubernetes_uid_managed_fields_owner",
        "wrong_mount",
        "non_directory_stat",
        "missing_backup",
        "missing_restore",
        "stale_backup",
        "bytes_mismatch",
        "hash_mismatch",
        "active_writers",
        "immutable_diff",
        "ownership_conflict",
        "server_dry_run_failure",
        "rollback_owner_commands_absent",
    ],
)
def test_not_ready_when_required_collector_or_domain_proof_fails(
    tmp_path: Path, options: BundleOptions, failed_check: str
) -> None:
    # Given: one otherwise-complete bundle with a failed or missing proof.
    bundle_path = write_bundle(tmp_path, options)

    # When: readiness recomputes the decision from captured files.
    completed, report = _run_cli(bundle_path)

    # Then: the exact proof fails and no mutation is possible.
    assert completed.returncode == 1
    assert report.ready is False
    assert failed_check in report.failed_checks
    assert report.mutation_calls == 0


def test_not_ready_when_captured_backup_changes_after_hashing(tmp_path: Path) -> None:
    # Given: a complete bundle whose backup inventory is modified after provenance capture.
    bundle_path = write_bundle(tmp_path)
    backup_path = tmp_path / "backup-inventory.json"
    _ = backup_path.write_bytes(backup_path.read_bytes() + b" ")

    # When: readiness independently recomputes the file hash.
    completed, report = _run_cli(bundle_path)

    # Then: stale provenance is rejected before payload parsing.
    assert completed.returncode == 1
    assert "backup_inventory_content_sha256" in report.failed_checks
    assert "restore_inventory_present" not in report.failed_checks
    assert report.ready is False
    assert report.mutation_calls == 0


def test_not_ready_when_source_descriptor_is_untrusted(tmp_path: Path) -> None:
    # Given: a valid file attributed to an unapproved collector descriptor.
    bundle_path = write_bundle(
        tmp_path,
        BundleOptions(descriptors={EvidenceKind.FINDMNT: "operator says device matches"}),
    )

    # When: readiness checks the source contract.
    completed, report = _run_cli(bundle_path)

    # Then: self-asserted attribution cannot satisfy readiness.
    assert completed.returncode == 1
    assert "findmnt_source_descriptor" in report.failed_checks
    assert report.mutation_calls == 0


def test_invalid_json_exits_two_without_success_output(tmp_path: Path) -> None:
    # Given: syntactically invalid JSON.
    bundle_path = tmp_path / "bundle.json"
    _ = bundle_path.write_text("{", encoding="utf-8")

    # When: the CLI parses the boundary.
    completed = subprocess.run(
        [sys.executable, "-m", "three_t_clip_pipeline.readiness", str(bundle_path)],
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: malformed input is distinguished from a not-ready decision.
    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr.startswith("CUTOVER_READINESS_INVALID")


def test_payloads_reject_credential_fields(tmp_path: Path) -> None:
    # Given: a collector payload carrying a forbidden credential-shaped extra field.
    payloads = ready_payloads()
    kubernetes = payloads[EvidenceKind.KUBERNETES_PV]
    assert isinstance(kubernetes, dict)
    credential_value = f"must-not-enter-evidence-{len(payloads)}"
    kubernetes["token"] = credential_value
    bundle_path = write_bundle(
        tmp_path, BundleOptions(overrides={EvidenceKind.KUBERNETES_PV: kubernetes})
    )

    # When: the typed payload boundary parses the artifact.
    completed, report = _run_cli(bundle_path)

    # Then: unknown credential fields fail closed and are never reflected in output.
    assert completed.returncode == 1
    assert "kubernetes_pv_payload_valid" in report.failed_checks
    assert credential_value not in completed.stdout
    assert report.mutation_calls == 0
