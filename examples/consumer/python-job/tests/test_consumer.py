"""Standalone Python consumer boundary tests."""

# ruff: noqa: INP001, S101, S603

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from pydantic import TypeAdapter

from three_t_clip_pipeline.contract import load_workload
from three_t_clip_pipeline.contract.models import WorkloadSpec

EXAMPLE = Path(__file__).parents[1]


def test_workload_supplies_only_v1alpha1_contract() -> None:
    """Keep consumer settings outside the strict workload contract."""
    # Given / When
    workload = load_workload(EXAMPLE / "workload.yaml")
    application = TypeAdapter(dict[str, str]).validate_json(
        (EXAMPLE / "app-config.json").read_bytes()
    )

    # Then
    assert workload.api_version == "three-t-clip-pipeline/v1alpha1"
    assert workload.spec.execution.command == (
        "python",
        "job.py",
        "--config",
        "app-config.json",
    )
    assert set(application) == {"message", "resultSchema"}
    assert not set(application) & set(WorkloadSpec.model_fields)


def test_consumer_process_interprets_its_own_config(tmp_path: Path) -> None:
    """Run the consumer through its real command-line surface."""
    # Given
    environment = os.environ.copy()
    environment["OUTPUT_ROOT"] = str(tmp_path)

    # When
    completed = subprocess.run(
        [sys.executable, str(EXAMPLE / "job.py"), "--config", str(EXAMPLE / "app-config.json")],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    # Then
    assert completed.returncode == 0
    assert completed.stdout == completed.stderr == ""
    assert json.loads((tmp_path / "result.json").read_text(encoding="utf-8")) == {
        "message": "portable consumer output",
        "schema": "python-job-result/v1",
    }
    assert (tmp_path / "provenance.json").is_file()
