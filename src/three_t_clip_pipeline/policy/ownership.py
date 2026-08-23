"""Typed resource ownership ledger boundary."""

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Literal, Self, final, override

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)


class ResourceOwnership(BaseModel):
    """A single owner and handoff policy for one deterministic resource identity."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    api_version: str = Field(alias="apiVersion", min_length=1)
    kind: str = Field(min_length=1)
    namespace: str = Field(min_length=1)
    name: str = Field(min_length=1)
    owner: str = Field(min_length=1)
    mechanism: str = Field(min_length=1)
    adoption_policy: Literal[
        "check-only",
        "no-adoption",
        "create-ephemeral-only",
        "documentation-only",
        "encryption-gated",
        "disabled",
        "submit-only",
        "isolated-prefix-only",
    ] = Field(alias="adoptionPolicy")
    backup_requirement: str = Field(alias="backupRequirement", min_length=1)
    rollback_owner: str = Field(alias="rollbackOwner", min_length=1)

    @field_validator("backup_requirement")
    @classmethod
    def reject_retain_as_backup(cls, requirement: str) -> str:
        """Reject Kubernetes retention policy presented as backup evidence."""
        words = requirement.casefold().replace(";", " ").split()
        if "retain" in words:
            raise RetainIsNotBackupError
        return requirement

    @property
    def identity(self) -> tuple[str, str, str, str]:
        """Return the GVK, namespace, and name uniqueness key."""
        return (self.api_version, self.kind, self.namespace, self.name)


class OwnershipLedger(BaseModel):
    """The complete resource ownership boundary."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    api_version: Literal["platform.three-t.dev/v1alpha1"] = Field(alias="apiVersion")
    kind: Literal["ResourceOwnershipLedger"]
    resources: tuple[ResourceOwnership, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_resource_identities(self) -> Self:
        """Reject a resource identity assigned more than once."""
        identities: set[tuple[str, str, str, str]] = set()
        for resource in self.resources:
            if resource.identity in identities:
                raise OwnershipConflictError(identity=resource.identity)
            identities.add(resource.identity)
        return self


@final
@dataclass(frozen=True, slots=True)
class OwnershipPolicyError(Exception):
    """Stable ownership policy failure exposed by the CLI."""

    code: str
    detail: str

    @override
    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"


@final
@dataclass(frozen=True, slots=True)
class OwnershipConflictError(ValueError):
    """Pydantic validator error for a repeated ownership identity."""

    identity: tuple[str, str, str, str]

    @override
    def __str__(self) -> str:
        return f"ownership_conflict: {'/'.join(self.identity)}"


@final
@dataclass(frozen=True, slots=True)
class RetainIsNotBackupError(ValueError):
    """Pydantic validator error for a retention-only backup claim."""

    @override
    def __str__(self) -> str:
        return "retain_is_not_backup"


def parse_ownership(raw_yaml: str) -> OwnershipLedger:
    """Parse untrusted YAML once into a frozen ownership ledger."""
    try:
        return OwnershipLedger.model_validate(yaml.safe_load(raw_yaml))
    except ValidationError as error:
        detail = str(error)
        code = (
            "ownership_conflict" if "ownership_conflict" in detail else "ownership_schema_invalid"
        )
        raise OwnershipPolicyError(code=code, detail=detail) from error
    except yaml.YAMLError as error:
        raise OwnershipPolicyError(code="ownership_schema_invalid", detail=str(error)) from error


def load_ownership(path: Path) -> OwnershipLedger:
    """Load and parse an ownership ledger from disk."""
    return parse_ownership(path.read_text(encoding="utf-8"))
