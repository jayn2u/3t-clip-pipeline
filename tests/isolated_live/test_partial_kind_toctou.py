from __future__ import annotations

import os
import select
import signal
import subprocess
from pathlib import Path

from tests.isolated_live.test_partial_kind_interruptions import SCRIPT, build_fake_environment


def test_replacement_between_partial_capture_and_cleanup_is_never_deleted(tmp_path: Path) -> None:
    # Given: partial Kind resources are captured, then the node identity is atomically replaced.
    env, state = build_fake_environment(tmp_path, scenario="cooperative", replace_node=True)
    stdout_path = tmp_path / "stdout"
    stderr_path = tmp_path / "stderr"
    evidence = tmp_path / "must-not-publish.json"
    with (
        stdout_path.open("w", encoding="utf-8") as stdout_stream,
        stderr_path.open("w", encoding="utf-8") as stderr_stream,
    ):
        process = subprocess.Popen(
            [
                str(SCRIPT),
                "--create",
                "--registry",
                "127.0.0.1:5001",
                "--build-push-local",
                "--run",
                "--cleanup",
                "--collect",
                str(evidence),
            ],
            cwd=SCRIPT.parents[1],
            env=env,
            stdout=stdout_stream,
            stderr=stderr_stream,
            text=True,
        )
        marker = state / "kind-pid"
        for _ in range(100):
            if marker.exists():
                break
            _ = select.select([], [], [], 0.05)
        assert marker.exists(), "fake Kind never reached its preparation boundary"

        # When: TERM begins capture and cleanup across the replacement race.
        os.kill(process.pid, signal.SIGTERM)
        _ = process.wait(timeout=8)

    # Then: cleanup fails closed and never issues deletion for the replacement identity.
    stderr = stderr_path.read_text(encoding="utf-8")
    deletions = (
        (state / "deletions").read_text(encoding="utf-8") if (state / "deletions").exists() else ""
    )
    assert process.returncode == 130
    assert "ISOLATED_LIVE_REJECTED cleanup_ownership_mismatch" in stderr
    assert (state / "node").read_text(encoding="utf-8") == "replacement-node-id"
    assert "replacement-node-id" not in deletions
    assert not evidence.exists()
