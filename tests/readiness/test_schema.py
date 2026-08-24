from __future__ import annotations

from pathlib import Path

from tests.readiness.fixtures import write_bundle
from three_t_clip_pipeline.readiness.models import JsonDocument, JsonValue, ReadinessBundle

SCHEMA_PATH = Path("schemas/cutover-readiness.schema.json")


def _load_json(path: Path) -> JsonValue:
    return JsonDocument.model_validate_json(path.read_text(encoding="utf-8")).root


def test_checked_schema_matches_generated_boundary_schema() -> None:
    # Given: the checked-in readiness schema and its frozen Pydantic source model.
    checked_in = _load_json(SCHEMA_PATH)

    # When: the source model generates its JSON Schema.
    generated = ReadinessBundle.model_json_schema(by_alias=True)

    # Then: schema drift is rejected exactly.
    assert checked_in == generated


def test_checked_schema_validates_complete_collector_bundle(tmp_path: Path) -> None:
    # Given: a complete bundle and the schema-generating boundary model.
    bundle_text = write_bundle(tmp_path).read_text(encoding="utf-8")

    # When: the public bundle is parsed by that boundary.
    bundle = ReadinessBundle.model_validate_json(bundle_text)

    # Then: every required source record is schema-valid.
    assert len(bundle.sources) == 9
