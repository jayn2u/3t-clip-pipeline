"""Strict v1alpha1 workload boundary models."""

from __future__ import annotations

import re
from typing import Annotated, ClassVar, Literal, LiteralString, NoReturn, Self
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictInt,
    StrictStr,
    StringConstraints,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

API_VERSION = "three-t-clip-pipeline/v1alpha1"
KIND = "Workload"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_IMAGE_PATTERN = re.compile(r"^[^\s:@]+(?:[/:][^\s:@]+)*@sha256:[0-9a-f]{64}$")
_NAME_PATTERN = re.compile(r"^[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?$")
_ENVIRONMENT_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MAX_NAME_LENGTH = 253

NonEmptyString = Annotated[StrictStr, StringConstraints(min_length=1)]
Sha256 = Annotated[StrictStr, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class ContractModel(BaseModel):
    """Shared strict and immutable contract behavior."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        populate_by_name=True,
    )


def _raise_contract_error(code: LiteralString, message: LiteralString) -> NoReturn:
    error = PydanticCustomError(code, message)
    raise error


def _relative_path(value: str, *, allow_dot: bool) -> str:
    segments = value.split("/")
    is_unsafe = (
        not value
        or "\\" in value
        or value.startswith("/")
        or (value != "." and any(segment in {"", ".", ".."} for segment in segments))
        or (value == "." and not allow_dot)
    )
    if is_unsafe:
        _raise_contract_error("path_not_relative", "path must be a safe relative POSIX path")
    return value


def _sha256(value: str) -> str:
    if _SHA256_PATTERN.fullmatch(value) is None:
        _raise_contract_error("sha256_invalid", "value must be a lowercase SHA-256 digest")
    return value


def _s3_object_uri(value: str) -> str:
    parsed = urlsplit(value)
    valid = (
        parsed.scheme == "s3"
        and bool(parsed.netloc)
        and bool(parsed.path.lstrip("/"))
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
    )
    if not valid:
        _raise_contract_error("s3_uri_invalid", "value must be an uncredentialed S3 object URI")
    return value


class Metadata(ContractModel):
    """Workload identity metadata."""

    name: NonEmptyString

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if len(value) > _MAX_NAME_LENGTH or _NAME_PATTERN.fullmatch(value) is None:
            _raise_contract_error("name_invalid", "name must be a portable DNS-style name")
        return value


class Bundle(ContractModel):
    """Immutable consumer bundle reference."""

    s3_uri: NonEmptyString = Field(alias="s3Uri")
    sha256: Sha256

    @field_validator("s3_uri")
    @classmethod
    def _validate_s3_uri(cls, value: str) -> str:
        return _s3_object_uri(value)

    @field_validator("sha256")
    @classmethod
    def _validate_sha256(cls, value: str) -> str:
        return _sha256(value)


class Execution(ContractModel):
    """Consumer image and argv execution boundary."""

    image: NonEmptyString
    command: tuple[NonEmptyString, ...] = Field(min_length=1, strict=False)
    working_directory: NonEmptyString = Field(alias="workingDirectory")

    @field_validator("image")
    @classmethod
    def _validate_image(cls, value: str) -> str:
        if _IMAGE_PATTERN.fullmatch(value) is None:
            _raise_contract_error(
                "image_not_immutable", "image must use an immutable sha256 digest"
            )
        return value

    @field_validator("working_directory")
    @classmethod
    def _validate_working_directory(cls, value: str) -> str:
        return _relative_path(value, allow_dot=True)


class KeyReference(ContractModel):
    """Named key in a Kubernetes Secret or ConfigMap."""

    name: NonEmptyString
    key: NonEmptyString


class FieldReference(ContractModel):
    """Downward API field reference."""

    field_path: NonEmptyString = Field(alias="fieldPath")


class SecretValueFrom(ContractModel):
    """Secret key reference variant."""

    secret_key_ref: KeyReference = Field(alias="secretKeyRef")


class ConfigMapValueFrom(ContractModel):
    """ConfigMap key reference variant."""

    config_map_key_ref: KeyReference = Field(alias="configMapKeyRef")


class FieldValueFrom(ContractModel):
    """Downward API reference variant."""

    field_ref: FieldReference = Field(alias="fieldRef")


ValueFrom = SecretValueFrom | ConfigMapValueFrom | FieldValueFrom


class EnvironmentVariable(ContractModel):
    """Environment variable populated only from an external reference."""

    name: NonEmptyString
    value_from: ValueFrom = Field(alias="valueFrom")

    @model_validator(mode="before")
    @classmethod
    def _reject_inline_secret(cls, data: JsonValue) -> JsonValue:
        if isinstance(data, dict) and "value" in data:
            _raise_contract_error(
                "inline_secret_forbidden", "inline environment values are forbidden"
            )
        return data

    @field_validator("name")
    @classmethod
    def _validate_environment_name(cls, value: str) -> str:
        if _ENVIRONMENT_NAME_PATTERN.fullmatch(value) is None:
            _raise_contract_error(
                "environment_name_invalid", "environment variable name is invalid"
            )
        return value


class CacheMapping(ContractModel):
    """Verified generic cache-object mapping."""

    s3_uri: NonEmptyString = Field(alias="s3Uri")
    destination: NonEmptyString
    inventory_sha256: Sha256 = Field(alias="inventorySha256")
    read_only: bool = Field(default=True, alias="readOnly", strict=True)

    @field_validator("s3_uri")
    @classmethod
    def _validate_s3_uri(cls, value: str) -> str:
        return _s3_object_uri(value)

    @field_validator("inventory_sha256")
    @classmethod
    def _validate_inventory_sha256(cls, value: str) -> str:
        return _sha256(value)

    @field_validator("destination")
    @classmethod
    def _validate_destination(cls, value: str) -> str:
        return _relative_path(value, allow_dot=False)


class Resources(ContractModel):
    """Generic scheduler resource requests."""

    cache_profile: NonEmptyString = Field(default="auto", alias="cacheProfile")
    gpu_resource: NonEmptyString = Field(default="nvidia.com/gpu", alias="gpuResource")
    gpu_count: Annotated[StrictInt, Field(ge=0)] = Field(default=0, alias="gpuCount")
    cpu: NonEmptyString
    memory: NonEmptyString


class Timeouts(ContractModel):
    """Bounded lifecycle timeout values in seconds."""

    profile_acquire_seconds: Annotated[StrictInt, Field(gt=0, le=7200)] = Field(
        default=900, alias="profileAcquireSeconds"
    )
    active_deadline_seconds: Annotated[StrictInt, Field(gt=0, le=604800)] = Field(
        default=86400, alias="activeDeadlineSeconds"
    )
    termination_grace_seconds: Annotated[StrictInt, Field(gt=0, le=600)] = Field(
        default=120, alias="terminationGraceSeconds"
    )
    publication_final_retry_seconds: Annotated[StrictInt, Field(gt=0, le=3600)] = Field(
        default=300, alias="publicationFinalRetrySeconds"
    )


class Outputs(ContractModel):
    """Publication destination and required result paths."""

    bucket: NonEmptyString
    prefix: NonEmptyString
    required_paths: tuple[NonEmptyString, ...] = Field(
        alias="requiredPaths", min_length=1, strict=False
    )

    @field_validator("prefix")
    @classmethod
    def _validate_prefix(cls, value: str) -> str:
        return _relative_path(value, allow_dot=False)

    @field_validator("required_paths")
    @classmethod
    def _validate_required_paths(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        checked = tuple(_relative_path(value, allow_dot=False) for value in values)
        if len(checked) != len(set(checked)):
            _raise_contract_error("duplicate_required_path", "required paths must be unique")
        return checked


class WorkloadSpec(ContractModel):
    """Portable workload execution and publication specification."""

    bundle: Bundle
    execution: Execution
    environment: tuple[EnvironmentVariable, ...] = Field(default=(), strict=False)
    cache_mappings: tuple[CacheMapping, ...] = Field(
        default=(), alias="cacheMappings", strict=False
    )
    resources: Resources
    timeouts: Timeouts
    outputs: Outputs

    @model_validator(mode="after")
    def _reject_duplicates(self) -> Self:
        environment_names = tuple(item.name for item in self.environment)
        if len(environment_names) != len(set(environment_names)):
            _raise_contract_error(
                "duplicate_environment_name", "environment variable names must be unique"
            )
        destinations = tuple(item.destination for item in self.cache_mappings)
        if len(destinations) != len(set(destinations)):
            _raise_contract_error("duplicate_destination", "cache destinations must be unique")
        return self


class Workload(ContractModel):
    """Frozen three-t-clip-pipeline/v1alpha1 workload contract."""

    api_version: Literal["three-t-clip-pipeline/v1alpha1"] = Field(alias="apiVersion")
    kind: Literal["Workload"]
    metadata: Metadata
    spec: WorkloadSpec
