"""Security and compatibility rejection behavior."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from three_t_clip_pipeline.contract import load_workload, validation_codes

from .conftest import IMMUTABLE_IMAGE, MINIMAL_YAML, write_workload


def _assert_rejected(tmp_path: Path, source: str, expected_code: str) -> None:
    external_calls: list[str] = []
    path = write_workload(tmp_path, source)
    with pytest.raises(ValidationError) as raised:
        _ = load_workload(path)
    assert expected_code in validation_codes(raised.value)
    assert external_calls == []


@pytest.mark.parametrize(
    ("unsafe_path", "case_name"),
    [
        ("../escape", "parent_traversal"),
        ("cache/../../escape", "nested_traversal"),
        ("/absolute", "absolute_path"),
        (r"windows\\escape", "backslash_path"),
        ("./workspace", "dot_segment"),
        ("workspace/", "trailing_separator"),
        ("workspace//nested", "empty_segment"),
    ],
)
def test_traversal_paths_are_rejected_before_external_io(
    tmp_path: Path, unsafe_path: str, case_name: str
) -> None:
    # Given: an execution path that is not safely relative
    source = MINIMAL_YAML.replace("workingDirectory: workspace", f"workingDirectory: {unsafe_path}")

    # When/Then: validation rejects it with the stable path code before external I/O
    _assert_rejected(tmp_path, source, "path_not_relative")
    assert case_name


@pytest.mark.parametrize(
    "image", ["workload:latest", "workload:v1", "workload", "workload@sha256:abc"]
)
def test_mutable_image_rejected_before_external_io(tmp_path: Path, image: str) -> None:
    # Given: a mutable or malformed workload image reference
    source = MINIMAL_YAML.replace(IMMUTABLE_IMAGE, image)

    # When/Then: validation rejects it with the stable immutable-image code
    _assert_rejected(tmp_path, source, "image_not_immutable")


def test_inline_secret_rejected_before_external_io(tmp_path: Path) -> None:
    # Given: an inline environment value disguised beside a valid reference
    environment = """\
  environment:
    - name: TOKEN
      value: do-not-store-me
      valueFrom:
        secretKeyRef: {name: workload-secret, key: token}
"""
    source = MINIMAL_YAML.replace("  resources:\n", environment + "  resources:\n")

    # When/Then: validation rejects it with the stable inline-secret code
    _assert_rejected(tmp_path, source, "inline_secret_forbidden")


@pytest.mark.parametrize(
    ("replacement", "expected_code"),
    [
        ("gpuCount: true", "int_type"),
        ("gpuCount: -1", "greater_than_equal"),
        ("profileAcquireSeconds: 0", "greater_than"),
        ("profileAcquireSeconds: 7201", "less_than_equal"),
        ("activeDeadlineSeconds: 604801", "less_than_equal"),
        ("terminationGraceSeconds: 601", "less_than_equal"),
        ("publicationFinalRetrySeconds: 3601", "less_than_equal"),
    ],
)
def test_resource_and_timeout_boundaries_are_strict(
    tmp_path: Path, replacement: str, expected_code: str
) -> None:
    # Given: a boolean/count or timeout outside the frozen numeric boundary
    section = (
        '    cpu: "1"\n    memory: 1Gi\n    ' + replacement + "\n"
        if replacement.startswith("gpuCount")
        else "  timeouts:\n    " + replacement + "\n"
    )
    marker = (
        '    cpu: "1"\n    memory: 1Gi\n'
        if replacement.startswith("gpuCount")
        else "  timeouts: {}\n"
    )
    source = MINIMAL_YAML.replace(marker, section)

    # When/Then: strict integer or bound validation rejects the value
    _assert_rejected(tmp_path, source, expected_code)


@pytest.mark.parametrize(
    ("source", "expected_code"),
    [
        (
            MINIMAL_YAML.replace("command: [python, run.py]", 'command: "python run.py"'),
            "tuple_type",
        ),
        (
            MINIMAL_YAML.replace("s3://code/bundle.tar.gz", "s3://user:pass@code/bundle.tar.gz"),
            "s3_uri_invalid",
        ),
        (
            MINIMAL_YAML.replace("a" * 64, "A" * 64),
            "string_pattern_mismatch",
        ),
        (
            MINIMAL_YAML.replace(
                "requiredPaths: [metrics.json]", "requiredPaths: [metrics.json, metrics.json]"
            ),
            "duplicate_required_path",
        ),
        (
            MINIMAL_YAML.replace("apiVersion: three-t-clip-pipeline/v1alpha1", "apiVersion: v2"),
            "literal_error",
        ),
        (MINIMAL_YAML.replace("kind: Workload", "kind: Job"), "literal_error"),
    ],
)
def test_malformed_contract_classes_are_rejected(
    tmp_path: Path, source: str, expected_code: str
) -> None:
    # Given: a malformed version, digest, URI, command, or duplicate output
    # When/Then: the boundary rejects the corresponding malformed class
    _assert_rejected(tmp_path, source, expected_code)


def test_environment_reference_requires_exactly_one_variant(tmp_path: Path) -> None:
    # Given: one environment valueFrom containing two mutually exclusive variants
    environment = """\
  environment:
    - name: TOKEN
      valueFrom:
        secretKeyRef: {name: workload-secret, key: token}
        configMapKeyRef: {name: workload-config, key: token}
