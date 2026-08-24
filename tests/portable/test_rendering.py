"""Parameterised scheduler and renderer invariants."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from three_t_clip_pipeline.contract import load_workload
from three_t_clip_pipeline.render import render_workflow_bytes

FIXTURE = Path(__file__).parents[1] / "fixtures/portable/workload.yaml"


@dataclass(frozen=True, slots=True)
class RendererCase:
    name: str
    profile: str
    bucket: str
    prefix: str
    gpu_resource: str
    command: tuple[str, ...]


@pytest.mark.parametrize(
    "case",
    [
        RendererCase(
            name="image-job",
            profile="accelerator-a",
            bucket="store-one",
            prefix="runs/a",
            gpu_resource="vendor.example/gpu",
            command=("python", "job.py"),
        ),
        RendererCase(
            name="batch-job",
            profile="accelerator-b",
            bucket="store-two",
            prefix="outputs/b",
            gpu_resource="example.net/device",
            command=("python", "-m", "worker"),
        ),
    ],
)
def test_renderer_preserves_generic_consumer_and_scheduler_contract(
    case: RendererCase,
) -> None:
    # Given
    base = load_workload(FIXTURE)
    resources = base.spec.resources.model_copy(
        update={
            "cache_profile": case.profile,
            "gpu_resource": case.gpu_resource,
            "gpu_count": 2,
        }
    )
    execution = base.spec.execution.model_copy(update={"command": case.command})
    outputs = base.spec.outputs.model_copy(update={"bucket": case.bucket, "prefix": case.prefix})
    workload = base.model_copy(
        update={
            "metadata": base.metadata.model_copy(update={"name": case.name}),
            "spec": base.spec.model_copy(
                update={"resources": resources, "execution": execution, "outputs": outputs}
            ),
        }
    )

    # When
    first = render_workflow_bytes(workload)
    second = render_workflow_bytes(workload)
    rendered = first.decode()

    # Then
    assert first == second
    assert all(f"- {part}\n" in rendered for part in case.command)
    assert f"{case.gpu_resource}: '2'" in rendered
    assert f"name: {case.name}-" in rendered
    assert "onExit: commit-results\n" in rendered


@pytest.mark.parametrize("namespace", ["team-a", "batch-space"])
def test_consumer_namespace_and_labels_are_not_contract_fields(
    namespace: str, tmp_path: Path
) -> None:
    # Given
    source = FIXTURE.read_text(encoding="utf-8")
    candidate = tmp_path / "namespaced.yaml"
    metadata = (
        f"  name: python-job\n  namespace: {namespace}\n"
        "  labels:\n    consumer.example/queue: general\n"
    )
    _ = candidate.write_text(source.replace("  name: python-job\n", metadata, 1), encoding="utf-8")

    # When / Then
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        _ = load_workload(candidate)
