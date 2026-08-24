from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/isolated-live-smoke.sh"

SOURCE_TO_LOCAL = (
    (
        (
            "quay.io/argoproj/workflow-controller:v4.0.7@sha256:"
            "8e3ca93350c18348e50cdb1899f37d672f9995d9bf51412d86dee52da22fff19"
        ),
        "argo-controller",
    ),
    (
        (
            "quay.io/argoproj/argocli:v4.0.7@sha256:"
            "8c141b1acd26df3de70724aedaae0ee5a7d361cdf744a1daa6a1596f205cee50"
        ),
        "argo-server",
    ),
    (
        (
            "quay.io/argoproj/argoexec:v4.0.7@sha256:"
            "eb2a7ca4d678a0c8c4f2de44f815f02d9eb12ac4609855f897744139aef220b4"
        ),
        "argo-executor",
    ),
    (
        (
            "registry.k8s.io/kubectl:v1.36.2@sha256:"
            "b0d792e0d8dfb9bb1b922b78b23137e2a34bb6f9667640353a9d2aadd1fd7761"
        ),
        "kubectl",
    ),
    (
        (
            "registry.k8s.io/nfd/node-feature-discovery:v0.18.3@sha256:"
            "f9ef2ebee55141a1758d3c0a87bb701f5db2adf6856f7218b11bc2bac7b63862"
        ),
        "node-feature-discovery",
    ),
    (
        (
            "nvcr.io/nvidia/k8s-device-plugin:v0.17.1@sha256:"
            "af31e2b7c7f89834c4e5219860def7ac2e49a207b3d4e8610d5a26772b7738e5"
        ),
        "nvidia-device-plugin",
    ),
    (
        (
            "docker.io/minio/minio:RELEASE.2025-04-22T22-12-26Z@sha256:"
            "a1ea29fa28355559ef137d71fc570e508a214ec84ff8083e39bc5428980b015e"
        ),
        "minio",
    ),
)


def _invoke(*arguments: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT), *arguments],
        cwd=ROOT,
        env=os.environ if env is None else env,
        check=False,
        capture_output=True,
        text=True,
    )


def _verification_env(tmp_path: Path) -> tuple[dict[str, str], Path]:
    canary_directory = tmp_path / "canaries"
    canary_directory.mkdir()
    canary_log = tmp_path / "external-command.log"
    for command in ("aws", "curl", "docker", "kind", "kubectl"):
        stub = canary_directory / command
        _ = stub.write_text(
            f"#!/bin/sh\nprintf '%s\\n' {command} >> \"$ISOLATED_LIVE_CANARY_LOG\"\nexit 97\n",
            encoding="utf-8",
        )
        _ = stub.chmod(0o755)
    return (
        {
            **os.environ,
            "ISOLATED_LIVE_CANARY_LOG": str(canary_log),
            "PATH": f"{canary_directory}:{os.environ['PATH']}",
        },
        canary_log,
    )


def test_literal_sources_map_to_exact_loopback_repositories_and_digests() -> None:
    # Given: the frozen source image lock and isolated-live script.
    lock = (ROOT / "deploy/versions.lock.yaml").read_text(encoding="utf-8")
    script = SCRIPT.read_text(encoding="utf-8")

    # When: literal mappings are independently compared.

    # Then: all seven locked sources and exact local repositories are present.
    assert len(SOURCE_TO_LOCAL) == 7
    for source, repository in SOURCE_TO_LOCAL:
        assert source in lock
        assert source in script
        assert f"localhost:5001/{repository}" in script


def test_external_target_rejected_before_mutation() -> None:
    # Given: a non-loopback registry request.

    # When: the smoke policy boundary parses it.
    result = _invoke("--create", "--registry", "ghcr.io/example", "--cleanup")

    # Then: it fails before a Docker or Kubernetes mutation.
    assert result.returncode == 2
    assert result.stderr == "ISOLATED_LIVE_REJECTED registry_not_loopback\n"


def test_mutable_digest_rejected_before_mutation() -> None:
    # Given: a source mapping override without a digest.
    env = {**os.environ, "ISOLATED_LIVE_SOURCE_OVERRIDE": "docker.io/minio/minio:latest"}

    # When: the smoke policy boundary checks image immutability.
    result = _invoke("--registry", "127.0.0.1:5001", "--cleanup", env=env)

    # Then: the mutable reference is rejected before mutation.
    assert result.returncode == 2
    assert result.stderr == "ISOLATED_LIVE_REJECTED source_digest_mismatch\n"


