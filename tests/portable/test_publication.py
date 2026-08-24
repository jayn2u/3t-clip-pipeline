"""Generic immutable publication invariants."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from three_t_clip_pipeline.runtime.publication import (
    CommitOutcome,
    FinalizePublicationRequest,
    ImmutableObject,
    finalize_publication,
    parse_commit_marker,
)


@dataclass
class Store:
    """In-memory immutable publication store."""

    values: dict[str, tuple[bytes, ImmutableObject]]
    marker: bytes | None = None
    creates: int = 0
    heads: list[str] = field(default_factory=list)

    def get(self, key: str) -> bytes:
        if key.endswith("COMMITTED.json") and self.marker is not None:
            return self.marker
        return self.values[key][0]

    def head(self, key: str) -> ImmutableObject:
        self.heads.append(key)
        return self.values[key][1]

    def create(self, key: str, payload: bytes) -> bool:
        del key
        self.creates += 1
        if self.marker is not None:
            return False
        self.marker = payload
        return True


def _boundary(prefix: str, required_path: str) -> tuple[FinalizePublicationRequest, Store]:
    body = b"result"
    item = ImmutableObject(
        key=f"{prefix}/{required_path}", size=len(body), sha256=hashlib.sha256(body).hexdigest()
    )
    provenance_body = b"{}"
    provenance = ImmutableObject(
        key=f"{prefix}/provenance.json",
        size=len(provenance_body),
        sha256=hashlib.sha256(provenance_body).hexdigest(),
    )
    workflow = json.dumps(
        {
            "metadata": {"name": "generic-workflow", "uid": "workflow-uid"},
            "status": {
                "nodes": {
                    "node": {
                        "type": "Pod",
                        "templateName": "run",
                        "displayName": "run",
                        "phase": "Succeeded",
                        "finishedAt": "2026-08-24T00:00:00Z",
                    }
                }
            },
        }
    ).encode()
    request = FinalizePublicationRequest(
        run_prefix=prefix,
        run_uid="run-uid",
        workflow_name="generic-workflow",
        workflow_uid="workflow-uid",
        main_node_identity="run",
        workload_image_digest="sha256:" + "1" * 64,
        platform_image_digest="sha256:" + "2" * 64,
        bundle_digest="3" * 64,
        objects=(item,),
        provenance=provenance,
        finalizer_started_at=datetime(2026, 8, 24, 0, 1, tzinfo=UTC),
    )
    store = Store(
        {
            "workflow": (workflow, ImmutableObject(key="workflow", size=0, sha256="0" * 64)),
            item.key: (body, item),
            provenance.key: (provenance_body, provenance),
        }
    )
    return request, store


@pytest.mark.parametrize(
    ("prefix", "required_path"),
    [("runs/job-a", "result.json"), ("jobs/job-b", "reports/summary.json")],
)
def test_verified_objects_publish_one_canonical_marker(prefix: str, required_path: str) -> None:
    # Given
    request, store = _boundary(prefix, required_path)

    # When
    outcome = finalize_publication(request, store, "workflow")

    # Then
    assert outcome == CommitOutcome.COMMITTED
    assert store.creates == 1
    assert store.marker is not None
    marker = parse_commit_marker(store.marker)
    assert marker.objects[0].key == f"{prefix}/{required_path}"
    assert marker.main_finished_at == "2026-08-24T00:00:00Z"


def test_head_mismatch_blocks_marker() -> None:
    # Given
    request, store = _boundary("runs/job-c", "result.bin")
    key = request.objects[0].key
    store.values[key] = (b"changed", ImmutableObject(key=key, size=7, sha256="9" * 64))

    # When
    outcome = finalize_publication(request, store, "workflow")

    # Then
    assert outcome == CommitOutcome.REFUSED
    assert store.creates == 0
