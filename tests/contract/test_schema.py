"""Generated JSON Schema compatibility checks."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import jsonschema
from pydantic import JsonValue, TypeAdapter

from three_t_clip_pipeline.contract import canonical_json_bytes, load_workload
from three_t_clip_pipeline.contract.generate_schema import SCHEMA_DIALECT, schema_bytes

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPOSITORY_ROOT / "schemas/workload-v1alpha1.schema.json"
_SCHEMA_ADAPTER: TypeAdapter[dict[str, JsonValue]] = TypeAdapter(dict[str, JsonValue])


def _assert_objects_are_closed(node: JsonValue) -> None:
    if isinstance(node, dict):
        if node.get("type") == "object":
            assert node.get("additionalProperties") is False
        for value in node.values():
            _assert_objects_are_closed(value)
    if isinstance(node, list):
        for item in node:
            _assert_objects_are_closed(item)


def test_checked_in_schema_is_draft_2020_12_and_closes_every_object() -> None:
    # Given: the checked-in schema generated from the strict models
    schema = _SCHEMA_ADAPTER.validate_json(SCHEMA_PATH.read_bytes())

    # When: its dialect and complete object graph are inspected
    jsonschema.Draft202012Validator.check_schema(schema)

    # Then: the dialect is frozen and every object rejects additional properties
    assert schema["$schema"] == SCHEMA_DIALECT
    _assert_objects_are_closed(schema)


def test_schema_accepts_canonical_example_and_rejects_unknown_fields(tmp_path: Path) -> None:
    # Given: canonical JSON from the checked-in example and the generated schema
    workload = load_workload(REPOSITORY_ROOT / "examples/workload-minimal.yaml")
    canonical_path = tmp_path / "canonical.json"
    invalid_path = tmp_path / "invalid.json"
    canonical = canonical_json_bytes(workload)
    _ = canonical_path.write_bytes(canonical)
    _ = invalid_path.write_bytes(canonical.removesuffix(b"}\n") + b',"unknownField":"rejected"}\n')

    # When: both documents are checked through the installed JSON Schema CLI
    valid = subprocess.run(["jsonschema", "-i", str(canonical_path), str(SCHEMA_PATH)], check=False)
    invalid = subprocess.run(["jsonschema", "-i", str(invalid_path), str(SCHEMA_PATH)], check=False)

    # Then: canonical data validates and a root extension is rejected
    assert valid.returncode == 0
    assert invalid.returncode != 0


def test_schema_generation_is_byte_identical_and_check_mode_succeeds(tmp_path: Path) -> None:
    # Given: two scratch destinations and the checked-in schema
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    command = [sys.executable, "-m", "three_t_clip_pipeline.contract.generate_schema"]

    # When: generation is repeated through the real module entry point
    first_result = subprocess.run([*command, str(first)], check=False, cwd=REPOSITORY_ROOT)
    second_result = subprocess.run([*command, str(second)], check=False, cwd=REPOSITORY_ROOT)
    check_result = subprocess.run(
        [*command, "--check", str(SCHEMA_PATH)], check=False, cwd=REPOSITORY_ROOT
    )

    # Then: both outputs and the checked-in artifact are exactly identical
    assert first_result.returncode == 0
    assert second_result.returncode == 0
    assert check_result.returncode == 0
    assert first.read_bytes() == second.read_bytes() == schema_bytes() == SCHEMA_PATH.read_bytes()
