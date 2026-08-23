from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Final, TypedDict, override

import jsonschema
from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter

ROOT: Final = Path(__file__).parents[2]
ROLE: Final = ROOT / "ansible/roles/platform_secrets"
SCHEMA: Final = ROOT / "schemas/bootstrap-secrets.schema.json"


class Contract(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    type: str
    keys: tuple[str, ...]


class RoleVariables(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    platform_secrets_namespace: str
    platform_secrets_contracts: dict[str, Contract]


class RoleDefaults(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    platform_secrets_context: str | None
    platform_secrets_environment_class: str | None
    platform_secrets_authorized_contexts: tuple[str, ...]
    platform_secrets_k3s_encryption_enabled: bool


class SecretMetadata(TypedDict):
    name: str
    namespace: str


class SecretDefinition(TypedDict):
    apiVersion: str
    kind: str
    metadata: SecretMetadata
    type: str
    data: dict[str, str]


class Evidence(TypedDict):
    name: str
    namespace: str
    uid: str
    resourceVersion: str
    key_names: list[str]
    key_names_sha256: str


@dataclass(frozen=True, slots=True)
class LifecycleRequest:
    context: str | None
    environment: str | None
    authorized_contexts: tuple[str, ...]
    encryption_enabled: bool
    values: dict[str, dict[str, str]]


@dataclass(frozen=True, slots=True)
class LifecycleError(RuntimeError):
    code: str

    @override
    def __str__(self) -> str:
        return self.code


@dataclass(slots=True)
class FakeKubernetesClient:
    mutations: int = 0
    dry_runs: int = 0
    resources: dict[str, SecretDefinition] = field(default_factory=dict)

    def apply(self, definition: SecretDefinition, *, dry_run: bool) -> dict[str, str]:
        name = definition["metadata"]["name"]
        if dry_run:
            self.dry_runs += 1
        elif self.resources.get(name) != definition:
            self.mutations += 1
            self.resources[name] = definition
        return {"uid": "fake-uid", "resourceVersion": "7"}


_ROLE_VARIABLES_ADAPTER: Final = TypeAdapter(RoleVariables)
_ROLE_DEFAULTS_ADAPTER: Final = TypeAdapter(RoleDefaults)
_SCHEMA_ADAPTER: Final = TypeAdapter(dict[str, JsonValue])
_EXPLICIT_REQUIRED: Final = "explicit_context_and_environment_required"
_CONTEXT_UNAUTHORIZED: Final = "context_not_authorized"
_PRODUCTION_FORBIDDEN: Final = "production_mutation_forbidden"
_ENCRYPTION_REQUIRED: Final = "k3s_secret_encryption_required"
_ENVIRONMENT_INVALID: Final = "environment_class_invalid"


def _json_yaml(path: Path) -> str:
    content = path.read_text(encoding="utf-8")
    return content.removeprefix("---\n")


def role_variables() -> RoleVariables:
    return _ROLE_VARIABLES_ADAPTER.validate_json(_json_yaml(ROLE / "vars/main.yml"))


def role_defaults() -> RoleDefaults:
    return _ROLE_DEFAULTS_ADAPTER.validate_json(_json_yaml(ROLE / "defaults/main.yml"))


def synthetic_values(
    expected_contracts: dict[str, tuple[str, tuple[str, ...]]],
) -> dict[str, dict[str, str]]:
    values: dict[str, dict[str, str]] = {}
    for name, (_secret_type, keys) in expected_contracts.items():
        values[name] = {key: f"fixture-{name}-{index}" for index, key in enumerate(keys)}
    return values


def schema() -> dict[str, JsonValue]:
    return _SCHEMA_ADAPTER.validate_json(SCHEMA.read_text(encoding="utf-8"))


def schema_path() -> Path:
    return SCHEMA


def apply_with_fake(
    request: LifecycleRequest,
    client: FakeKubernetesClient,
) -> list[Evidence]:
    if not request.context or not request.environment:
        raise LifecycleError(_EXPLICIT_REQUIRED)
    if request.environment == "production":
        raise LifecycleError(_PRODUCTION_FORBIDDEN)
    if request.context not in request.authorized_contexts:
        raise LifecycleError(_CONTEXT_UNAUTHORIZED)
    if not request.encryption_enabled:
        raise LifecycleError(_ENCRYPTION_REQUIRED)
    if request.environment not in {"development", "ephemeral"}:
        raise LifecycleError(_ENVIRONMENT_INVALID)

    jsonschema.validate({"platform_secret_values": request.values}, schema())
    evidence: list[Evidence] = []
    for name, contract in role_variables().platform_secrets_contracts.items():
        secret_values = request.values[name]
        definition = SecretDefinition(
            apiVersion="v1",
            kind="Secret",
            metadata={"name": name, "namespace": "three-t-pipeline"},
            type=contract.type,
            data={
                key: base64.b64encode(value.encode()).decode()
                for key, value in secret_values.items()
            },
        )
        result = client.apply(definition, dry_run=request.environment == "development")
        key_names = sorted(secret_values)
        evidence.append(
            Evidence(
                name=name,
                namespace="three-t-pipeline",
                uid=result["uid"],
                resourceVersion=result["resourceVersion"],
                key_names=key_names,
                key_names_sha256=hashlib.sha256("\n".join(key_names).encode()).hexdigest(),
            )
        )
    return evidence
