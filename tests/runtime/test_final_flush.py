from __future__ import annotations

import hashlib
import os
import signal
from dataclasses import dataclass
from multiprocessing import Process
from pathlib import Path
from time import monotonic

from three_t_clip_pipeline.runtime.lifecycle import PublisherRequest, run_publisher_sidecar
from three_t_clip_pipeline.runtime.status import (
    MarkerResult,
    ObjectRecord,
    PublicationManifest,
    PublicationState,
)


@dataclass(frozen=True, slots=True)
class FinalFlushStore:
    started_fd: int
    release_fd: int
    finished_fd: int
    status_path: Path
    marker_count_path: Path
    block_late: bool = False

    def upload_immutable(self, source: Path, key: str) -> ObjectRecord:
        content = source.read_bytes()
        if source.name == "initial.txt":
            _ = os.write(self.started_fd, b"1")
            _ = os.read(self.release_fd, 1)
            _ = os.write(self.finished_fd, b"1")
        if source.name == "late.txt" and self.block_late:
            signal.pause()
        return ObjectRecord(
            key=key,
            size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
        )

    def put_manifest(self, key: str, payload: bytes) -> None:
        del key
        _ = self.status_path.write_bytes(payload)

    def create_marker(self, key: str, payload: bytes) -> MarkerResult:
        del key, payload
        count = int(self.marker_count_path.read_text(encoding="utf-8"))
        _ = self.marker_count_path.write_text(str(count + 1), encoding="utf-8")
        return MarkerResult.CREATED


def _run_publisher(output: Path, store: FinalFlushStore, final_flush_seconds: int = 2) -> None:
    request = PublisherRequest(
        run_prefix="runs/late",
        run_uid="run-late",
        output_root=output,
        required_paths=("late.txt",),
        publication_final_retry_seconds=final_flush_seconds,
        termination_grace_seconds=final_flush_seconds,
    )
    _ = run_publisher_sidecar(request, store)


def _wait_for_signal_pause(pid: int) -> None:
    deadline = monotonic() + 1.0
    observed = ""
    while monotonic() < deadline:
        observed = Path(f"/proc/{pid}/wchan").read_text(encoding="utf-8")
        if "pause" in observed or "sigtimedwait" in observed:
            return
        os.sched_yield()
    message = f"publisher did not enter signal pause; wchan={observed}"
    raise AssertionError(message)


def test_sigterm_final_flush_includes_required_output_created_before_term(tmp_path: Path) -> None:
    # Given
    output = tmp_path / "output"
    output.mkdir()
    _ = (output / "initial.txt").write_bytes(b"initial")
    status_path = tmp_path / "publication.json"
    marker_count_path = tmp_path / "marker-count"
    _ = marker_count_path.write_text("0", encoding="utf-8")
    started_read, started_write = os.pipe()
    release_read, release_write = os.pipe()
    finished_read, finished_write = os.pipe()
    store = FinalFlushStore(
        started_write,
        release_read,
        finished_write,
        status_path,
        marker_count_path,
    )
    process = Process(target=_run_publisher, args=(output, store))

    try:
        process.start()
        os.close(started_write)
        os.close(release_read)
        os.close(finished_write)
        assert os.read(started_read, 1) == b"1"
        _ = os.write(release_write, b"1")
        assert os.read(finished_read, 1) == b"1"
        pid = process.pid
        assert pid is not None
        _wait_for_signal_pause(pid)
        _ = (output / "late.txt").write_bytes(b"late")

        # When
        os.kill(pid, signal.SIGTERM)
        process.join(timeout=2)

        # Then
        assert process.exitcode == 0
        manifest = PublicationManifest.model_validate_json(status_path.read_bytes())
        assert manifest.publication_state == PublicationState.COMPLETED
        assert tuple(record.key for record in manifest.objects) == (
            "runs/late/initial.txt",
            "runs/late/late.txt",
        )
        assert marker_count_path.read_text(encoding="utf-8") == "0"
    finally:
        if process.is_alive():
            process.kill()
            process.join(timeout=2)
        os.close(started_read)
        os.close(release_write)
        os.close(finished_read)


def test_sigterm_final_flush_timeout_records_failed_state_within_bound(tmp_path: Path) -> None:
    # Given
    output = tmp_path / "output"
    output.mkdir()
    status_path = tmp_path / "publication.json"
    marker_count_path = tmp_path / "marker-count"
    _ = marker_count_path.write_text("0", encoding="utf-8")
    store = FinalFlushStore(-1, -1, -1, status_path, marker_count_path, block_late=True)
    process = Process(target=_run_publisher, args=(output, store, 1))

    try:
        process.start()
        pid = process.pid
        assert pid is not None
        _wait_for_signal_pause(pid)
        _ = (output / "late.txt").write_bytes(b"late")

        # When
        os.kill(pid, signal.SIGTERM)
        process.join(timeout=1.5)

        # Then
        assert process.exitcode == 0
        manifest = PublicationManifest.model_validate_json(status_path.read_bytes())
        assert manifest.publication_state == PublicationState.FAILED
        assert manifest.diagnostics == ("final_flush_timeout",)
        assert manifest.objects == ()
        assert marker_count_path.read_text(encoding="utf-8") == "0"
    finally:
        if process.is_alive():
            process.kill()
            process.join(timeout=2)
