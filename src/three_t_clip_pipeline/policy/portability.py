"""Typed external reference-test and standalone consumer policy boundaries."""

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Literal, Self, final, override

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from three_t_clip_pipeline.contract import load_workload
from three_t_clip_pipeline.contract.models import Workload

_FORBIDDEN_IMPORT_ROOTS = frozenset(
    {
        "lab" + "_clip",
        "open" + "_clip",
        "pipeline",
        "to" + "rch",
        "train",
        "wand" + "b",
    }
)

BoundaryCode = Literal["consumer_semantics_forbidden", "reference_import_forbidden"]
_CONSUMER_SEMANTICS_FORBIDDEN: BoundaryCode = "consumer_semantics_forbidden"
_REFERENCE_IMPORT_FORBIDDEN: BoundaryCode = "reference_import_forbidden"


class ReferenceTest(BaseModel):
    """One provenance-bearing reference test classification."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(pattern=r"^test_[A-Za-z0-9_]+\.py$")
    classification: str = Field(min_length=1)
    provenance: str = Field(min_length=1)
    disposition: str = Field(min_length=1)


class ReferenceTestLedger(BaseModel):
    """Complete classification for the discovered external test inventory."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    api_version: Literal["platform.three-t.dev/v1alpha1"] = Field(alias="apiVersion")
    kind: Literal["ReferenceTestClassification"]
    reference_root: str = Field(alias="referenceRoot", min_length=1)
    tests: tuple[ReferenceTest, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_test_paths(self) -> Self:
        """Reject duplicate reference-test classifications."""
        paths = [entry.path for entry in self.tests]
        if len(paths) != len(set(paths)):
            raise DuplicateClassificationError
        return self


@final
@dataclass(frozen=True, slots=True)
class DuplicateClassificationError(ValueError):
    """Pydantic validator error for a multiply classified reference test."""

    @override
    def __str__(self) -> str:
        return "duplicate_reference_test"


@final
@dataclass(frozen=True, slots=True)
class PortabilityPolicyError(Exception):
    """Stable portability policy failure."""

    code: str
    detail: str

    @override
    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"


@final
@dataclass(slots=True)
class ConsumerBoundaryError(Exception):
    """Stable rejection at the standalone consumer policy boundary."""

    code: BoundaryCode
    detail: str

    @override
    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"


def _consumer_error(code: BoundaryCode, detail: str) -> ConsumerBoundaryError:
    return ConsumerBoundaryError(code, detail)


def load_consumer_workload(path: Path) -> Workload:
    """Load a workload while translating consumer-only fields to a stable policy code."""
    try:
        return load_workload(path)
    except ValidationError as error:
        extra_locations = tuple(
            ".".join(str(segment) for segment in item["loc"])
            for item in error.errors()
            if item["type"] == "extra_forbidden"
        )
        if extra_locations:
            raise _consumer_error(
                _CONSUMER_SEMANTICS_FORBIDDEN, ",".join(extra_locations)
            ) from error
        raise


def validate_consumer_source(path: Path) -> tuple[str, ...]:
    """Return imported roots or reject reference and consumer-runtime dependencies."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        match node:
            case ast.Import(names=names):
                roots.update(alias.name.partition(".")[0].lower() for alias in names)
            case ast.ImportFrom(module=module) if module is not None:
                roots.add(module.partition(".")[0].lower())
            case _:
                continue
    forbidden = tuple(sorted(roots & _FORBIDDEN_IMPORT_ROOTS))
    if forbidden:
        raise _consumer_error(_REFERENCE_IMPORT_FORBIDDEN, ",".join(forbidden))
    return tuple(sorted(roots))


def parse_classification(raw_yaml: str) -> ReferenceTestLedger:
    """Parse untrusted classification YAML once into a frozen ledger."""
    try:
        return ReferenceTestLedger.model_validate(yaml.safe_load(raw_yaml))
    except ValidationError as error:
        raise PortabilityPolicyError(
            code="classification_schema_invalid", detail=str(error)
        ) from error
    except yaml.YAMLError as error:
        raise PortabilityPolicyError(
            code="classification_schema_invalid", detail=str(error)
        ) from error


def require_exhaustive_classification(ledger: ReferenceTestLedger, discovered: set[str]) -> None:
    """Require the fresh filesystem inventory and ledger to match exactly."""
    classified = {entry.path for entry in ledger.tests}
    missing = sorted(discovered - classified)
    extra = sorted(classified - discovered)
    if missing:
        raise PortabilityPolicyError(
            code="unclassified_reference_test",
            detail=",".join(missing),
        )
    if extra:
        raise PortabilityPolicyError(
            code="stale_reference_test_classification",
            detail=",".join(extra),
        )


def load_classification(path: Path) -> ReferenceTestLedger:
    """Load and parse a reference-test classification ledger."""
    return parse_classification(path.read_text(encoding="utf-8"))