def test_occupied_port_rejected_before_mutation() -> None:
    # Given: another process owns loopback port 5001.
    env = {**os.environ, "ISOLATED_LIVE_TEST_OCCUPIED_PORT": "1"}

    # When: the smoke preflight runs.
    result = _invoke("--registry", "127.0.0.1:5001", "--cleanup", env=env)

    # Then: occupation is rejected before resource creation.
    assert result.returncode == 2
    assert result.stderr == "ISOLATED_LIVE_REJECTED occupied_port\n"


@pytest.mark.parametrize("credential", ["GHCR_TOKEN", "GITHUB_TOKEN", "DOCKER_AUTH_CONFIG"])
def test_credentials_rejected_before_mutation(credential: str) -> None:
    # Given: one forbidden registry credential is present.
    env = {**os.environ, credential: "credential-canary"}

    # When: the smoke preflight runs.
    result = _invoke("--registry", "127.0.0.1:5001", "--cleanup", env=env)

    # Then: it rejects the credential without leaking its value.
    assert result.returncode == 2
    assert result.stderr == "ISOLATED_LIVE_REJECTED registry_credentials_present\n"
    assert "credential-canary" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    ("resource", "code"),
    [("registry", "preexisting_registry"), ("cluster", "preexisting_cluster")],
)
def test_preexisting_resource_rejected_before_mutation(resource: str, code: str) -> None:
    # Given: a conflicting task-owned resource is reported by preflight discovery.
    env = {**os.environ, "ISOLATED_LIVE_TEST_PREEXISTING": resource}

    # When: the smoke preflight runs.
    result = _invoke("--registry", "127.0.0.1:5001", "--cleanup", env=env)

    # Then: no creation is attempted over the existing resource.
    assert result.returncode == 2
    assert result.stderr == f"ISOLATED_LIVE_REJECTED {code}\n"


def test_kind_fixture_is_loopback_registry_ready() -> None:
    # Given: the checked-in Kind fixture.
    fixture = ROOT / "tests/isolated_live/fixtures/kind-local-registry.yaml"

    # When: its machine-consumed configuration is read.
    document = fixture.read_text(encoding="utf-8")

    # Then: containerd uses the task-owned certs directory and no host port is exposed.
    assert "kind: Cluster" in document
    assert 'config_path = "/etc/containerd/certs.d"' in document
    assert "extraPortMappings" not in document


def test_static_policy_forbids_unsafe_storage_production_and_authorization_creation() -> None:
    # Given: all isolated-live and cutover-owned artifacts.
    storage = (ROOT / "deploy/kustomize/base/storage.yaml").read_text(encoding="utf-8")
    script = SCRIPT.read_text(encoding="utf-8")
    cutover = (ROOT / "docs/operator/pv-minio-cutover.md").read_text(encoding="utf-8")
    rollback = (ROOT / "docs/operator/pv-minio-rollback.md").read_text(encoding="utf-8")

    # When: static safety policy inspects their machine-significant tokens.

    # Then: no false storage proof or production apply path exists and authority is external.
    assert "DirectoryOrCreate" not in storage + script
    assert "hostPath:" in script
    assert "type: Directory" in script
    assert "environment-class production" not in script
    assert "docker login" not in script
    assert "AUTHORIZATION_FILE" in cutover + rollback
    assert "must already exist" in cutover
    assert "must already exist" in rollback


def test_interruptions_exit_after_cleanup_without_success_publication() -> None:
    # Given: the isolated-live signal and evidence publication boundaries.
    script = SCRIPT.read_text(encoding="utf-8")

    # When: their machine-significant statements are inspected.

    # Then: signals terminate and success is gated behind independently parsed evidence.
    assert "trap interrupt INT TERM" in script
    assert "exit 130" in script
    assert script.index("jq -e '.workflow.phase") < script.index("ISOLATED_LIVE_SUCCEEDED")


