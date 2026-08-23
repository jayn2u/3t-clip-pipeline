"""Strict workload model behavior."""

from __future__ import annotations

from pathlib import Path

from three_t_clip_pipeline.contract import load_workload

from .conftest import MINIMAL_YAML, write_workload

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_minimal_example_materializes_frozen_defaults() -> None:
    # Given: the checked-in minimal workload example
    path = REPOSITORY_ROOT / "examples/workload-minimal.yaml"

    # When: it crosses the typed workload boundary
    workload = load_workload(path)

    # Then: its identity and every frozen default are materialized
    assert workload.api_version == "three-t-clip-pipeline/v1alpha1"
    assert workload.kind == "Workload"
    assert workload.spec.environment == ()
    assert workload.spec.cache_mappings == ()
    assert workload.spec.resources.cache_profile == "auto"
    assert workload.spec.resources.gpu_resource == "nvidia.com/gpu"
    assert workload.spec.resources.gpu_count == 0
    assert workload.spec.timeouts.profile_acquire_seconds == 900
    assert workload.spec.timeouts.active_deadline_seconds == 86400
    assert workload.spec.timeouts.termination_grace_seconds == 120
    assert workload.spec.timeouts.publication_final_retry_seconds == 300


def test_all_environment_reference_variants_and_cache_mapping_are_accepted(tmp_path: Path) -> None:
    # Given: external-only environment references and one verified cache mapping
    additions = """\
  environment:
    - name: SECRET_TOKEN
      valueFrom:
        secretKeyRef: {name: workload-secret, key: token}
    - name: CONFIG_MODE
      valueFrom:
        configMapKeyRef: {name: workload-config, key: mode}
    - name: RUN_UID
      valueFrom:
        fieldRef: {fieldPath: metadata.uid}
  cacheMappings:
    - s3Uri: s3://cache/inventory.json
      destination: cache/data
      inventorySha256: cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc
"""
    path = write_workload(
        tmp_path, MINIMAL_YAML.replace("  resources:\n", additions + "  resources:\n")
    )

    # When: the workload is parsed
    workload = load_workload(path)

    # Then: variants remain typed references and cache read-only defaults to true
    assert tuple(item.name for item in workload.spec.environment) == (
        "SECRET_TOKEN",
        "CONFIG_MODE",
        "RUN_UID",
    )
    assert workload.spec.cache_mappings[0].read_only is True
