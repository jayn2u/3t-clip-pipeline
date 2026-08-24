"""Executable portability-ledger coverage checks."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from three_t_clip_pipeline.policy.check_test_map import (
    TestMapError as MapVerificationError,
)
from three_t_clip_pipeline.policy.check_test_map import (
    verify_test_map,
)
from three_t_clip_pipeline.policy.portability import load_classification

ROOT = Path(__file__).parents[2]
REFERENCE_ROOT = Path("/mnt/data") / ("lab" + "_clip")
REFERENCE_NAME = "lab" + "clip"
CLASSIFICATION = ROOT / f"docs/migration/{REFERENCE_NAME}-test-classification.yaml"
TEST_MAP = ROOT / f"docs/migration/{REFERENCE_NAME}-test-map.md"


def test_live_reference_inventory_is_classified_and_portable_rows_are_linked() -> None:
    # Given
    discovered = {path.name for path in (REFERENCE_ROOT / "pipeline/tests").glob("test_*.py")}
    ledger = load_classification(CLASSIFICATION)
    portable = sum(entry.classification == "portable-invariant" for entry in ledger.tests)
    consumer_owned = len(ledger.tests) - portable

    # When
    report = verify_test_map(
        reference_root=REFERENCE_ROOT,
        classification_path=CLASSIFICATION,
        map_path=TEST_MAP,
    )

    # Then
    assert report.discovered == len(discovered) == len(ledger.tests)
    assert {entry.path for entry in ledger.tests} == discovered
    assert report.portable == report.linked_portable == portable
    assert report.consumer_owned == consumer_owned


def test_cli_reports_machine_readable_success_token() -> None:
    # Given / When
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "three_t_clip_pipeline.policy.check_test_map",
            "--reference-root",
            str(REFERENCE_ROOT),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    # Then
    assert completed.returncode == 0
    assert completed.stdout.startswith("test_map_ok ")
    assert completed.stderr == ""


def test_malformed_classification_is_rejected(tmp_path: Path) -> None:
    # Given
    malformed = tmp_path / "classification.yaml"
    _ = malformed.write_text("tests: [not-valid", encoding="utf-8")

    # When / Then
    with pytest.raises(MapVerificationError) as captured:
        _ = verify_test_map(
            reference_root=REFERENCE_ROOT,
            classification_path=malformed,
            map_path=TEST_MAP,
        )
    assert captured.value.code == "classification_schema_invalid"


def test_misleading_map_count_is_rejected(tmp_path: Path) -> None:
    # Given
    misleading = tmp_path / "map.md"
    _ = misleading.write_text(
        TEST_MAP.read_text(encoding="utf-8").replace("portable: 9", "portable: 999", 1),
        encoding="utf-8",
    )

    # When / Then
    with pytest.raises(MapVerificationError) as captured:
        _ = verify_test_map(
            reference_root=REFERENCE_ROOT,
            classification_path=CLASSIFICATION,
            map_path=misleading,
        )
    assert captured.value.code == "test_map_summary_mismatch"


def test_nonexistent_portable_test_node_is_rejected(tmp_path: Path) -> None:
    # Given
    missing_node = tmp_path / "map.md"
    source = TEST_MAP.read_text(encoding="utf-8").replace(
        "test_head_mismatch_blocks_marker`",
        "test_node_does_not_exist`",
        1,
    )
    _ = missing_node.write_text(source, encoding="utf-8")

    # When / Then
    with pytest.raises(MapVerificationError) as captured:
        _ = verify_test_map(
            reference_root=REFERENCE_ROOT,
            classification_path=CLASSIFICATION,
            map_path=missing_node,
        )
    assert captured.value.code == "portable_test_node_missing"
