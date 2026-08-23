#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["pyyaml>=6.0,<7"]
# ///
# ─── How to run ───
# .venv/bin/python scripts/platform_manifest.py --help

"""Build and policy-check the deterministic platform manifest inventory."""

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final, NewType

import yaml
from pydantic import JsonValue, TypeAdapter

Owner = NewType("Owner", str)
Identity = tuple[str, str, str, str]
IMMUTABLE_IMAGE: Final = re.compile(r"^[^\s]+@sha256:[0-9a-f]{64}$")
FLOATING_REFERENCE: Final = re.compile(r"https?://[^\s]*(?:/|ref=)(?:main|master)(?:[/?#\s]|$)")
MANIFEST_INVALID: Final = "manifest_invalid"
OWNERSHIP_CONFLICT: Final = "ownership_conflict"
FLOATING_REFERENCE_CODE: Final = "floating_reference"
IMAGE_NOT_IMMUTABLE: Final = "image_not_immutable"
DOCUMENT_BOUNDARY: Final = re.compile(r"(?m)^---\s*$")
_JSON_VALUE_ADAPTER: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)


@dataclass(frozen=True, slots=True)
class _Source:
    owner: Owner
    path: Path


@dataclass(frozen=True, slots=True)
class _Resource:
    document: dict[str, JsonValue]
    identity: Identity
    owner: Owner
    ledger_owner: str


class _PlatformPolicyError(Exception):
    code: str
    detail: str

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def _mapping(value: JsonValue, field: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise _PlatformPolicyError(MANIFEST_INVALID, field)
    return value


def _required_string(value: JsonValue, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise _PlatformPolicyError(MANIFEST_INVALID, field)
    return value


def _identity(document: dict[str, JsonValue]) -> Identity:
    metadata = _mapping(document.get("metadata"), "metadata")
    return (
        _required_string(document.get("apiVersion"), "apiVersion"),
        _required_string(document.get("kind"), "kind"),
        _required_string(metadata.get("namespace", "_cluster"), "namespace"),
        _required_string(metadata.get("name"), "name"),
    )


def _images(value: JsonValue) -> list[str]:
    if isinstance(value, dict):
        found: list[str] = []
        for key, item in value.items():
            if key == "image" and isinstance(item, str):
                found.append(item)
            found.extend(_images(item))
        return found
    if isinstance(value, list):
        return [image for item in value for image in _images(item)]
    return []


def _ledger_owner(owner: Owner, identity: Identity) -> str:
    _, kind, _, _ = identity
    if owner == "helm-argo":
        return "Helm release argo-workflows"
    if owner in {"helm-gpu", "helm"}:
        return "pinned NFD/NVIDIA Helm releases"
    if kind in {"StorageClass", "PersistentVolume", "PersistentVolumeClaim"}:
        return "Kustomize field manager three-t-pipeline-storage"
    if kind in {"Deployment", "Service"}:
        return "Kustomize field manager three-t-pipeline-storage"
    return "Kustomize field manager three-t-pipeline-platform"


def _load_ledger_owners(path: Path) -> set[str]:
    raw: JsonValue = _JSON_VALUE_ADAPTER.validate_python(
        yaml.safe_load(path.read_text(encoding="utf-8"))
    )
    document = _mapping(raw, "ownership_ledger")
    resources = document.get("resources")
    if not isinstance(resources, list):
        raise _PlatformPolicyError(OWNERSHIP_CONFLICT, "ledger_resources")
    owners: set[str] = set()
    for resource in resources:
        owner = _mapping(resource, "ledger_resource").get("owner")
        if isinstance(owner, str):
            owners.add(owner)
    return owners


def _parse_source(source: _Source, ledger_owners: set[str]) -> list[_Resource]:
    text = source.path.read_text(encoding="utf-8")
    if FLOATING_REFERENCE.search(text):
        raise _PlatformPolicyError(FLOATING_REFERENCE_CODE, str(source.path))
    resources: list[_Resource] = []
    for document_text in DOCUMENT_BOUNDARY.split(text):
        raw: JsonValue = _JSON_VALUE_ADAPTER.validate_python(yaml.safe_load(document_text))
        if raw is None:
            continue
        document = _mapping(raw, "document")
        identity = _identity(document)
        metadata = _mapping(document.get("metadata"), "metadata")
        annotations = _mapping(metadata.get("annotations", {}), "annotations")
        annotated_owner = annotations.get("platform.three-t.dev/owner")
        owner = Owner(annotated_owner) if isinstance(annotated_owner, str) else source.owner
        for image in _images(document):
            if not IMMUTABLE_IMAGE.fullmatch(image):
                raise _PlatformPolicyError(IMAGE_NOT_IMMUTABLE, image)
        ledger_owner = _ledger_owner(owner, identity)
        if ledger_owner not in ledger_owners:
            raise _PlatformPolicyError(OWNERSHIP_CONFLICT, ledger_owner)
        mechanism_owner = Owner("helm") if owner.startswith("helm") else owner
        resources.append(_Resource(document, identity, mechanism_owner, ledger_owner))
    return resources


def _write(resources: list[_Resource], output: Path | None, inventory: Path) -> None:
    ordered = sorted(resources, key=lambda resource: resource.identity)
    if output is not None:
        rendered = yaml.safe_dump_all(
            [resource.document for resource in ordered],
            explicit_start=True,
            sort_keys=True,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        _ = output.write_text(rendered, encoding="utf-8")
    items = [
        {
            "apiVersion": resource.identity[0],
            "kind": resource.identity[1],
            "namespace": resource.identity[2],
            "name": resource.identity[3],
            "owner": resource.owner,
            "ledgerOwner": resource.ledger_owner,
        }
        for resource in ordered
    ]
    inventory.parent.mkdir(parents=True, exist_ok=True)
    _ = inventory.write_text(
        json.dumps(items, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _option_values(arguments: list[str], option: str) -> list[str]:
    return [arguments[index + 1] for index, value in enumerate(arguments[:-1]) if value == option]


def _required_option(arguments: list[str], option: str) -> str:
    values = _option_values(arguments, option)
    if len(values) != 1:
        raise _PlatformPolicyError(MANIFEST_INVALID, option)
    return values[0]


def _main() -> int:
    arguments = sys.argv[1:]
    sources: list[_Source] = []
    for value in _option_values(arguments, "--source"):
        owner, separator, path = value.partition(":")
        if separator != ":" or owner not in {"helm", "helm-argo", "helm-gpu", "kustomize"}:
            raise _PlatformPolicyError(MANIFEST_INVALID, "source")
        sources.append(_Source(Owner(owner), Path(path)))
    inventory = Path(_required_option(arguments, "--inventory"))
    ledger = Path(_required_option(arguments, "--ledger"))
    output_values = _option_values(arguments, "--output")
    output = Path(output_values[0]) if len(output_values) == 1 else None
    ledger_owners = _load_ledger_owners(ledger)
    resources = [
        resource for source in sources for resource in _parse_source(source, ledger_owners)
    ]
    identities: set[Identity] = set()
    for resource in resources:
        if resource.identity in identities:
            raise _PlatformPolicyError(OWNERSHIP_CONFLICT, "/".join(resource.identity))
        identities.add(resource.identity)
    _write(resources, output, inventory)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(_main())
    except _PlatformPolicyError as error:
        _ = sys.stderr.write(f"PLATFORM_RENDER_INVALID {error}\n")
        raise SystemExit(2) from error
