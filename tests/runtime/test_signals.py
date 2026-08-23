from __future__ import annotations

import os
import signal
from dataclasses import dataclass
from multiprocessing import Process
from pathlib import Path

from typer.testing import CliRunner

from three_t_clip_pipeline.cli import app
from three_t_clip_pipeline.render import constants as render_constants
from three_t_clip_pipeline.runtime.lifecycle import (
    ARTIFACT_PUBLISHER_NAME,
    BUNDLE_INIT_NAME,
    CACHE_MOUNT_PATH,
    COMMIT_MARKER_NAME,
    COMMIT_RESULTS_NAME,
    CONTROL_MOUNT_PATH,
    INIT_MANIFEST_NAME,
    INPUT_MOUNT_PATH,
    MAIN_NAME,
    OUTPUT_MOUNT_PATH,
    PUBLICATION_MANIFEST_NAME,
    PublisherRequest,
    run_publisher_sidecar,
)
from three_t_clip_pipeline.runtime.status import MarkerResult, ObjectRecord, PublicationManifest


@dataclass(frozen=True, slots=True)
class InterruptibleStore:
    started_fd: int
    release_fd: int
    status_path: Path

    def upload_immutable(self, source: Path, key: str) -> ObjectRecord:
        del source, key
        _ = os.write(self.started_fd, b"1")
        _ = os.read(self.release_fd, 1)
        message = "SIGTERM must interrupt the blocked upload"
        raise AssertionError(message)

    def put_manifest(self, key: str, payload: bytes) -> None:
        del key
        _ = self.status_path.write_bytes(payload)

    def create_marker(self, key: str, payload: bytes) -> MarkerResult:
        del key, payload
        count_path = self.status_path.with_name("marker-count")
        count = int(count_path.read_text(encoding="utf-8"))
        _ = count_path.write_text(str(count + 1), encoding="utf-8")
        return MarkerResult.CREATED


def _publisher_process(output: Path, status_path: Path, started_fd: int, release_fd: int) -> None:
    store = InterruptibleStore(started_fd, release_fd, status_path)
    request = PublisherRequest(
        run_prefix="runs/signal",
        run_uid="run-signal",
        output_root=output,
        required_paths=("result.bin",),
        publication_final_retry_seconds=2,
        termination_grace_seconds=2,
    )
    _ = run_publisher_sidecar(request, store)


def test_sigterm_cancels_bounded_without_commit(tmp_path: Path) -> None:
    # Given
    output = tmp_path / "output"
    output.mkdir()
    _ = (output / "result.bin").write_bytes(b"partial")
    status_path = tmp_path / "publication.json"
    marker_count_path = tmp_path / "marker-count"
    _ = marker_count_path.write_text("0", encoding="utf-8")
    started_read, started_write = os.pipe()
    release_read, release_write = os.pipe()
    process = Process(
        target=_publisher_process,
        args=(output, status_path, started_write, release_read),
    )

    try:
        process.start()
        os.close(started_write)
        os.close(release_read)
        assert os.read(started_read, 1) == b"1"

        # When
        pid = process.pid
        assert pid is not None
        os.kill(pid, signal.SIGTERM)
        process.join(timeout=2)

        # Then
        assert process.exitcode == 0
        manifest = PublicationManifest.model_validate_json(status_path.read_bytes())
        assert manifest.publication_state.value in {"cancelled", "failed"}
        assert manifest.diagnostics
        assert marker_count_path.read_text(encoding="utf-8") == "0"
    finally:
        if process.is_alive():
            process.kill()
            process.join(timeout=2)
        os.close(started_read)
        os.close(release_write)


def test_repeated_sigterm_remains_bounded(tmp_path: Path) -> None:
    # Given
    output = tmp_path / "output"
    output.mkdir()
    _ = (output / "result.bin").write_bytes(b"partial")
    status_path = tmp_path / "publication.json"
    started_read, started_write = os.pipe()
    release_read, release_write = os.pipe()
    process = Process(
        target=_publisher_process,
        args=(output, status_path, started_write, release_read),
    )

    try:
        process.start()
        os.close(started_write)
        os.close(release_read)
        assert os.read(started_read, 1) == b"1"

        # When
        pid = process.pid
        assert pid is not None
        os.kill(pid, signal.SIGTERM)
        os.kill(pid, signal.SIGTERM)
        process.join(timeout=2)

        # Then
        assert process.exitcode == 0
        manifest = PublicationManifest.model_validate_json(status_path.read_bytes())
        assert manifest.publication_state.value in {"cancelled", "failed"}
    finally:
        if process.is_alive():
            process.kill()
            process.join(timeout=2)
        os.close(started_read)
        os.close(release_write)


def test_runtime_entrypoint_exposes_platform_commands_as_argv() -> None:
    # Given
    runner = CliRunner()

    # When
    result = runner.invoke(app, ["runtime", "--help"])

    # Then
    assert result.exit_code == 0
    assert {"init", "publisher", "finalize"} <= set(result.stdout.split())


def test_rendered_phase_argv_is_accepted_without_shell_or_credentials() -> None:
    # Given
    runner = CliRunner()

    # When
    result = runner.invoke(app, ["runtime", "init", "--phase", "preflight"])

    # Then
    assert result.exit_code == 0
    assert result.stdout == ""


def test_renderer_and_runtime_frozen_constants_cannot_drift() -> None:
    # Given / When
    renderer = (
        render_constants.BUNDLE_INIT_NAME,
        render_constants.MAIN_NAME,
        render_constants.ARTIFACT_PUBLISHER_NAME,
        render_constants.COMMIT_RESULTS_NAME,
        render_constants.INPUT_MOUNT,
        render_constants.CACHE_MOUNT,
        render_constants.CONTROL_MOUNT,
        render_constants.OUTPUT_MOUNT,
        render_constants.INIT_MANIFEST_NAME,
        render_constants.PUBLICATION_MANIFEST_NAME,
        render_constants.COMMIT_MARKER_NAME,
    )
    runtime = (
        BUNDLE_INIT_NAME,
        MAIN_NAME,
        ARTIFACT_PUBLISHER_NAME,
        COMMIT_RESULTS_NAME,
        INPUT_MOUNT_PATH,
        CACHE_MOUNT_PATH,
        CONTROL_MOUNT_PATH,
        OUTPUT_MOUNT_PATH,
        INIT_MANIFEST_NAME,
        PUBLICATION_MANIFEST_NAME,
        COMMIT_MARKER_NAME,
    )

    # Then
    assert runtime == renderer


def test_final_flush_budget_never_exceeds_termination_grace(tmp_path: Path) -> None:
    # Given
    request = PublisherRequest(
        run_prefix="runs/bounds",
        run_uid="run-bounds",
        output_root=tmp_path,
        required_paths=("result.bin",),
        publication_final_retry_seconds=300,
        termination_grace_seconds=120,
    )

    # When
    budget = request.final_flush_seconds

    # Then
    assert budget == 120
