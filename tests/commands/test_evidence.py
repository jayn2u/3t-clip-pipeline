from __future__ import annotations

import json
from typing import TYPE_CHECKING

from three_t_clip_pipeline.evidence import sanitize_evidence

if TYPE_CHECKING:
    from pydantic import JsonValue


def test_evidence_allows_only_selected_metadata_and_digest_images() -> None:
    # Given: hostile external evidence containing every prohibited secret form.
    raw: JsonValue = {
        "metadata": {
            "uid": "123e4567-e89b-12d3-a456-426614174000",
            "name": "wf",
            "annotations": {"token": "CANARY"},
        },
        "status": {"phase": "Succeeded"},
        "images": [
            "registry/workload@sha256:" + "b" * 64,
            "registry/workload:latest",
        ],
        "node": "gpu-a",
        "pvc": "cache-pvc",
        "objects": [
            {
                "key": "metrics.json",
                "size": 42,
                "sha256": "a" * 64,
                "signedUrl": "https://example.invalid/?X-Amz-Credential=CANARY",
            }
        ],
        "marker": {"status": "committed", "secret": {"data": "CANARY"}},
        "env": {"AWS_SECRET_ACCESS_KEY": "CANARY"},
        "kind": "Secret",
        "data": {"password": "CANARY"},
    }

    # When: external data crosses the evidence boundary.
    sanitized = sanitize_evidence(raw)
    encoded = json.dumps(sanitized, sort_keys=True)

    # Then: only the permit-listed proof fields survive.
    assert sanitized == {
        "workflowUid": "123e4567-e89b-12d3-a456-426614174000",
        "phase": "Succeeded",
        "imageDigests": ["sha256:" + "b" * 64],
        "selectedNode": "gpu-a",
        "selectedPvc": "cache-pvc",
        "objects": [{"size": 42, "sha256": "a" * 64}],
        "markerStatus": "committed",
    }
    assert "CANARY" not in encoded
    assert ":latest" not in encoded
    assert "signed" not in encoded.lower()
    assert "secret" not in encoded.lower()


def test_evidence_rejects_hostile_values_in_every_allowed_string_field() -> None:
    # Given: prohibited material placed only in nominally allowed evidence fields.
    raw: JsonValue = {
        "metadata": {"uid": "credential-marker"},
        "status": {"phase": "https://signed.invalid/?token=phase"},
        "images": ["registry/workload:latest"],
        "node": "token-bearing-node",
        "pvc": "secret-bearing-pvc",
        "objects": [
            {
                "key": "https://signed.invalid/?token=object",
                "size": True,
                "sha256": "a" * 64,
            }
        ],
        "marker": {"status": "password-marker"},
    }

    # When: hostile values cross the sanitizer boundary.
    encoded = json.dumps(sanitize_evidence(raw), sort_keys=True)

    # Then: no arbitrary string or boolean-as-size is durable evidence.
    assert sanitize_evidence(raw) == {"imageDigests": [], "objects": []}
    assert all(
        fragment not in encoded.casefold()
        for fragment in (
            "credential",
            "https://",
            "?token=",
            "token-bearing",
            "secret-bearing",
            ":latest",
            "password",
        )
    )


def test_evidence_never_persists_raw_object_keys() -> None:
    # Given: valid object proofs paired with ordinary and hostile opaque keys.
    keys = (
        "CANARY",
        "metrics.json",
        "https://signed.invalid/object",
        "result.bin?token=value",
        "control\x1fkey",
    )
    raw: JsonValue = {
        "objects": [
            {"key": key, "size": index, "sha256": character * 64}
            for index, (key, character) in enumerate(zip(keys, "abcde", strict=True), start=1)
        ]
    }

    # When: object evidence crosses the durable sanitizer boundary.
    sanitized = sanitize_evidence(raw)
    encoded = json.dumps(sanitized, sort_keys=True)

    # Then: size/checksum proof remains but no raw key field or value survives.
    assert sanitized.get("objects") == [
        {"size": index, "sha256": character * 64}
        for index, character in enumerate("abcde", start=1)
    ]
    assert '"key"' not in encoded
    assert all(key not in encoded for key in keys)
