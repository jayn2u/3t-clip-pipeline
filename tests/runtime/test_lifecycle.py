from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from three_t_clip_pipeline.contract import load_workload
from three_t_clip_pipeline.render import render_workflow_bytes
from three_t_clip_pipeline.runtime.lifecycle import (
    FinalizeRequest,
    InitRequest,
    PublisherRequest,
    finalize_results,
    initialize_bundle,
    publish_outputs,
)
from three_t_clip_pipeline.runtime.status import (
    InitManifest,
    MainStatus,
    MarkerResult,
    ObjectRecord,
    PublicationManifest,
    PublicationState,
)


def _record(key: str, content: bytes) -> ObjectRecord:
    return ObjectRecord(key=key, size=len(content), sha256=hashlib.sha256(content).hexdigest())


def _finalize(store: RecordingStore, status: MainStatus = MainStatus.SUCCEEDED) -> MarkerResult:
    return finalize_results(
        FinalizeRequest(
            run_prefix="runs/example",
            run_uid="run-123",
            main_status=status,
            required_paths=("metrics.json",),
        ),
        store,
    )


@dataclass
class RecordingStore:
    source: dict[str, bytes]
    calls: list[str] = field(default_factory=list)
    objects: dict[str, bytes] = field(default_factory=dict)
    marker_calls: int = 0

    def download_verified(self, source_uri: str, destination: Path, sha256: str) -> ObjectRecord:
        self.calls.append(f"download:{source_uri}")
        content = self.source[source_uri]
        assert hashlib.sha256(content).hexdigest() == sha256
        destination.parent.mkdir(parents=True, exist_ok=True)
        _ = destination.write_bytes(content)
        return _record(destination.as_posix(), content)

    def upload_immutable(self, source: Path, key: str) -> ObjectRecord:
        self.calls.append(f"upload:{key}")
        content = source.read_bytes()
        self.objects[key] = content
        return _record(key, content)

    def put_manifest(self, key: str, payload: bytes) -> None:
        self.calls.append(f"manifest:{key}")
        self.objects[key] = payload

    def get_manifest(self, key: str) -> bytes:
        self.calls.append(f"get:{key}")
        return self.objects[key]

    def head(self, key: str) -> ObjectRecord:
        self.calls.append(f"head:{key}")
        return _record(key, self.objects[key])

    def create_marker(self, key: str, payload: bytes) -> MarkerResult:
        self.calls.append(f"marker:{key}")
        self.marker_calls += 1
        if key in self.objects:
            return MarkerResult.ALREADY_EXISTS
        self.objects[key] = payload
        return MarkerResult.CREATED


@pytest.fixture
def successful_store(tmp_path: Path) -> tuple[RecordingStore, InitRequest, PublisherRequest]:
    bundle = b"verified bundle"
    cache = b"verified cache inventory"
    store = RecordingStore(source={"s3://code/bundle": bundle, "s3://cache/inventory": cache})
    init_request = InitRequest(
        run_prefix="runs/example",
        run_uid="run-123",
        bundle_uri="s3://code/bundle",
        bundle_sha256=hashlib.sha256(bundle).hexdigest(),
        cache_objects=(("s3://cache/inventory", hashlib.sha256(cache).hexdigest(), "data"),),
        input_root=tmp_path / "input",
        cache_root=tmp_path / "cache",
    )
    output_root = tmp_path / "output"
    output_root.mkdir()
    _ = (output_root / "metrics.json").write_bytes(b'{"accuracy":1}')
    publisher_request = PublisherRequest(
        run_prefix="runs/example",
        run_uid="run-123",
        output_root=output_root,
        required_paths=("metrics.json",),
    )
    return store, init_request, publisher_request


def test_successful_boundary(
    successful_store: tuple[RecordingStore, InitRequest, PublisherRequest],
) -> None:
    # Given
    store, init_request, publisher_request = successful_store

    # When
    init_manifest = initialize_bundle(init_request, store)
    publication_manifest = publish_outputs(publisher_request, store)
    outcome = _finalize(store)

    # Then
    assert init_manifest.state == "completed"
    assert publication_manifest.publication_state == PublicationState.COMPLETED
    assert outcome == MarkerResult.CREATED
    assert store.marker_calls == 1
    init_index = store.calls.index("manifest:runs/example/.staging/run-123/init.json")
    upload_index = store.calls.index("upload:runs/example/metrics.json")
    assert init_index < upload_index
    assert store.calls.index("upload:runs/example/metrics.json") < store.calls.index(
        "marker:runs/example/COMMITTED.json"
    )


@pytest.mark.parametrize(
    "status",
    [MainStatus.FAILED, MainStatus.CANCELLED, MainStatus.TIMED_OUT],
)
def test_finalizer_refuses_non_successful_main(
    successful_store: tuple[RecordingStore, InitRequest, PublisherRequest], status: MainStatus
) -> None:
    # Given
    store, init_request, publisher_request = successful_store
    _ = initialize_bundle(init_request, store)
    _ = publish_outputs(publisher_request, store)

    # When
    outcome = _finalize(store, status)

    # Then
    assert outcome == MarkerResult.REFUSED
    assert store.marker_calls == 0


