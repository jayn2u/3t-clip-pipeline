"""Permit-list sanitizer for durable operational evidence."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final, TypedDict

if TYPE_CHECKING:
    from pydantic import JsonValue

_DIGEST_IMAGE: Final = re.compile(r"^[^\s]+@(sha256:[0-9a-f]{64})$")
_SHA256: Final = re.compile(r"^[0-9a-f]{64}$")
_KUBERNETES_UID: Final = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_DNS_SUBDOMAIN: Final = re.compile(r"^[a-z0-9](?:[-a-z0-9.]{0,251}[a-z0-9])?$")
_SENSITIVE_FRAGMENT: Final = re.compile(
    r"secret|password|credential|token|access[-_]?key|signature",
    re.IGNORECASE,
)
_WORKFLOW_PHASES: Final = frozenset(("Pending", "Running", "Succeeded", "Failed", "Error"))
_MARKER_STATUSES: Final = frozenset(("committed", "uncommitted", "missing", "invalid", "unknown"))
_MAX_OBJECT_SIZE: Final = 5 * 1024**4
_MAX_DNS_NAME_LENGTH: Final = 253
_CONTROL_END: Final = 32
_DELETE_CHARACTER: Final = 127


class ObjectEvidence(TypedDict):
    """Permit-listed object proof metadata."""

    size: int
    sha256: str


class SanitizedEvidence(TypedDict, total=False):
    """Permit-listed Kubernetes and object-store proof metadata."""

    workflowUid: str
    phase: str
    imageDigests: list[str]
    selectedNode: str
    selectedPvc: str
    objects: list[ObjectEvidence]
    markerStatus: str


def _mapping(value: JsonValue | None) -> dict[str, JsonValue] | None:
    match value:
        case dict() as mapping:
            return mapping
        case _:
            return None


def _string(mapping: dict[str, JsonValue] | None, key: str) -> str | None:
    if mapping is None:
        return None
    match mapping.get(key):
        case str() as value:
            return value
        case _:
            return None


def _safe_text(value: str) -> bool:
    has_control = any(
        ord(character) < _CONTROL_END or ord(character) == _DELETE_CHARACTER for character in value
    )
    has_url_syntax = "://" in value or "?" in value or "#" in value
    return not has_control and not has_url_syntax and _SENSITIVE_FRAGMENT.search(value) is None


def _uid(value: str | None) -> str | None:
    if value is not None and _KUBERNETES_UID.fullmatch(value):
        return value
    return None


def _phase(value: str | None) -> str | None:
    if value in _WORKFLOW_PHASES:
        return value
    return None


def _dns_name(value: str | None) -> str | None:
    if (
        value is not None
        and len(value) <= _MAX_DNS_NAME_LENGTH
        and _safe_text(value)
        and _DNS_SUBDOMAIN.fullmatch(value)
    ):
        return value
    return None


def _marker_status(value: str | None) -> str | None:
    if value in _MARKER_STATUSES:
        return value
    return None


def _image_digests(images: JsonValue | None) -> list[str]:
    match images:
        case list() as values:
            return sorted(
                match.group(1)
                for image in values
                if isinstance(image, str) and (match := _DIGEST_IMAGE.fullmatch(image)) is not None
            )
        case _:
            return []


def _objects(objects: JsonValue | None) -> list[ObjectEvidence]:
    if not isinstance(objects, list):
        return []
    sanitized: list[ObjectEvidence] = []
    for item in objects:
        mapping = _mapping(item)
        key = _string(mapping, "key")
        sha256 = _string(mapping, "sha256")
        size = mapping.get("size") if mapping is not None else None
        if key is None or sha256 is None or _SHA256.fullmatch(sha256) is None:
            continue
        if not isinstance(size, int) or isinstance(size, bool):
            continue
        if 0 <= size <= _MAX_OBJECT_SIZE:
            sanitized.append({"size": size, "sha256": sha256})
    return sorted(sanitized, key=lambda item: (item["sha256"], item["size"]))


def sanitize_evidence(raw: JsonValue) -> SanitizedEvidence:
    """Return only non-sensitive proof metadata from untrusted external data."""
    root = _mapping(raw)
    if root is None:
        return {}
    result: SanitizedEvidence = {}
    metadata = _mapping(root.get("metadata"))
    status = _mapping(root.get("status"))
    marker = _mapping(root.get("marker"))
    workflow_uid = _uid(_string(metadata, "uid"))
    phase = _phase(_string(status, "phase"))
    selected_node = _dns_name(_string(root, "node"))
    selected_pvc = _dns_name(_string(root, "pvc"))
    marker_status = _marker_status(_string(marker, "status"))
    if workflow_uid is not None:
        result["workflowUid"] = workflow_uid
    if phase is not None:
        result["phase"] = phase
    if selected_node is not None:
        result["selectedNode"] = selected_node
    if selected_pvc is not None:
        result["selectedPvc"] = selected_pvc
    if marker_status is not None:
        result["markerStatus"] = marker_status

    result["imageDigests"] = _image_digests(root.get("images"))
    result["objects"] = _objects(root.get("objects"))
    return result
