"""Typed external reference-test classification boundary."""

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Literal, Self, final, override

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


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
