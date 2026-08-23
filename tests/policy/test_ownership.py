import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, validate
from pydantic import RootModel

from three_t_clip_pipeline.policy.ownership import (
    OwnershipPolicyError,
    load_ownership,
    parse_ownership,
)

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]


class JsonDocument(RootModel[JsonValue]):
    pass


REPOSITORY_ROOT = Path(__file__).parents[2]
LEDGER_PATH = REPOSITORY_ROOT / "config/resource-ownership.yaml"
SCHEMA_PATH = REPOSITORY_ROOT / "schemas/resource-ownership.schema.json"
EXPECTED_IDENTITIES = {
    ("platform.three-t.dev/v1alpha1", "HostBootstrapSet", "_cluster", "inventory:k3s_cluster"),
    ("apiextensions.k8s.io/v1", "CustomResourceDefinitionSet", "_cluster", "argoproj.io"),
    ("apps/v1", "DeploymentSet", "argo", "argo-control-plane-and-rbac"),
    (
        "platform.three-t.dev/v1alpha1",
        "HelmReleaseSet",
        "_cluster",
        "gpu-discovery-and-device-plugin",
    ),
    ("platform.three-t.dev/v1alpha1", "KustomizeResourceSet", "three-t-pipeline", "platform-core"),
    ("storage.k8s.io/v1", "StorageResourceSet", "three-t-pipeline", "configured-overlay-storage"),
    ("apps/v1", "MinIOResourceSet", "three-t-pipeline", "configured-overlay-minio"),
    ("v1", "SecretSet", "three-t-pipeline", "platform-secrets"),
    ("platform.three-t.dev/v1alpha1", "OptionalResourceSet", "_cluster", "tailscale"),
    ("argoproj.io/v1alpha1", "Workflow", "three-t-pipeline", "generated"),
    ("s3.platform.three-t.dev/v1alpha1", "RunPrefix", "_object-store", "generated-run-prefix"),
}
DUPLICATE_LEDGER = """
apiVersion: platform.three-t.dev/v1alpha1
kind: ResourceOwnershipLedger
resources:
  - &resource
    apiVersion: v1
    kind: Secret
    namespace: three-t-pipeline
    name: credentials
    owner: owner-a
    mechanism: ansible
    adoptionPolicy: encryption-gated
    backupRequirement: key-name-only inventory
    rollbackOwner: operator-a
  - <<: *resource
    owner: owner-b
"""


def test_resource_ledger_matches_schema() -> None:
    schema = JsonDocument.model_validate_json(SCHEMA_PATH.read_text(encoding="utf-8")).root
    assert isinstance(schema, dict)
    Draft202012Validator.check_schema(schema)
    ledger = load_ownership(LEDGER_PATH)
    document = JsonDocument.model_validate_json(ledger.model_dump_json(by_alias=True)).root
    validate(instance=document, schema=schema, cls=Draft202012Validator)


def test_every_matrix_row_has_exactly_one_owner() -> None:
    ledger = load_ownership(LEDGER_PATH)
    identities = {resource.identity for resource in ledger.resources}
    assert identities == EXPECTED_IDENTITIES
    assert len(identities) == len(ledger.resources) == 11
    assert all(resource.owner.strip() for resource in ledger.resources)


def test_retain_is_not_accepted_as_backup() -> None:
    malformed = DUPLICATE_LEDGER.replace("key-name-only inventory", "Retain")
    malformed = malformed.replace("  - <<: *resource\n    owner: owner-b\n", "")
    with pytest.raises(OwnershipPolicyError, match="ownership_schema_invalid"):
        _ = parse_ownership(malformed)


def test_live_adoption_is_not_a_policy_value() -> None:
    malformed = DUPLICATE_LEDGER.replace("encryption-gated", "adopt-live")
    malformed = malformed.replace("  - <<: *resource\n    owner: owner-b\n", "")
    with pytest.raises(OwnershipPolicyError, match="ownership_schema_invalid"):
        _ = parse_ownership(malformed)


def test_duplicate_owner_rejected() -> None:
    with pytest.raises(OwnershipPolicyError) as captured:
        _ = parse_ownership(DUPLICATE_LEDGER)
    assert captured.value.code == "ownership_conflict"


def test_malformed_owner_rejected() -> None:
    malformed = DUPLICATE_LEDGER.replace("owner: owner-a", "owner: ''")
    malformed = malformed.replace("  - <<: *resource\n    owner: owner-b\n", "")
    with pytest.raises(OwnershipPolicyError, match="ownership_schema_invalid"):
        _ = parse_ownership(malformed)


def test_checker_reports_machine_success() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "three_t_clip_pipeline.policy.check_ownership",
            str(LEDGER_PATH),
        ],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout == "OWNERSHIP_OK\n"
    assert result.stderr == ""
