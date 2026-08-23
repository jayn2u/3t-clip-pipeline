from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Final

import pytest
from pydantic import ValidationError

from tests.fakes.s3 import (
    FakeS3,
    FakeS3CancelledError,
    FakeS3ChecksumMismatchError,
    FakeS3PreconditionFailedError,
    FakeS3RuntimeStore,
    FakeS3TimeoutError,
)
from three_t_clip_pipeline.contract import canonical_json_bytes, load_workload
from three_t_clip_pipeline.portable_smoke import SmokeCase, SmokeReport
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
    CommitMarker,
    MainStatus,
    MarkerResult,
    PublicationManifest,
    commit_marker_key,
    runtime_json_bytes,
)

_ROOT: Final = Path(__file__).parents[2]
_WORKLOAD: Final = _ROOT / "examples/consumer/hello/workload.yaml"
_CONSUMER: Final = _ROOT / "examples/consumer/hello/run.py"
_BUNDLE: Final = b"hello consumer bundle\n"


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class _FinalizationInput:
    publication: PublicationManifest
    request: FinalizeRequest


def _run_consumer(case: SmokeCase, output_root: Path) -> MainStatus:
    environment = os.environ.copy()
    environment["OUTPUT_ROOT"] = str(output_root)
    if case == SmokeCase.MAIN_NONZERO:
        environment["HELLO_EXIT_CODE"] = "7"
    completed = subprocess.run(
        [sys.executable, str(_CONSUMER)], check=False, env=environment, timeout=5
    )
    return MainStatus.SUCCEEDED if completed.returncode == 0 else MainStatus.FAILED


def _publish(
    case: SmokeCase, request: PublisherRequest, store: FakeS3RuntimeStore
) -> tuple[PublicationManifest | None, str | None]:
    if case == SmokeCase.UPLOAD_TIMEOUT:
        store.s3.delay_put_until_timeout(f"{request.run_prefix}/provenance.json")
    if case in {SmokeCase.CANCEL, SmokeCase.CANCEL_RESUME}:
        store.s3.cancel_put(f"{request.run_prefix}/provenance.json")
    try:
        return publish_outputs(request, store), None
    except FakeS3TimeoutError:
        diagnostic = "upload_timeout"
    except FakeS3CancelledError:
        diagnostic = "cancelled"
    store.write_terminal_publication(request.run_prefix, request.run_uid, diagnostic)
    return None, diagnostic


def _finalize(
    case: SmokeCase, boundary: _FinalizationInput, store: FakeS3RuntimeStore
) -> str | None:
    publication = boundary.publication
    marker_key = commit_marker_key(boundary.request.run_prefix)
    match case:  # noqa: MATCH_OK
        case SmokeCase.MARKER_RACE:
            marker = CommitMarker(runUid=boundary.request.run_uid, objects=publication.objects)
            store.s3.seed(marker_key, runtime_json_bytes(marker))
        case SmokeCase.MARKER_COLLISION:
            marker = CommitMarker(runUid="conflicting-run", objects=publication.objects)
            store.s3.seed(marker_key, runtime_json_bytes(marker))
        case (
            SmokeCase.SUCCESS
            | SmokeCase.CORRUPT_OBJECT
            | SmokeCase.CORRUPT_COMMITTED
            | SmokeCase.MISSING_OUTPUT
            | SmokeCase.MAIN_NONZERO
            | SmokeCase.UPLOAD_TIMEOUT
            | SmokeCase.CANCEL
            | SmokeCase.CANCEL_RESUME
            | SmokeCase.PARTIAL_PUBLICATION
        ):
            pass
    outcome = finalize_results(boundary.request, store)
    match outcome:  # noqa: MATCH_OK
        case MarkerResult.CREATED | MarkerResult.ALREADY_EXISTS:
            diagnostic = None
        case MarkerResult.COLLISION:
            diagnostic = "marker_collision"
        case MarkerResult.REFUSED:
            diagnostic = (
                "main_nonzero"
                if boundary.request.main_status == MainStatus.FAILED
                else "missing_required_output"
            )
    if case == SmokeCase.PARTIAL_PUBLICATION:
        store.s3.seed(
            f"{boundary.request.run_prefix}/untracked.partial",
            b"partial",
            {"sha256": _sha256(b"partial")},
        )
    if case == SmokeCase.CORRUPT_COMMITTED:
        store.s3.corrupt_get(f"{boundary.request.run_prefix}/result.json")
    return diagnostic


