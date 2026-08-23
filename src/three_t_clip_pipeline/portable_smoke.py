"""Machine-readable command boundary for the source-checkout portable smoke."""

from __future__ import annotations

import json
import os
import sys
from enum import StrEnum
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Protocol, TypeGuard, runtime_checkable

from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter

_JSON_ADAPTER: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)

if TYPE_CHECKING:
    from types import ModuleType


class SmokeCase(StrEnum):
    """Supported deterministic portable-slice scenarios."""

    SUCCESS = "success"
    CORRUPT_OBJECT = "corrupt-object"
    CORRUPT_COMMITTED = "corrupt-committed"
    MISSING_OUTPUT = "missing-output"
    MAIN_NONZERO = "main-nonzero"
    MARKER_RACE = "marker-race"
    MARKER_COLLISION = "marker-collision"
    UPLOAD_TIMEOUT = "upload-timeout"
    CANCEL = "cancel"
    CANCEL_RESUME = "cancel-resume"
    PARTIAL_PUBLICATION = "partial-publication"


class SmokeReport(BaseModel):
    """Stable JSON observables emitted by the portable smoke."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    case: SmokeCase
    schema_validated: bool
    render_validated: bool
    runtime_validated: bool
    workload_sha256: str
    workflow_sha256: str
    run_sha256: str
    marker_sha256: str | None
    required_object_hashes: tuple[str, ...]
    conditional_marker_attempts: int
    marker_count: int
    reader_status: str
    diagnostic_code: str | None
    network_calls: int
    kubernetes_calls: int
    fake_state_verified: bool


def _write_report(report: SmokeReport, path: Path, *, append: bool) -> None:
    payload: JsonValue = _JSON_ADAPTER.validate_json(report.model_dump_json())
    if append and path.exists():
        existing = _JSON_ADAPTER.validate_json(path.read_bytes())
        payload = [*existing, payload] if isinstance(existing, list) else [existing, payload]
    _ = path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")


@runtime_checkable
class _SmokeRunnerModule(Protocol):
    def run_portable_slice(self, case: SmokeCase | str) -> SmokeReport: ...


def _is_smoke_runner(module: ModuleType) -> TypeGuard[_SmokeRunnerModule]:
    return isinstance(module, _SmokeRunnerModule)


def main() -> int:
    """Execute one test-owned scenario and enforce expected-failure semantics."""
    runner = import_module("tests.smoke.test_portable_contract_slice")
    if not _is_smoke_runner(runner):
        return 2
    selected = SmokeCase(os.environ.get("SMOKE_CASE", SmokeCase.SUCCESS))
    report = runner.run_portable_slice(selected)
    output = os.environ.get("SMOKE_JSON") or None
    if output is None:
        _ = sys.stdout.write(report.model_dump_json() + "\n")
    else:
        _write_report(report, Path(output), append=os.environ.get("SMOKE_APPEND") == "1")
    expected_failure = os.environ.get("SMOKE_EXPECT_FAILURE") == "1"
    observed_failure = report.diagnostic_code is not None and report.reader_status == "uncommitted"
    return int(expected_failure != observed_failure)


if __name__ == "__main__":
    raise SystemExit(main())
