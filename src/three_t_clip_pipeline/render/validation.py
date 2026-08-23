"""Checksum-locked offline schema and kubeconform validation."""

from __future__ import annotations

import hashlib
import subprocess
from typing import TYPE_CHECKING, ClassVar, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, ValidationError

from three_t_clip_pipeline.render.errors import ClientValidationError, SchemaLockError

_JSON_VALUE_ADAPTER: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)

if TYPE_CHECKING:
    from pathlib import Path


class _LockModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True, strict=True)


class _Source(_LockModel):
    repository: str
    commit: str
    source_path: str = Field(alias="sourcePath")


class _Sources(_LockModel):
    argo: _Source
    kubernetes: _Source


class _File(_LockModel):
    path: str
    sha256: str


class _SchemaLock(_LockModel):
    version: Literal[1]
    sources: _Sources
    files: tuple[_File, ...] = Field(strict=False)


def validate_schema_lock(root: Path) -> None:
    """Require every packaged schema to match the frozen local lock."""
    lock_path = root / "schemas/schema-sources.lock.yaml"
    try:
        raw = _JSON_VALUE_ADAPTER.validate_python(yaml.safe_load(lock_path.read_text()))
        lock = _SchemaLock.model_validate(raw)
    except (OSError, yaml.YAMLError, ValidationError) as error:
        raise SchemaLockError(code="schema_lock_invalid", path=lock_path) from error
    expected_sources = (
        (
            lock.sources.argo,
            "https://github.com/argoproj/argo-workflows",
            "9aeb47ce10339f4a14819335c6a00027353ba0df",
            "manifests/base/crds/full/argoproj.io_workflows.yaml",
        ),
        (
            lock.sources.kubernetes,
            "https://github.com/yannh/kubernetes-json-schema",
            "5a69f8365c9d3ed7de997f5365e22481cf775fa2",
            "v1.36.2-standalone-strict",
        ),
    )
    if any(
        (source.repository, source.commit, source.source_path) != (repository, commit, source_path)
        for source, repository, commit, source_path in expected_sources
    ):
        raise SchemaLockError(code="schema_source_drift", path=lock_path)
    locked_paths = {root / item.path for item in lock.files}
    packaged_paths = {
        *sorted((root / "schemas/argo").rglob("*.json")),
        *sorted((root / "schemas/kubernetes").rglob("*.json")),
    }
    missing_paths = locked_paths - packaged_paths
    if missing_paths:
        raise SchemaLockError(code="schema_missing", path=min(missing_paths))
    if packaged_paths - locked_paths:
        raise SchemaLockError(code="schema_inventory_drift", path=lock_path)
    for item in lock.files:
        schema_path = root / item.path
        try:
            actual = hashlib.sha256(schema_path.read_bytes()).hexdigest()
        except OSError as error:
            raise SchemaLockError(code="schema_missing", path=schema_path) from error
        if actual != item.sha256:
            raise SchemaLockError(code="schema_hash_drift", path=schema_path)


def run_client_validation(manifest: Path, root: Path) -> str:
    """Validate a manifest through only the checksum-locked local wrapper."""
    validate_schema_lock(root)
    wrapper = root / "scripts/validate-manifest-local.sh"
    try:
        completed = subprocess.run(  # noqa: S603
            [str(wrapper), str(manifest)],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as error:
        raise ClientValidationError(
            code="client_validator_unavailable", detail=str(error)
        ) from error
    output = f"{completed.stdout}{completed.stderr}"
    if completed.returncode != 0:
        raise ClientValidationError(code="client_validation_failed", detail=output.strip())
    return output
