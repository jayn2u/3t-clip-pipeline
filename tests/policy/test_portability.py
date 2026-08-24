import ast
from pathlib import Path
from typing import override

import pytest

from three_t_clip_pipeline.policy.portability import (
    PortabilityPolicyError,
    load_classification,
    require_exhaustive_classification,
)

REPOSITORY_ROOT = Path(__file__).parents[2]
REFERENCE_TEST_ROOT = Path("/mnt/data/lab_clip/pipeline/tests")
CLASSIFICATION_PATH = REPOSITORY_ROOT / "docs/migration/labclip-test-classification.yaml"
TARGET_SOURCE_ROOT = REPOSITORY_ROOT / "src/three_t_clip_pipeline"
FORBIDDEN_IMPORT_ROOTS = {"lab_clip", "open_clip", "pipeline", "torch", "train", "wandb"}


class ImportRootCollector(ast.NodeVisitor):
    roots: set[str]

    def __init__(self) -> None:
        self.roots = set()

    @override
    def visit_Import(self, node: ast.Import) -> None:
        self.roots.update(alias.name.partition(".")[0] for alias in node.names)

    @override
    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module is not None:
            self.roots.add(node.module.partition(".")[0])


def discovered_reference_tests() -> set[str]:
    return {path.name for path in REFERENCE_TEST_ROOT.glob("test_*.py") if path.is_file()}


def imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    collector = ImportRootCollector()
    collector.visit(tree)
    return collector.roots


def test_every_reference_test_is_classified_exactly_once() -> None:
    ledger = load_classification(CLASSIFICATION_PATH)
    discovered = discovered_reference_tests()
    require_exhaustive_classification(ledger, discovered)
    assert ledger.reference_root == str(REFERENCE_TEST_ROOT)
    assert len(ledger.tests) == len(discovered)
    assert {entry.classification for entry in ledger.tests} == {
        "labclip-only",
        "portable-invariant",
    }
    assert all(entry.provenance.startswith("pipeline/tests/") for entry in ledger.tests)


def test_unclassified_reference_rejected() -> None:
    ledger = load_classification(CLASSIFICATION_PATH)
    discovered = discovered_reference_tests() | {"test_new_reference.py"}
    with pytest.raises(PortabilityPolicyError) as captured:
        require_exhaustive_classification(ledger, discovered)
    assert captured.value.code == "unclassified_reference_test"


def test_stale_reference_classification_rejected() -> None:
    ledger = load_classification(CLASSIFICATION_PATH)
    discovered = discovered_reference_tests() - {ledger.tests[0].path}
    with pytest.raises(PortabilityPolicyError) as captured:
        require_exhaustive_classification(ledger, discovered)
    assert captured.value.code == "stale_reference_test_classification"


def test_target_source_has_no_reference_or_research_imports() -> None:
    violations: list[str] = []
    for path in sorted(TARGET_SOURCE_ROOT.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        forbidden_roots = imported_roots(path) & FORBIDDEN_IMPORT_ROOTS
        if "/mnt/data/lab_clip" in source or forbidden_roots:
            violations.append(f"{path.relative_to(REPOSITORY_ROOT)}:{sorted(forbidden_roots)}")
    assert violations == []
