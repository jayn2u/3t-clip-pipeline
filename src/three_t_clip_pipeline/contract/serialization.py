"""Canonical workload serialization and identity."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, NewType

if TYPE_CHECKING:
    from three_t_clip_pipeline.contract.models import Workload

WorkloadDigest = NewType("WorkloadDigest", str)


def canonical_json_bytes(workload: Workload) -> bytes:
    """Serialize a workload as canonical UTF-8 JSON with materialized defaults."""
    document = workload.model_dump(mode="json", by_alias=True, exclude_none=False)
    return (
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()


def workload_digest(workload: Workload) -> WorkloadDigest:
    """Return the SHA-256 identity of the canonical workload bytes."""
    return WorkloadDigest(hashlib.sha256(canonical_json_bytes(workload)).hexdigest())
