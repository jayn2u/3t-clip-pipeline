"""Canonical workload bytes and identity."""

from __future__ import annotations

import hashlib
from pathlib import Path

from three_t_clip_pipeline.contract import (
    Workload,
    canonical_json_bytes,
    load_workload,
    workload_digest,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_canonical_round_trip_is_byte_identical_and_digest_stable() -> None:
    # Given: a validated minimal workload
    workload = load_workload(REPOSITORY_ROOT / "examples/workload-minimal.yaml")

    # When: canonical bytes are parsed and serialized repeatedly
    first = canonical_json_bytes(workload)
    reparsed = Workload.model_validate_json(first)
    second = canonical_json_bytes(reparsed)

    # Then: UTF-8 bytes, defaults, ordering, newline, and SHA-256 identity are stable
    assert first == second
    assert first.endswith(b"\n")
    assert not first.endswith(b"\n\n")
    assert first.decode("utf-8").startswith('{"apiVersion"')
    assert b'"gpuCount":0' in first
    assert workload_digest(workload) == hashlib.sha256(first).hexdigest()


def test_minimal_canonical_compatibility_snapshot_is_unchanged() -> None:
    # Given: the frozen minimal example and its v1alpha1 compatibility fixture
    workload = load_workload(REPOSITORY_ROOT / "examples/workload-minimal.yaml")
    fixture = REPOSITORY_ROOT / "tests/contract/fixtures/workload-minimal.canonical.json"

    # When: the current source of truth serializes the example
    current = canonical_json_bytes(workload)

    # Then: it remains byte-identical to the frozen compatibility surface
    assert current == fixture.read_bytes()


def test_unicode_is_serialized_as_utf8_instead_of_ascii_escapes(tmp_path: Path) -> None:
    # Given: an inert Unicode argv element
    source = (REPOSITORY_ROOT / "examples/workload-minimal.yaml").read_text(encoding="utf-8")
    source = source.replace("      - run.py\n", "      - run.py\n      - 한글\n")
    path = tmp_path / "unicode.yaml"
    _ = path.write_text(source, encoding="utf-8")

    # When: the workload is serialized canonically
    canonical = canonical_json_bytes(load_workload(path))

    # Then: the original UTF-8 payload is retained
    assert "한글".encode() in canonical
    assert b"\\u" not in canonical
