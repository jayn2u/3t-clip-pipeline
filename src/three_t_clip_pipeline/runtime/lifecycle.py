"""Runtime lifecycle state machine."""

from __future__ import annotations

import signal
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Final, Protocol

from pydantic import ValidationError

from three_t_clip_pipeline.runtime.status import (
    CommitMarker,
    InitManifest,
    MainStatus,
    MarkerResult,
    ObjectRecord,
    PublicationManifest,
    PublicationState,
    commit_marker_key,
    init_manifest_key,
    owned_destination,
    publication_manifest_key,
    runtime_json_bytes,
    safe_relative_path,
)

if TYPE_CHECKING:
    from types import FrameType

BUNDLE_INIT_NAME: Final = "bundle-init"
MAIN_NAME: Final = "main"
ARTIFACT_PUBLISHER_NAME: Final = "artifact-publisher"
COMMIT_RESULTS_NAME: Final = "commit-results"
INPUT_MOUNT_PATH: Final = "/workspace/input"
CACHE_MOUNT_PATH: Final = "/workspace/cache"
CONTROL_MOUNT_PATH: Final = "/workspace/control"
OUTPUT_MOUNT_PATH: Final = "/workspace/output"
INIT_MANIFEST_NAME: Final = "init.json"
PUBLICATION_MANIFEST_NAME: Final = "publication.json"
COMMIT_MARKER_NAME: Final = "COMMITTED.json"
_RESERVED_OUTPUT_NAMES: Final = frozenset({PUBLICATION_MANIFEST_NAME, COMMIT_MARKER_NAME})


@dataclass(frozen=True, slots=True)
class InitRequest:
    """Typed initialization inputs from the rendered platform container."""

    run_prefix: str
    run_uid: str
    bundle_uri: str
    bundle_sha256: str
    cache_objects: tuple[tuple[str, str, str], ...]
    input_root: Path
    cache_root: Path

    def __post_init__(self) -> None:
        """Reject unsafe rendered paths at construction."""
        _ = safe_relative_path(self.run_prefix)
        _ = safe_relative_path(self.run_uid)
        for _, _, destination in self.cache_objects:
            _ = safe_relative_path(destination)


@dataclass(frozen=True, slots=True)
class PublisherRequest:
    """Typed publisher inputs and bounded termination budgets."""

    run_prefix: str
    run_uid: str
    output_root: Path
    required_paths: tuple[str, ...]
    publication_final_retry_seconds: int = 300
    termination_grace_seconds: int = 120

    def __post_init__(self) -> None:
        """Reject unsafe paths and unbounded shutdown values."""
        _ = safe_relative_path(self.run_prefix)
        _ = safe_relative_path(self.run_uid)
        for path in self.required_paths:
            _ = safe_relative_path(path)
        if self.publication_final_retry_seconds <= 0 or self.termination_grace_seconds <= 0:
            message = "publisher time bounds must be positive"
            raise ValueError(message)

    @property
    def final_flush_seconds(self) -> int:
        """Cap final retries by the pod's termination grace period."""
        return min(self.publication_final_retry_seconds, self.termination_grace_seconds)


@dataclass(frozen=True, slots=True)
class FinalizeRequest:
    """Stable workflow result and required publication boundary."""

    run_prefix: str
    run_uid: str
    main_status: MainStatus
    required_paths: tuple[str, ...]

    def __post_init__(self) -> None:
        """Reject unsafe workflow and required-object paths."""
        _ = safe_relative_path(self.run_prefix)
        _ = safe_relative_path(self.run_uid)
        for path in self.required_paths:
            _ = safe_relative_path(path)


class _InitStore(Protocol):
    def download_verified(
        self, source_uri: str, destination: Path, sha256: str
    ) -> ObjectRecord: ...
    def put_manifest(self, key: str, payload: bytes) -> None: ...


class _PublisherStore(Protocol):
    def upload_immutable(self, source: Path, key: str) -> ObjectRecord: ...
    def put_manifest(self, key: str, payload: bytes) -> None: ...


class _FinalizerStore(Protocol):
    def get_manifest(self, key: str) -> bytes: ...
    def head(self, key: str) -> ObjectRecord: ...
    def create_marker(self, key: str, payload: bytes) -> MarkerResult: ...


def initialize_bundle(request: InitRequest, store: _InitStore) -> InitManifest:
    """Hydrate verified immutable inputs and upload init proof before returning."""
    request.input_root.mkdir(parents=True, exist_ok=True)
    request.cache_root.mkdir(parents=True, exist_ok=True)
    records = [
        store.download_verified(
            request.bundle_uri,
            owned_destination(request.input_root, "bundle"),
            request.bundle_sha256,
        )
    ]
    for source_uri, sha256, destination in request.cache_objects:
        records.append(
            store.download_verified(
                source_uri,
                owned_destination(request.cache_root, f"{destination}/inventory.json"),
                sha256,
            )
        )
    manifest = InitManifest(objects=tuple(records))
    key = init_manifest_key(request.run_prefix, request.run_uid)
    store.put_manifest(key, runtime_json_bytes(manifest))
    return manifest


