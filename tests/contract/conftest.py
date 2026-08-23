"""Shared workload contract fixtures."""

from __future__ import annotations

from pathlib import Path

IMMUTABLE_IMAGE = (
    "registry.example/workload@sha256:"
    "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
)
MINIMAL_YAML = f"""\
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
    command: [python, run.py]
    workingDirectory: workspace
  resources:
    cpu: "1"
    memory: 1Gi
  timeouts: {{}}
  outputs:
    bucket: results
    prefix: runs/smoke
    requiredPaths: [metrics.json]
"""


def write_workload(tmp_path: Path, source: str) -> Path:
    """Write one isolated workload input file."""
    path = tmp_path / "workload.yaml"
    _ = path.write_text(source, encoding="utf-8")
    return path
