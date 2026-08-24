from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from three_t_clip_pipeline.runtime.publication import (
    CommitOutcome,
    FinalizePublicationRequest,
    ImmutableObject,
    commit_marker_bytes,
    committed_schema_bytes,
    finalize_publication,
    parse_commit_marker,
)
from three_t_clip_pipeline.runtime.status import PublicationState


def _workflow(
    finished_at: str = "2026-08-24T01:02:03.456789Z", *, duplicate: bool = False
) -> bytes:
    node = {
        "type": "Pod",
        "templateName": "run",
        "displayName": "run",
        "phase": "Succeeded",
        "finishedAt": finished_at,
    }
    nodes = {"node-a": node, "node-b": node} if duplicate else {"node-a": node}
    return json.dumps(
        {
            "metadata": {"name": "wf", "uid": "workflow-1"},
            "status": {
                "finishedAt": "1999-01-01T00:00:00Z",
                "nodes": nodes,
            },
        }
    ).encode()


@dataclass
class Store:
    objects: dict[str, tuple[bytes, ImmutableObject]]
    marker: bytes | None = None
    marker_attempts: int = 0
    calls: list[str] = field(default_factory=list)

    def get(self, key: str) -> bytes:
        self.calls.append(f"get:{key}")
        if key == "workflow":
            return self.objects[key][0]
        if key == "runs/a/COMMITTED.json" and self.marker is not None:
            return self.marker
        return self.objects[key][0]

    def head(self, key: str) -> ImmutableObject:
        self.calls.append(f"head:{key}")
        return self.objects[key][1]

    def create(self, key: str, payload: bytes) -> bool:
        self.calls.append(f"create:{key}")
        self.marker_attempts += 1
        if self.marker is not None:
            return False
        self.marker = payload
        return True


def _request() -> FinalizePublicationRequest:
    return FinalizePublicationRequest(
        run_prefix="runs/a",
        run_uid="run-1",
        workflow_name="wf",
        workflow_uid="workflow-1",
        main_node_identity="run",
        workload_image_digest="sha256:" + "1" * 64,
        platform_image_digest="sha256:" + "2" * 64,
        bundle_digest="3" * 64,
        objects=(
            ImmutableObject(key="runs/a/z.json", size=1, sha256="4" * 64),
            ImmutableObject(key="runs/a/a.json", size=2, sha256="5" * 64),
        ),
        provenance=ImmutableObject(
            key="runs/a/provenance.json",
            size=2,
            sha256=hashlib.sha256(b"{}").hexdigest(),
        ),
        finalizer_started_at=datetime(2026, 8, 24, 1, 3, tzinfo=UTC),
    )


def _store(workflow: bytes | None = None) -> Store:
    request = _request()
    values = {item.key: (b"x", item) for item in request.objects}
    values[request.provenance.key] = (
        b"{}",
        request.provenance,
    )
    values["workflow"] = (
        workflow or _workflow(),
        ImmutableObject(key="workflow", size=0, sha256="0" * 64),
    )
    return Store(values)


def test_verified_commit_marker_and_idempotent_duplicate() -> None:
    store = _store()

    first = finalize_publication(_request(), store, "workflow")
    second = finalize_publication(_request(), store, "workflow")

    assert first == CommitOutcome.COMMITTED
    assert second == CommitOutcome.ALREADY_COMMITTED
    assert store.marker_attempts == 2
    assert store.marker is not None
    marker = parse_commit_marker(store.marker)
    assert marker.main_finished_at == "2026-08-24T01:02:03.456789Z"
    assert tuple(item.key for item in marker.objects) == (
        "runs/a/a.json",
        "runs/a/z.json",
    )
    assert store.calls.index("head:runs/a/a.json") < store.calls.index(
        "create:runs/a/COMMITTED.json"
    )
    assert store.marker == commit_marker_bytes(marker)


def test_conflicting_marker_is_collision() -> None:
    store = _store()
    store.marker = b'{"schema":"committed/v1","runUid":"other"}\n'

    outcome = finalize_publication(_request(), store, "workflow")

    assert outcome == CommitOutcome.COMMIT_COLLISION
    assert store.marker_attempts == 1


@pytest.mark.parametrize(
    "workflow",
    [
        b'{"metadata":{"name":"wf","uid":"workflow-1"},"status":{"nodes":{}}}',
        _workflow("bad"),
        _workflow("2026-08-24T01:04:00Z"),
        _workflow().replace(b'"phase": "Succeeded"', b'"phase": "Failed"'),
        _workflow().replace(b'"type": "Pod"', b'"type": "Steps"'),
    ],
)
def test_malformed_or_ineligible_workflow_blocks_marker(workflow: bytes) -> None:
    store = _store(workflow)

    outcome = finalize_publication(_request(), store, "workflow")

    assert outcome == CommitOutcome.REFUSED
    assert store.marker_attempts == 0


def test_ambiguous_main_node_blocks_marker() -> None:
    store = _store(_workflow(duplicate=True))

    assert finalize_publication(_request(), store, "workflow") == CommitOutcome.REFUSED
    assert store.marker_attempts == 0


def test_marker_schema_is_strict() -> None:
    store = _store()
    assert finalize_publication(_request(), store, "workflow") == CommitOutcome.COMMITTED
    assert store.marker is not None

    with pytest.raises(ValidationError):
        _ = parse_commit_marker(store.marker.rstrip(b"\n")[:-1] + b',"extra":1}\n')


def test_committed_schema_is_byte_locked() -> None:
    schema_path = Path(__file__).resolve().parents[2] / "schemas/committed-v1.json"

    assert schema_path.read_bytes() == committed_schema_bytes()


def test_cancelled_publication_never_attempts_marker() -> None:
    store = _store()
    request = _request().model_copy(update={"publication_state": PublicationState.CANCELLED})

    assert finalize_publication(request, store, "workflow") == CommitOutcome.REFUSED
    assert store.marker_attempts == 0


def test_corrupt_object_head_never_attempts_marker() -> None:
    store = _store()
    expected = _request().objects[0]
    store.objects[expected.key] = (
        b"corrupt",
        ImmutableObject(key=expected.key, size=99, sha256="9" * 64),
    )

    assert finalize_publication(_request(), store, "workflow") == CommitOutcome.REFUSED
    assert store.marker_attempts == 0


@pytest.mark.parametrize(
    ("size", "sha256"),
    [(999, hashlib.sha256(b"{}").hexdigest()), (2, "9" * 64)],
)
def test_wrong_provenance_head_never_attempts_marker(size: int, sha256: str) -> None:
    request = _request()
    store = _store()
    store.objects[request.provenance.key] = (
        b"{}",
        ImmutableObject(key=request.provenance.key, size=size, sha256=sha256),
    )

    assert finalize_publication(request, store, "workflow") == CommitOutcome.REFUSED
    assert store.marker_attempts == 0


def test_missing_provenance_never_attempts_marker() -> None:
    request = _request()
    store = _store()
    del store.objects[request.provenance.key]

    assert finalize_publication(request, store, "workflow") == CommitOutcome.REFUSED
    assert store.marker_attempts == 0
