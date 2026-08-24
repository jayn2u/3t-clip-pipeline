"""Verify the read-only reference inventory and target-owned portability map."""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from enum import StrEnum, unique
from pathlib import Path
from typing import Final, override

from three_t_clip_pipeline.policy.portability import (
    PortabilityPolicyError,
    load_classification,
    require_exhaustive_classification,
)

_ROOT: Final = Path(__file__).parents[3]
_REFERENCE_NAME: Final = "lab" + "clip"
_CLASSIFICATION: Final = _ROOT / f"docs/migration/{_REFERENCE_NAME}-test-classification.yaml"
_MAP: Final = _ROOT / f"docs/migration/{_REFERENCE_NAME}-test-map.md"
_CONSUMER_CLASS: Final = "lab" + "clip-only"
_SUMMARY_PATTERN: Final = re.compile(
    r"<!-- test-map:v1 portable: (?P<portable>\d+) consumer-owned: (?P<consumer>\d+) -->"
)
_ROW_START: Final = r"^\| `(?P<reference>test_[A-Za-z0-9_]+\.py)` \| "
_ROW_CLASS: Final = rf"(?P<classification>portable-invariant|{_CONSUMER_CLASS}) \| "
_ROW_END: Final = r"(?P<target>[^|]+) \| (?P<hook>[^|]+) \|$"
_ROW_PATTERN: Final = re.compile(f"{_ROW_START}{_ROW_CLASS}{_ROW_END}")
_CLI_ARGUMENT_COUNT: Final = 2
_COLLECTION_TIMEOUT_SECONDS: Final = 60
_SUMMARY_REQUIRED: Final = "machine summary is required"
_ROWS_UNIQUE: Final = "reference rows must be unique"
_MAP_DIFFERS: Final = "map and ledger differ"
_COUNTS_DIFFER: Final = "declared counts differ from rows"


@unique
class _ErrorCode(StrEnum):
    SUMMARY_MISSING = "test_map_summary_missing"
    DUPLICATE_ROW = "duplicate_test_map_row"
    LINK_INVALID = "portable_test_link_invalid"
    ROOT_MISMATCH = "reference_root_mismatch"
    MAP_UNREADABLE = "test_map_unreadable"
    CLASSIFICATION_MISMATCH = "test_map_classification_mismatch"
    PORTABLE_HOOK_UNEXPECTED = "portable_adapter_hook_unexpected"
    CONSUMER_HOOK_MISSING = "consumer_adapter_hook_missing"
    SUMMARY_MISMATCH = "test_map_summary_mismatch"
    NODE_MISSING = "portable_test_node_missing"
    COLLECTION_TIMEOUT = "portable_test_collection_timeout"


@dataclass(frozen=True, slots=True)
class TestMapReport:
    """Verified classification and target-link counts."""

    discovered: int
    portable: int
    consumer_owned: int
    linked_portable: int


@dataclass(slots=True)
class TestMapError(Exception):
    """Stable test-map verification failure."""

    code: str
    detail: str

    @override
    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"


@dataclass(frozen=True, slots=True)
class _MapRow:
    reference: str
    classification: str
    target: str
    hook: str


def _reference_test_root(reference_root: Path) -> Path:
    direct = reference_root / "pipeline/tests"
    return direct if direct.is_dir() else reference_root


def _error(code: str, detail: str) -> TestMapError:
    return TestMapError(code, detail)


def _parse_map(source: str) -> tuple[int, int, tuple[_MapRow, ...]]:
    summary = _SUMMARY_PATTERN.search(source)
    if summary is None:
        raise _error(_ErrorCode.SUMMARY_MISSING, _SUMMARY_REQUIRED)
    rows = tuple(
        _MapRow(**match.groupdict())
        for line in source.splitlines()
        if (match := _ROW_PATTERN.fullmatch(line)) is not None
    )
    references = tuple(row.reference for row in rows)
    if len(references) != len(set(references)):
        raise _error(_ErrorCode.DUPLICATE_ROW, _ROWS_UNIQUE)
    return int(summary["portable"]), int(summary["consumer"]), rows