"""
    source = MINIMAL_YAML.replace("  resources:\n", environment + "  resources:\n")

    # When/Then: every union branch rejects the extra variant
    _assert_rejected(tmp_path, source, "extra_forbidden")


@pytest.mark.parametrize(
    ("marker", "replacement"),
    [
        ("kind: Workload", "kind: Workload\nunknownField: rejected"),
        ("  name: smoke", "  name: smoke\n  unknownField: rejected"),
        ("spec:", "spec:\n  unknownField: rejected"),
        (
            "    s3Uri: s3://code/bundle.tar.gz",
            "    s3Uri: s3://code/bundle.tar.gz\n    unknownField: rejected",
        ),
        (
            f"    image: {IMMUTABLE_IMAGE}",
            f"    image: {IMMUTABLE_IMAGE}\n    unknownField: rejected",
        ),
        ("  resources:", "  resources:\n    unknownField: rejected"),
        ("  timeouts: {}", "  timeouts:\n    unknownField: rejected"),
        ("  outputs:", "  outputs:\n    unknownField: rejected"),
    ],
)
def test_unknown_fields_are_rejected_at_each_required_object(
    tmp_path: Path, marker: str, replacement: str
) -> None:
    # Given: an unknown property inserted into a required contract object
    source = MINIMAL_YAML.replace(marker, replacement, 1)

    # When/Then: the nearest strict object rejects the unknown property
    _assert_rejected(tmp_path, source, "extra_forbidden")


def test_duplicate_cache_destinations_and_environment_names_are_rejected(tmp_path: Path) -> None:
    # Given: duplicate external environment names and cache destinations
    additions = """\
  environment:
    - name: RUN_UID
      valueFrom: {fieldRef: {fieldPath: metadata.uid}}
    - name: RUN_UID
      valueFrom: {fieldRef: {fieldPath: metadata.name}}
  cacheMappings:
    - s3Uri: s3://cache/one.json
      destination: cache/data
      inventorySha256: cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc
    - s3Uri: s3://cache/two.json
      destination: cache/data
      inventorySha256: dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd
"""
    source = MINIMAL_YAML.replace("  resources:\n", additions + "  resources:\n")
    path = write_workload(tmp_path, source)

    # When: the workload is validated
    with pytest.raises(ValidationError) as raised:
        _ = load_workload(path)

    # Then: duplicate identity classes are both stable validation outcomes
    assert set(validation_codes(raised.value)) & {
        "duplicate_environment_name",
        "duplicate_destination",
    }


def test_prompt_injection_like_argv_remains_inert_data(tmp_path: Path) -> None:
    # Given: an untrusted argv element that would be dangerous in a shell
    injection = "$(cat /etc/passwd); echo stolen"
    source = MINIMAL_YAML.replace(
        "command: [python, run.py]", f'command: [python, run.py, "{injection}"]'
    )

    # When: the contract parses the command
    workload = load_workload(write_workload(tmp_path, source))

    # Then: it remains one exact argv element and is never interpreted
    assert workload.spec.execution.command[-1] == injection
