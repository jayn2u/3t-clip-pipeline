from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING

from three_t_clip_pipeline.readiness.models import EXPECTED_DESCRIPTORS, EvidenceKind, JsonValue

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True, slots=True)
class BundleOptions:
    overrides: Mapping[EvidenceKind, JsonValue] = field(
        default_factory=dict[EvidenceKind, JsonValue]
    )
    omitted: frozenset[EvidenceKind] = frozenset()
    exit_codes: Mapping[EvidenceKind, int] = field(default_factory=dict[EvidenceKind, int])
    descriptors: Mapping[EvidenceKind, str] = field(default_factory=dict[EvidenceKind, str])


def ready_payloads(captured_at: datetime | None = None) -> dict[EvidenceKind, JsonValue]:
    timestamp = (captured_at or datetime.now(tz=UTC)).isoformat()
    inventory: JsonValue = {
        "capturedAt": timestamp,
        "objects": [
            {"key": "results/a.json", "size": 64, "sha256": "a" * 64},
            {"key": "results/b.json", "size": 64, "sha256": "b" * 64},
        ],
    }
    return {
        EvidenceKind.KUBERNETES_PV: {
            "apiVersion": "v1",
            "kind": "PersistentVolume",
            "metadata": {
                "name": "three-t-local-data",
                "uid": "pv-uid-123",
                "annotations": {
                    "three-t.dev/owner": "legacy-storage",
                    "three-t.dev/intended-device": "/dev/nvme0n1p1",
                    "three-t.dev/max-backup-age-seconds": 300,
                },
                "managedFields": [{"manager": "legacy-storage", "operation": "Update"}],
            },
            "spec": {"local": {"path": "/srv/three-t-pipeline"}},
        },
        EvidenceKind.FINDMNT: {
            "filesystems": [
                {
                    "source": "/dev/nvme0n1p1",
                    "target": "/srv/three-t-pipeline",
                    "fstype": "ext4",
                }
            ]
        },
        EvidenceKind.DIRECTORY_STAT: {
            "path": "/srv/three-t-pipeline",
            "fileType": "Directory",
            "exists": True,
        },
        EvidenceKind.BACKUP_INVENTORY: inventory,
        EvidenceKind.RESTORE_INVENTORY: inventory,
        EvidenceKind.QUIESCENCE: {
            "resourceUid": "pv-uid-123",
            "activeWriters": 0,
            "marker": "freeze-123",
        },
        EvidenceKind.MANIFEST_DIFF: {
            "resourceUid": "pv-uid-123",
            "immutableChanges": [],
            "ownershipConflicts": [],
        },
        EvidenceKind.SERVER_DRY_RUN: {
            "apiVersion": "v1",
            "kind": "PersistentVolume",
            "metadata": {"name": "three-t-local-data"},
            "spec": {"local": {"path": "/srv/three-t-pipeline"}},
        },
        EvidenceKind.ROLLBACK: {
            "resourceUid": "pv-uid-123",
            "freezeMarker": "freeze-123",
            "owner": "storage-oncall",
            "commands": ["kubectl get pv three-t-local-data -o json"],
        },
    }


def write_bundle(directory: Path, options: BundleOptions | None = None) -> Path:
    selected = options or BundleOptions()
    payloads = ready_payloads()
    payloads.update(selected.overrides)
    sources: list[JsonValue] = []
    for kind in EvidenceKind:
        if kind in selected.omitted:
            continue
        content = json.dumps(payloads[kind], sort_keys=True, separators=(",", ":")).encode()
        captured_file = f"{kind.value}.json"
        _ = (directory / captured_file).write_bytes(content)
        sources.append(
            {
                "sourceKind": kind.value,
                "sourceDescriptor": selected.descriptors.get(kind, EXPECTED_DESCRIPTORS[kind]),
                "exitCode": selected.exit_codes.get(kind, 0),
                "contentSha256": sha256(content).hexdigest(),
                "capturedFile": captured_file,
            }
        )
    bundle_path = directory / "bundle.json"
    _ = bundle_path.write_text(
        json.dumps(
            {"schema": "three-t-pipeline/cutover-readiness-v2", "sources": sources},
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return bundle_path
