"""End-to-end tests for the workload contract command."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from three_t_clip_pipeline.contract import Workload

IMMUTABLE_IMAGE = (
    "registry.example/workload@sha256:"
    "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
)


def test_validate_writes_canonical_json_when_workload_is_valid(tmp_path: Path) -> None:
    # Given: a minimal valid workload file and an output destination
    source = tmp_path / "workload.yaml"
    output = tmp_path / "canonical.json"
    _ = source.write_text(
        f"""\
apiVersion: three-t-clip-pipeline/v1alpha1
kind: Workload
metadata:
  name: smoke
spec:
  bundle:
    s3Uri: s3://code/bundle.tar.gz
    sha256: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
  execution:
    image: {IMMUTABLE_IMAGE}
    command: [python, train.py]
    workingDirectory: workspace
  resources:
    cpu: "1"
    memory: 1Gi
  timeouts: {{}}
  outputs:
    bucket: results
    prefix: runs/smoke
    requiredPaths: [metrics.json]
""",
        encoding="utf-8",
    )

    # When: the installed CLI validates through its public command surface
    result = subprocess.run(
        [
            str(Path(sys.executable).with_name("3t-pipeline")),
            "contract",
            "validate",
            str(source),
            "--canonical-json",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: validation succeeds and writes defaults as parseable canonical JSON
    assert result.returncode == 0, result.stderr
    document = Workload.model_validate_json(output.read_text(encoding="utf-8"))
    assert document.api_version == "three-t-clip-pipeline/v1alpha1"
    assert document.spec.resources.gpu_count == 0


def test_validate_reports_stable_code_and_writes_nothing_when_invalid(tmp_path: Path) -> None:
    # Given: a traversal workload and a canonical output destination
    source = tmp_path / "invalid.yaml"
    output = tmp_path / "canonical.json"
    _ = source.write_text(
        f"""\
apiVersion: three-t-clip-pipeline/v1alpha1
kind: Workload
metadata: {{name: invalid}}
spec:
  bundle:
    s3Uri: s3://code/bundle.tar.gz
    sha256: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
  execution:
    image: {IMMUTABLE_IMAGE}
    command: [python, run.py]
    workingDirectory: ../escape
  resources: {{cpu: "1", memory: 1Gi}}
  timeouts: {{}}
  outputs: {{bucket: results, prefix: runs/invalid, requiredPaths: [metrics.json]}}
""",
        encoding="utf-8",
    )

    # When: validation runs through the public CLI
    result = subprocess.run(
        [
            str(Path(sys.executable).with_name("3t-pipeline")),
            "contract",
            "validate",
            str(source),
            "--canonical-json",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: the stable code is observable and no canonical artifact is created
    assert result.returncode == 2
    assert "CONTRACT_INVALID path_not_relative" in result.stderr
    assert not output.exists()