def _target_selector(target: str) -> str:
    rendered = target.strip()
    if not rendered.startswith("`") or not rendered.endswith("`"):
        raise _error(_ErrorCode.LINK_INVALID, rendered)
    selector = rendered[1:-1]
    path_text, separator, node = selector.partition("::")
    if separator != "::" or not node:
        raise _error(_ErrorCode.LINK_INVALID, selector)
    path = _ROOT / path_text
    portable_root = _ROOT / "tests/portable"
    example_root = _ROOT / "examples/consumer/python-job/tests"
    if not path.is_file() or not (
        path.is_relative_to(portable_root) or path.is_relative_to(example_root)
    ):
        raise _error(_ErrorCode.LINK_INVALID, path_text)
    return selector


def _collect_target_selectors(rows: tuple[_MapRow, ...]) -> tuple[str, ...]:
    selectors = tuple(_target_selector(row.target) for row in rows)
    command = [sys.executable, "-m", "pytest", "--collect-only", "-q", *selectors]
    try:
        completed = subprocess.run(  # noqa: S603
            command,
            cwd=_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=_COLLECTION_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        raise _error(_ErrorCode.COLLECTION_TIMEOUT, ",".join(selectors)) from error
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or ",".join(selectors)
        raise _error(_ErrorCode.NODE_MISSING, detail)
    return selectors


def verify_test_map(
    *, reference_root: Path, classification_path: Path, map_path: Path
) -> TestMapReport:
    """Verify inventory exhaustiveness, row uniqueness, links, hooks, and counts."""
    test_root = _reference_test_root(reference_root)
    discovered = {path.name for path in test_root.glob("test_*.py") if path.is_file()}
    try:
        ledger = load_classification(classification_path)
        require_exhaustive_classification(ledger, discovered)
    except PortabilityPolicyError as error:
        raise _error(error.code, error.detail) from error
    if ledger.reference_root != str(test_root):
        raise _error(_ErrorCode.ROOT_MISMATCH, ledger.reference_root)
    try:
        source = map_path.read_text(encoding="utf-8")
    except OSError as error:
        raise _error(_ErrorCode.MAP_UNREADABLE, str(error)) from error
    declared_portable, declared_consumer, rows = _parse_map(source)
    ledger_classes = {entry.path: entry.classification for entry in ledger.tests}
    map_classes = {row.reference: row.classification for row in rows}
    if map_classes != ledger_classes:
        raise _error(_ErrorCode.CLASSIFICATION_MISMATCH, _MAP_DIFFERS)
    portable_rows = tuple(row for row in rows if row.classification == "portable-invariant")
    consumer_rows = tuple(row for row in rows if row.classification == _CONSUMER_CLASS)
    linked_portable = _collect_target_selectors(portable_rows)
    for row in portable_rows:
        if row.hook.strip() != "—":
            raise _error(_ErrorCode.PORTABLE_HOOK_UNEXPECTED, row.reference)
    for row in consumer_rows:
        if row.target.strip() != "—" or row.hook.strip() == "—":
            raise _error(_ErrorCode.CONSUMER_HOOK_MISSING, row.reference)
    if (declared_portable, declared_consumer) != (len(portable_rows), len(consumer_rows)):
        raise _error(_ErrorCode.SUMMARY_MISMATCH, _COUNTS_DIFFER)
    return TestMapReport(
        discovered=len(discovered),
        portable=len(portable_rows),
        consumer_owned=len(consumer_rows),
        linked_portable=len(linked_portable),
    )


def main(argv: list[str] | None = None) -> int:
    """Run the test-map verifier without mutating the reference repository."""
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != _CLI_ARGUMENT_COUNT or arguments[0] != "--reference-root":
        _ = sys.stderr.write("usage: check_test_map --reference-root PATH\n")
        return 2
    try:
        report = verify_test_map(
            reference_root=Path(arguments[1]),
            classification_path=_CLASSIFICATION,
            map_path=_MAP,
        )
    except TestMapError as error:
        _ = sys.stderr.write(f"{error}\n")
        return 1
    output = (
        f"test_map_ok reference_tests={report.discovered} portable={report.portable} "
        f"consumer_owned={report.consumer_owned} linked={report.linked_portable}\n"
    )
    _ = sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
