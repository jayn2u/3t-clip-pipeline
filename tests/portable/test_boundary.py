"""Standalone consumer and reference-import boundaries."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import ClassVar

import pytest
from pydantic import BaseModel, ConfigDict, Field

from three_t_clip_pipeline.contract import canonical_json_bytes
from three_t_clip_pipeline.contract.models import Workload
from three_t_clip_pipeline.policy import portability
from three_t_clip_pipeline.render import render_workflow_bytes

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests/fixtures/portable"
TARGET_ROOTS = (
    ROOT / "src",
    ROOT / "schemas",
    ROOT / "examples",
    ROOT / "tests/portable",
)
FORBIDDEN_IMPORT_ROOTS = frozenset(
    {"lab" + "_clip", "open" + "_clip", "pipeline", "torch", "train", "wand" + "b"}
)
_YAML_TO_JSON = "import json,sys,yaml; json.dump(yaml.safe_load(sys.stdin.read()),sys.stdout)"


class _RenderedContainer(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True, strict=True)

    command: tuple[str, ...]


class _RenderedTemplate(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True, strict=True)

    name: str
    container: _RenderedContainer | None = None


class _RenderedSpec(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True, strict=True)

    on_exit: str = Field(alias="onExit")
    templates: tuple[_RenderedTemplate, ...]


class _RenderedWorkflow(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True, strict=True)

    spec: _RenderedSpec


def test_research_field_consumer_semantics_forbidden() -> None:
    # Given
    workload_path = FIXTURES / "invalid-consumer-field.yaml"

    # When / Then
    with pytest.raises(portability.ConsumerBoundaryError) as captured:
        _ = portability.load_consumer_workload(workload_path)
    assert captured.value.code == "consumer_semantics_forbidden"


def test_LABCLIP_import_reference_import_forbidden() -> None:  # noqa: N802
    # Given
    fixture = FIXTURES / "injected-reference-import.txt"
    # When / Then
    with pytest.raises(portability.ConsumerBoundaryError) as captured:
        _ = portability.validate_consumer_source(fixture)
    assert captured.value.code == "reference_import_forbidden"


def test_prompt_injection_fixture_round_trips_as_inert_argv_data(tmp_path: Path) -> None:
    # Given
    sentinel = tmp_path / "executed"
    hostile = (
        (FIXTURES / "prompt-injection.txt").read_text(encoding="utf-8").format(sentinel=sentinel)
    )
    source = (FIXTURES / "workload.yaml").read_text(encoding="utf-8")
    candidate = tmp_path / "hostile-command.yaml"
    command = f"    command: [python, job.py, --payload, {json.dumps(hostile)}]\n"
    _ = candidate.write_text(
        source.replace("    command: [python, job.py]\n", command, 1), encoding="utf-8"
    )

    # When
    workload = portability.load_consumer_workload(candidate)
    canonical = canonical_json_bytes(workload)
    canonical_workload = Workload.model_validate_json(canonical)
    rendered = render_workflow_bytes(canonical_workload)
    parsed = subprocess.run(
        [sys.executable, "-c", _YAML_TO_JSON],
        input=rendered.decode(),
        check=True,
        capture_output=True,
        text=True,
    )
    document = _RenderedWorkflow.model_validate_json(parsed.stdout)
    workload_template = next(
        template for template in document.spec.templates if template.name == "workload"
    )

    # Then
    assert canonical_workload.spec.execution.command == ("python", "job.py", "--payload", hostile)
    assert workload_template.container is not None
    assert workload_template.container.command == canonical_workload.spec.execution.command
    assert document.spec.on_exit == "commit-results"
    assert not sentinel.exists()


def test_target_import_graph_has_no_reference_or_consumer_packages() -> None:
    # Given
    violations: dict[str, str] = {}

    # When
    for target_root in TARGET_ROOTS:
        for path in target_root.rglob("*.py"):
            try:
                roots = portability.validate_consumer_source(path)
            except portability.ConsumerBoundaryError as error:
                violations[str(path.relative_to(ROOT))] = error.detail
                continue
            if set(roots) & FORBIDDEN_IMPORT_ROOTS:
                violations[str(path.relative_to(ROOT))] = ",".join(roots)

    # Then
    assert violations == {}