def _discover_output_files(root: Path) -> tuple[tuple[str, Path], ...]:
    files: list[tuple[str, Path]] = []
    for candidate in sorted(root.rglob("*")):
        if candidate.is_symlink() or not candidate.is_file():
            continue
        relative = candidate.relative_to(root).as_posix()
        _ = safe_relative_path(relative)
        if PurePosixPath(relative).name not in _RESERVED_OUTPUT_NAMES:
            files.append((relative, candidate))
    return tuple(files)


def publish_outputs(request: PublisherRequest, store: _PublisherStore) -> PublicationManifest:
    """Upload a finite output snapshot and terminal publication manifest."""
    records = tuple(
        store.upload_immutable(source, f"{request.run_prefix}/{relative}")
        for relative, source in _discover_output_files(request.output_root)
    )
    manifest = PublicationManifest(
        publicationState=PublicationState.COMPLETED, objects=records, diagnostics=()
    )
    store.put_manifest(
        publication_manifest_key(request.run_prefix, request.run_uid), runtime_json_bytes(manifest)
    )
    return manifest


@dataclass(slots=True)
class _FinalFlushState:
    """Accumulate uploads and interrupted keys across TERM handling."""

    records: dict[str, ObjectRecord]
    interrupted_keys: set[str]


def _final_flush(
    request: PublisherRequest, store: _PublisherStore, state: _FinalFlushState
) -> PublicationManifest:
    def timeout(_signum: int, _frame: FrameType | None) -> None:
        raise TimeoutError

    _ = signal.signal(signal.SIGALRM, timeout)
    _ = signal.setitimer(signal.ITIMER_REAL, float(request.final_flush_seconds))
    try:
        for relative, source in _discover_output_files(request.output_root):
            key = f"{request.run_prefix}/{relative}"
            if key not in state.records and key not in state.interrupted_keys:
                state.records[key] = store.upload_immutable(source, key)
    except TimeoutError:
        publication_state = PublicationState.FAILED
        diagnostics = ("final_flush_timeout",)
    else:
        publication_state = (
            PublicationState.CANCELLED if state.interrupted_keys else PublicationState.COMPLETED
        )
        diagnostics = ("sigterm_during_upload",) if state.interrupted_keys else ()
    finally:
        _ = signal.setitimer(signal.ITIMER_REAL, 0.0)
    manifest = PublicationManifest(
        publicationState=publication_state,
        objects=tuple(state.records[key] for key in sorted(state.records)),
        diagnostics=diagnostics,
    )
    store.put_manifest(
        publication_manifest_key(request.run_prefix, request.run_uid), runtime_json_bytes(manifest)
    )
    return manifest


def run_publisher_sidecar(request: PublisherRequest, store: _PublisherStore) -> PublicationManifest:
    """Trap TERM, stop discovery, persist diagnostics, and exit without marker access."""
    received = False
    active_key: str | None = None
    state = _FinalFlushState(records={}, interrupted_keys=set())
    previous = signal.getsignal(signal.SIGTERM)
    previous_alarm = signal.getsignal(signal.SIGALRM)

    def terminate(_signum: int, _frame: FrameType | None) -> None:
        nonlocal received
        if not received:
            received = True
            if active_key is not None:
                state.interrupted_keys.add(active_key)
            raise InterruptedError

    _ = signal.signal(signal.SIGTERM, terminate)
    _ = signal.signal(signal.SIGALRM, lambda _signum, _frame: None)
    _ = signal.setitimer(signal.ITIMER_REAL, 1.0, 1.0)
    try:
        while True:
            for relative, source in _discover_output_files(request.output_root):
                key = f"{request.run_prefix}/{relative}"
                if key in state.records:
                    continue
                active_key = key
                state.records[key] = store.upload_immutable(source, key)
                active_key = None
            signal.pause()
    except InterruptedError:
        return _final_flush(request, store, state)
    finally:
        _ = signal.setitimer(signal.ITIMER_REAL, 0.0)
        _ = signal.signal(signal.SIGTERM, previous)
        _ = signal.signal(signal.SIGALRM, previous_alarm)


def _verified_publication(
    request: FinalizeRequest, store: _FinalizerStore
) -> PublicationManifest | None:
    try:
        init = InitManifest.model_validate_json(
            store.get_manifest(init_manifest_key(request.run_prefix, request.run_uid))
        )
        publication = PublicationManifest.model_validate_json(
            store.get_manifest(publication_manifest_key(request.run_prefix, request.run_uid))
        )
    except (KeyError, ValidationError):
        return None
    if init.state != "completed" or publication.publication_state != PublicationState.COMPLETED:
        return None
    expected = {f"{request.run_prefix}/{path}" for path in request.required_paths}
    records = {record.key: record for record in publication.objects}
    if not expected.issubset(records):
        return None
    try:
        heads_match = all(store.head(record.key) == record for record in publication.objects)
    except (KeyError, ValueError):
        return None
    if not heads_match:
        return None
    return publication


def finalize_results(request: FinalizeRequest, store: _FinalizerStore) -> MarkerResult:
    """Conditionally commit only a successful, complete, HEAD-verified publication."""
    if request.main_status != MainStatus.SUCCEEDED:
        return MarkerResult.REFUSED
    publication = _verified_publication(request, store)
    if publication is None:
        return MarkerResult.REFUSED
    marker = CommitMarker(runUid=request.run_uid, objects=publication.objects)
    return store.create_marker(commit_marker_key(request.run_prefix), runtime_json_bytes(marker))