def test_pod_digest_verification_matches_declared_image_to_image_id_without_mutation(
    tmp_path: Path,
) -> None:
    # Given: Kubernetes reports a bare status.image and matching declared/imageID digest.
    fixture = ROOT / "tests/isolated_live/fixtures/pods-matching.json"
    env, canary_log = _verification_env(tmp_path)

    # When: the verification-only CLI consumes the Pod list.
    result = _invoke("--verify-pods-json", str(fixture), env=env)

    # Then: one declared/imageID comparison passes and no external command was selected.
    assert result.returncode == 0
    assert result.stdout == "ISOLATED_LIVE_PODS_VERIFIED comparisons=1\n"
    assert not canary_log.exists()


@pytest.mark.parametrize(
    ("fixture_name", "rejection"),
    [
        ("pods-mismatched.json", "pod_image_digest_mismatch"),
        ("pods-missing-declared.json", "pod_image_declared_mapping_missing"),
        ("pods-empty-statuses.json", "pod_image_statuses_missing"),
        ("pods-invalid.json", "pod_json_invalid"),
    ],
)
def test_pod_digest_verification_rejects_untrustworthy_payloads_without_mutation(
    tmp_path: Path, fixture_name: str, rejection: str
) -> None:
    # Given: a Pod payload missing a trustworthy declared-to-runtime digest comparison.
    fixture = ROOT / "tests/isolated_live/fixtures" / fixture_name
    env, canary_log = _verification_env(tmp_path)

    # When: the verification-only CLI consumes the malformed or misleading payload.
    result = _invoke("--verify-pods-json", str(fixture), env=env)

    # Then: it rejects before selecting Docker, Kind, Kubernetes, curl, or S3 tooling.
    assert result.returncode == 2
    assert result.stderr == f"ISOLATED_LIVE_REJECTED {rejection}\n"
    assert not canary_log.exists()


@pytest.mark.parametrize(
    "fixture_name",
    [
        "cleanup-ownership-mismatch.json",
        "cleanup-ownership-node-mismatch.json",
        "cleanup-ownership-token-mismatch.json",
    ],
)
def test_cleanup_ownership_rejects_replacement_without_mutation(
    tmp_path: Path, fixture_name: str
) -> None:
    # Given: a task's captured IDs differ from resources now reachable by the old names.
    fixture = ROOT / "tests/isolated_live/fixtures" / fixture_name
    env, canary_log = _verification_env(tmp_path)

    # When: cleanup ownership selection is verified.
    result = _invoke("--verify-cleanup-ownership-json", str(fixture), env=env)

    # Then: neither replacement is selected for deletion.
    assert result.returncode == 2
    assert result.stderr == "ISOLATED_LIVE_REJECTED cleanup_ownership_mismatch\n"
    assert not canary_log.exists()


def test_cleanup_evidence_verification_requires_every_owned_resource_receipt(
    tmp_path: Path,
) -> None:
    # Given: a complete cleanup receipt for an isolated Kind run.
    fixture = ROOT / "tests/isolated_live/fixtures/cleanup-evidence-matching.json"
    env, canary_log = _verification_env(tmp_path)

    # When: the cleanup-evidence verifier consumes the receipt.
    result = _invoke("--verify-cleanup-evidence-json", str(fixture), env=env)

    # Then: it accepts only the complete receipt without selecting external tooling.
    assert result.returncode == 0
    assert result.stdout == "ISOLATED_LIVE_CLEANUP_VERIFIED\n"
    assert not canary_log.exists()


@pytest.mark.parametrize(
    "fixture_name",
    [
        "cleanup-evidence-port19000-busy.json",
        "cleanup-evidence-pid-live.json",
        "cleanup-evidence-stale-context.json",
        "cleanup-evidence-temp-present.json",
    ],
)
def test_cleanup_evidence_verification_rejects_incomplete_receipts_without_mutation(
    tmp_path: Path, fixture_name: str
) -> None:
    # Given: a receipt that leaves a task-owned resource or user kubeconfig changed.
    fixture = ROOT / "tests/isolated_live/fixtures" / fixture_name
    env, canary_log = _verification_env(tmp_path)

    # When: the cleanup-evidence verifier consumes the incomplete receipt.
    result = _invoke("--verify-cleanup-evidence-json", str(fixture), env=env)

    # Then: it rejects the misleading cleanup success without external tooling.
    assert result.returncode == 2
    assert result.stderr == "ISOLATED_LIVE_REJECTED cleanup_evidence_incomplete\n"
    assert not canary_log.exists()