def test_finalizer_refuses_corrupt_uploaded_object(
    successful_store: tuple[RecordingStore, InitRequest, PublisherRequest],
) -> None:
    # Given
    store, init_request, publisher_request = successful_store
    _ = initialize_bundle(init_request, store)
    _ = publish_outputs(publisher_request, store)
    store.objects["runs/example/metrics.json"] = b"corrupt"

    # When
    outcome = _finalize(store)

    # Then
    assert outcome == MarkerResult.REFUSED
    assert store.marker_calls == 0


def test_finalizer_refuses_missing_uploaded_object(
    successful_store: tuple[RecordingStore, InitRequest, PublisherRequest],
) -> None:
    # Given
    store, init_request, publisher_request = successful_store
    _ = initialize_bundle(init_request, store)
    _ = publish_outputs(publisher_request, store)
    del store.objects["runs/example/metrics.json"]

    # When
    outcome = _finalize(store)

    # Then
    assert outcome == MarkerResult.REFUSED
    assert store.marker_calls == 0


def test_publisher_filters_symlink_and_platform_control_files(tmp_path: Path) -> None:
    # Given
    output = tmp_path / "output"
    output.mkdir()
    _ = (output / "metrics.json").write_bytes(b"{}")
    _ = (output / "publication.json").write_bytes(b"misleading")
    (output / "escape").symlink_to(tmp_path / "outside")
    store = RecordingStore(source={})

    # When
    manifest = publish_outputs(
        PublisherRequest(
            run_prefix="runs/filter",
            run_uid="run-filter",
            output_root=output,
            required_paths=("metrics.json",),
        ),
        store,
    )

    # Then
    assert tuple(item.key for item in manifest.objects) == ("runs/filter/metrics.json",)
    assert "runs/filter/escape" not in store.objects
    assert store.objects["runs/filter/.staging/run-filter/publication.json"] != b"misleading"


@pytest.mark.parametrize("unsafe_path", ["../escape", "/absolute", "dir/../../escape"])
def test_runtime_request_rejects_path_traversal(tmp_path: Path, unsafe_path: str) -> None:
    # Given / When / Then
    with pytest.raises(ValueError, match="safe relative POSIX path"):
        _ = PublisherRequest(
            run_prefix="runs/path",
            run_uid="run-path",
            output_root=tmp_path,
            required_paths=(unsafe_path,),
        )


def test_initializer_rejects_symlink_escape(tmp_path: Path) -> None:
    # Given
    cache_root = tmp_path / "cache"
    outside = tmp_path / "outside"
    cache_root.mkdir()
    outside.mkdir()
    (cache_root / "data").symlink_to(outside, target_is_directory=True)
    content = b"inventory"
    store = RecordingStore(source={"s3://cache/inventory": content, "s3://bundle": b"bundle"})
    request = InitRequest(
        run_prefix="runs/path",
        run_uid="run-path",
        bundle_uri="s3://bundle",
        bundle_sha256=hashlib.sha256(b"bundle").hexdigest(),
        cache_objects=(("s3://cache/inventory", hashlib.sha256(content).hexdigest(), "data"),),
        input_root=tmp_path / "input",
        cache_root=cache_root,
    )

    # When / Then
    with pytest.raises(ValueError, match="task-owned root"):
        _ = initialize_bundle(request, store)
    assert not (outside / "inventory.json").exists()


def test_malformed_manifest_refuses_commit_without_success_output(
    successful_store: tuple[RecordingStore, InitRequest, PublisherRequest],
) -> None:
    # Given
    store, init_request, publisher_request = successful_store
    _ = initialize_bundle(init_request, store)
    _ = publish_outputs(publisher_request, store)
    store.objects["runs/example/.staging/run-123/publication.json"] = b'{"status":"success"}'

    # When
    outcome = _finalize(store)

    # Then
    assert outcome == MarkerResult.REFUSED
    assert store.marker_calls == 0


def test_manifests_are_strict_boundary_models() -> None:
    # Given
    init_payload = b'{"schema":"runtime-init/v1","state":"completed","objects":[],"extra":1}'
    publication_payload = (
        b'{"schema":"runtime-publication/v1","publicationState":"completed",'
        b'"objects":[],"diagnostics":[],"extra":1}'
    )

    # When / Then
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        _ = InitManifest.model_validate_json(init_payload)
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        _ = PublicationManifest.model_validate_json(publication_payload)


def test_renderer_freezes_role_specific_mount_modes() -> None:
    # Given
    root = Path(__file__).resolve().parents[2]
    workload = load_workload(root / "examples/workload-minimal.yaml")
    rendered = render_workflow_bytes(workload).decode()

    # When
    main = rendered[rendered.index("      name: main\n") : rendered.index("    initContainers:\n")]
    bundle = rendered[
        rendered.index("      name: bundle-init\n") : rendered.index(
            "      name: artifact-publisher\n"
        )
    ]
    publisher = rendered[
        rendered.index("      name: artifact-publisher\n") : rendered.index("    name: workload\n")
    ]
    finalizer = rendered[rendered.rindex("      name: commit-results\n") :]

    # Then
    assert "mountPath: /workspace/cache\n        name: cache\n        readOnly: true" in main
    assert "mountPath: /workspace/input\n        name: input\n        readOnly: true" in main
    assert "mountPath: /workspace/output\n        name: output\n" in main
    assert "readOnly" not in bundle
    assert "mountPath: /workspace/control\n        name: control\n" in publisher
    assert "mountPath: /workspace/output\n        name: output\n        readOnly: true" in publisher
    assert "volumeMounts" not in finalizer
