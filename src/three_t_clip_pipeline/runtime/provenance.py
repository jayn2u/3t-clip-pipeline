"""Sanitized immutable runtime provenance."""

from __future__ import annotations

import json
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, StrictStr


class ProvenanceRecord(BaseModel):
    """Non-secret identities safe to publish with a run."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True, strict=True)

    run_uid: StrictStr
    workflow_uid: StrictStr
    workload_image_digest: StrictStr
    platform_image_digest: StrictStr
    bundle_digest: StrictStr
    source_revision: StrictStr | None = None


def provenance_bytes(record: ProvenanceRecord) -> bytes:
    """Serialize sanitized provenance canonically."""
    payload = record.model_dump(mode="json")
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
