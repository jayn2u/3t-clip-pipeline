import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from three_t_clip_pipeline.contract import load_workload
from three_t_clip_pipeline.render import SchemaLockError, validate_schema_lock

ROOT = Path(__file__).resolve().parents[2]


def test_unknown_api_is_rejected_locally(tmp_path: Path) -> None:
    source = (ROOT / "examples/workload-minimal.yaml").read_text()
    workload_path = tmp_path / "unknown-api.yaml"
    _ = workload_path.write_text(source.replace("three-t-clip-pipeline/v1alpha1", "unknown/v9"))

    with pytest.raises(ValidationError, match="literal_error"):
        _ = load_workload(workload_path)


def test_mutable_image_is_rejected_locally() -> None:
    with pytest.raises(ValidationError, match="image must use an immutable sha256 digest"):
        _ = load_workload(ROOT / "tests/contract/fixtures/invalid-mutable-image.yaml")


def test_missing_schema_is_rejected_locally(tmp_path: Path) -> None:
    _ = shutil.copytree(ROOT / "schemas", tmp_path / "schemas")
    _ = (tmp_path / "schemas/argo/v4.0.7/workflow-v1alpha1.json").unlink()

    with pytest.raises(SchemaLockError, match="schema_missing"):
        validate_schema_lock(tmp_path)


def test_schema_hash_drift_is_rejected_locally(tmp_path: Path) -> None:
    _ = shutil.copytree(ROOT / "schemas", tmp_path / "schemas")
    schema = tmp_path / "schemas/argo/v4.0.7/workflow-v1alpha1.json"
    _ = schema.write_bytes(schema.read_bytes() + b"\n")

    with pytest.raises(SchemaLockError, match="schema_hash_drift"):
        validate_schema_lock(tmp_path)