def run_portable_slice(case: SmokeCase | str) -> SmokeReport:
    selected = SmokeCase(case)
    workload = load_workload(_WORKLOAD)
    workload_bytes = canonical_json_bytes(workload)
    workflow_bytes = render_workflow_bytes(workload)
    run_uid = _sha256(workload_bytes)
    run_prefix = workload.spec.outputs.prefix
    s3 = FakeS3()
    s3.seed("pipeline-code/hello.bundle", _BUNDLE, {"sha256": _sha256(_BUNDLE)})
    store = FakeS3RuntimeStore(s3)
    diagnostic: str | None = None

    with TemporaryDirectory(prefix="3t-portable-") as temporary:
        workspace = Path(temporary)
        init_request = InitRequest(
            run_prefix=run_prefix,
            run_uid=run_uid,
            bundle_uri=workload.spec.bundle.s3_uri,
            bundle_sha256=workload.spec.bundle.sha256,
            cache_objects=(),
            input_root=workspace / "input",
            cache_root=workspace / "cache",
        )
        if selected == SmokeCase.CORRUPT_OBJECT:
            s3.corrupt_get("pipeline-code/hello.bundle")
        try:
            _ = initialize_bundle(init_request, store)
        except FakeS3ChecksumMismatchError:
            diagnostic = "checksum_mismatch"

        output_root = workspace / "output"
        if diagnostic is None:
            main_status = _run_consumer(selected, output_root)
            if selected == SmokeCase.MISSING_OUTPUT:
                (output_root / "result.json").unlink()
            publisher_request = PublisherRequest(
                run_prefix=run_prefix,
                run_uid=run_uid,
                output_root=output_root,
                required_paths=workload.spec.outputs.required_paths,
            )
            publication, diagnostic = _publish(selected, publisher_request, store)
            if selected == SmokeCase.CANCEL_RESUME:
                with pytest.raises(FakeS3PreconditionFailedError):
                    _ = publish_outputs(publisher_request, store)
            if publication is not None:
                diagnostic = _finalize(
                    selected,
                    _FinalizationInput(
                        publication=publication,
                        request=FinalizeRequest(
                            run_prefix=run_prefix,
                            run_uid=run_uid,
                            main_status=main_status,
                            required_paths=workload.spec.outputs.required_paths,
                        ),
                    ),
                    store,
                )

        reader_committed = store.is_committed(run_prefix, run_uid)
        if selected == SmokeCase.PARTIAL_PUBLICATION and not reader_committed:
            diagnostic = "partial_publication"
        if selected == SmokeCase.CORRUPT_COMMITTED and not reader_committed:
            diagnostic = "committed_object_corrupt"
        marker_key = commit_marker_key(run_prefix)
        marker_body = s3.get(marker_key) if s3.contains(marker_key) else None
        required_hashes = tuple(
            s3.head(f"{run_prefix}/{path}").sha256
            for path in workload.spec.outputs.required_paths
            if s3.contains(f"{run_prefix}/{path}")
        )
        marker_count = s3.marker_count(run_prefix)
        state_verified = marker_count == int(marker_body is not None)

    return SmokeReport(
        case=selected,
        schema_validated=True,
        render_validated=workflow_bytes.startswith(b"apiVersion: argoproj.io/v1alpha1"),
        runtime_validated=True,
        workload_sha256=_sha256(workload_bytes),
        workflow_sha256=_sha256(workflow_bytes),
        run_sha256=run_uid,
        marker_sha256=None if marker_body is None else _sha256(marker_body),
        required_object_hashes=required_hashes,
        conditional_marker_attempts=s3.conditional_marker_attempts,
        marker_count=marker_count,
        reader_status="committed" if reader_committed else "uncommitted",
        diagnostic_code=diagnostic,
        network_calls=0,
        kubernetes_calls=0,
        fake_state_verified=state_verified,
    )


def test_complete_portable_slice_produces_one_readable_commit() -> None:
    # Given / When
    report = run_portable_slice(SmokeCase.SUCCESS)

    # Then
    assert report.reader_status == "committed"
    assert report.conditional_marker_attempts == 1
    assert report.marker_count == 1
    assert len(report.required_object_hashes) == 2
    assert report.fake_state_verified


@pytest.mark.parametrize(
    ("case", "code"),
    [
        (SmokeCase.CORRUPT_OBJECT, "checksum_mismatch"),
        (SmokeCase.CORRUPT_COMMITTED, "committed_object_corrupt"),
        (SmokeCase.MISSING_OUTPUT, "missing_required_output"),
        (SmokeCase.MAIN_NONZERO, "main_nonzero"),
        (SmokeCase.MARKER_COLLISION, "marker_collision"),
        (SmokeCase.UPLOAD_TIMEOUT, "upload_timeout"),
        (SmokeCase.CANCEL, "cancelled"),
        (SmokeCase.CANCEL_RESUME, "cancelled"),
        (SmokeCase.PARTIAL_PUBLICATION, "partial_publication"),
    ],
)
def test_failure_matrix_never_accepts_current_run(case: SmokeCase, code: str) -> None:
    # Given / When
    report = run_portable_slice(case)

    # Then
    assert report.reader_status == "uncommitted"
    assert report.diagnostic_code == code


def test_marker_412_identical_winner_is_idempotently_readable() -> None:
    # Given / When
    report = run_portable_slice(SmokeCase.MARKER_RACE)

    # Then
    assert report.reader_status == "committed"
    assert report.marker_count == 1
    assert report.conditional_marker_attempts == 1


def test_malformed_contract_is_rejected_before_runtime(tmp_path: Path) -> None:
    # Given
    malformed = tmp_path / "workload.yaml"
    _ = malformed.write_text("apiVersion: injected/v9\nkind: Workload\n", encoding="utf-8")

    # When / Then
    with pytest.raises(ValidationError):
        _ = load_workload(malformed)
