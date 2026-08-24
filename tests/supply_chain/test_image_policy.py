import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from pydantic import JsonValue, TypeAdapter

ROOT = Path(__file__).parents[2]
TOOLS_LOCK = ROOT / "ci/tools.lock.yaml"
IMAGES_LOCK = ROOT / "deploy/images.lock.yaml"
METADATA_POLICY = ROOT / "scripts/image-metadata.py"
RUNTIME_REPOSITORY = "ghcr.io/jayn2u/3t-clip-pipeline-runtime"
IMMUTABLE_RUNTIME = re.compile(rf"^{re.escape(RUNTIME_REPOSITORY)}@sha256:[0-9a-f]{{64}}$")
MAPPING_ADAPTER: TypeAdapter[dict[str, JsonValue]] = TypeAdapter(dict[str, JsonValue])


def _mapping(path: Path) -> dict[str, JsonValue]:
    return MAPPING_ADAPTER.validate_python(yaml.safe_load(path.read_text(encoding="utf-8")))


def _workflow_text(name: str) -> str:
    return (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")


def test_frozen_tool_and_action_pins_are_consumed_exactly() -> None:
    # Given: Task 1's authoritative immutable tool lock.
    lock_text = TOOLS_LOCK.read_text(encoding="utf-8")
    lock = _mapping(TOOLS_LOCK)
    workflows = _workflow_text("ci.yml") + _workflow_text("runtime-image.yml")

    # When: every executable checksum and GitHub Action pin is enumerated.
    checksums: set[str] = set(re.findall(r"(?m)^\s+\w+(?:Tar)?Sha256: ([0-9a-f]{64})$", lock_text))
    actions = lock["githubActions"]
    assert isinstance(actions, dict)

    # Then: every used action is pinned, while checksummed tool setup actions stay unused.
    assert len(checksums) == 7
    assert all(checksum in workflows for checksum in checksums)
    assert len(actions) == 7
    assert all(isinstance(action, str) for action in actions.values())
    for action_name in ("checkout", "uploadArtifact", "setupDocker", "buildPush", "login"):
        action = actions[action_name]
        assert isinstance(action, str)
        assert action in workflows
    setup_uv = actions["setupUv"]
    setup_buildx = actions["setupBuildx"]
    assert isinstance(setup_uv, str)
    assert isinstance(setup_buildx, str)
    assert setup_uv not in workflows
    assert setup_buildx not in workflows
    assert not re.search(r"uses:\s+[^\s]+@(main|master|v\d+)(?:\s|$)", workflows)


def test_image_lock_contains_only_immutable_reviewed_boundaries() -> None:
    # Given: the deployment image lock.
    lock = _mapping(IMAGES_LOCK)

    # When: its runtime and local infrastructure references are read.
    runtime_image = lock["runtimeImage"]

    # Then: every boundary is exact and the local exception is loopback-only.
    assert lock["reviewStatus"] == "reviewed"
    assert runtime_image == (
        "ghcr.io/jayn2u/3t-clip-pipeline-runtime@sha256:"
        "9ba8cd5a2a1d8c6882edfe80ab10c0df4d0c7ca320626e8aed74344367eafe42"
    )
    assert isinstance(runtime_image, str)
    assert IMMUTABLE_RUNTIME.fullmatch(runtime_image)
    assert lock["kindNodeImage"] == (
        "docker.io/kindest/node:v1.35.0@sha256:"
        "4613778f3cfcd10e615029370f5786704559103cf27bef934597ba562b269661"
    )
    assert lock["localRegistryImage"] == (
        "docker.io/library/registry:2.8.3@sha256:"
        "a3d8aaa63ed8681a604f1dea0aa03f100d5895b6a58ace528858a7b332415373"
    )
    assert lock["localPushException"] == {
        "credentials": "forbidden",
        "host": "127.0.0.1",
        "owner": "task-14",
    }


def test_runtime_workflow_has_approved_digest_lifecycle() -> None:
    # Given: the release workflow as machine-consumed YAML text.
    workflow = _workflow_text("runtime-image.yml")

    # When: its publication and attestation controls are inspected.
    # Then: PRs cannot push, develop requires approval, and outputs are immutable.
    assert "environment: runtime-image-release" in workflow
    assert "github.ref == 'refs/heads/develop'" in workflow
    assert "sha-${{ github.sha }}" in workflow
    assert "runtime-envelope-index.json" in workflow
    assert "sbom: true" in workflow
    assert "provenance: mode=max" in workflow
    assert ".cache/ci/runtime-sbom.json" in workflow
    assert ".cache/ci/runtime-provenance.json" in workflow
    assert "scripts/image-metadata.py propose" in workflow
    assert "D_envelope: ${{ steps.build.outputs.digest }}" in workflow
    assert "D_runtime: ${{ steps.select.outputs.runtime-digest }}" in workflow
    assert "scripts/image-metadata.py select-runtime" in workflow
    assert 'imagetools inspect "$REGISTRY_IMAGE@$D_envelope"' in workflow
    assert "deploy/images.lock.yaml" not in re.sub(
        r"(?m)^\s*#.*$", "", workflow.split("scripts/image-metadata.py propose", maxsplit=1)[1]
    )
    assert "docker push" not in _workflow_text("ci.yml")
    assert "docker login" not in _workflow_text("ci.yml")
    assert ":latest" not in workflow


@pytest.mark.parametrize(
    ("digest", "commit_sha"),
    [
        ("latest", "a" * 40),
        ("sha256:" + "0" * 63, "a" * 40),
        ("sha256:" + "b" * 64, "short"),
    ],
    ids=["latest_rejected", "malformed_digest_rejected", "commit_tag_rejected"],
)
def test_unreviewed_digest_rejected_before_proposal(
    tmp_path: Path,
    digest: str,
    commit_sha: str,
) -> None:
    # Given: malformed BuildKit metadata or a non-canonical commit identity.
    metadata = tmp_path / "metadata.json"
    output = tmp_path / "proposal.yaml"
    _ = metadata.write_text(
        json.dumps(
            {
                "envelopeDigest": "sha256:" + "e" * 64,
                "runtimeDigest": digest,
            }
        ),
        encoding="utf-8",
    )

    # When: a lock proposal is requested.
    result = subprocess.run(
        [
            sys.executable,
            str(METADATA_POLICY),
            "propose",
            "--metadata",
            str(metadata),
            "--commit-sha",
            commit_sha,
            "--output",
            str(output),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: the boundary fails closed without publishing a proposal.
    assert result.returncode == 2
    assert "IMAGE_METADATA_INVALID" in result.stderr
    assert not output.exists()


def test_valid_metadata_produces_review_required_proposal(tmp_path: Path) -> None:
    # Given: selected metadata with distinct envelope and runtime digests.
    metadata = tmp_path / "metadata.json"
    output = tmp_path / "proposal.yaml"
    digest = "sha256:" + "b" * 64
    commit_sha = "a" * 40
    envelope_digest = "sha256:" + "e" * 64
    _ = metadata.write_text(
        json.dumps({"envelopeDigest": envelope_digest, "runtimeDigest": digest}),
        encoding="utf-8",
    )

    # When: the metadata policy creates a proposal artifact.
    result = subprocess.run(
        [
            sys.executable,
            str(METADATA_POLICY),
            "propose",
            "--metadata",
            str(metadata),
            "--commit-sha",
            commit_sha,
            "--output",
            str(output),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: the proposal is immutable and explicitly unapproved.
    assert result.returncode == 0
    proposal = _mapping(output)
    assert proposal["runtimeImage"] == f"{RUNTIME_REPOSITORY}@{digest}"
    assert proposal["envelopeDigest"] == envelope_digest
    assert proposal["immutableTag"] == f"sha-{commit_sha}"
    assert proposal["reviewStatus"] == "proposed"
    assert proposal["autoUpdate"] is False


def test_pr_push_forbidden_and_latest_rejected() -> None:
    # Given: both CI workflows and the operator lock.
    ci_workflow = _workflow_text("ci.yml")
    release_workflow = _workflow_text("runtime-image.yml")
    lock = IMAGES_LOCK.read_text(encoding="utf-8")

    # When: mutable and unauthorized mutation paths are scanned.
    # Then: CI never logs in/pushes and no artifact selects latest.
    assert "docker/login-action" not in ci_workflow
    assert "push: true" not in ci_workflow
    assert ":latest" not in ci_workflow + release_workflow + lock
    assert "autoUpdate: false" in lock
    assert "preserveReferencedDigests: true" in lock


def test_runtime_container_context_excludes_secret_and_ml_inputs() -> None:
    # Given: the runtime build definition and allowlisted context.
    containerfile = (ROOT / "Containerfile.runtime").read_text(encoding="utf-8")
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    base = (
        "docker.io/library/python:3.12.11-slim-bookworm@sha256:"
        "519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7"
    )
    frontend_digest = "26147acbda4f14c5add9946e2fd2ed543fc402884fd75146bd342a7f6271dc1d"
    syntax = f"# syntax=docker/dockerfile:1.20@sha256:{frontend_digest}"

    # When: the build inputs, base, and runtime identity are inspected.
    # Then: only package inputs enter a digest-pinned non-root CPU image.
    assert dockerignore == [
        "**",
        "!README.md",
        "!pyproject.toml",
        "!uv.lock",
        "!src/",
        "!src/**",
        "src/**/__pycache__/",
        "src/**/*.py[co]",
    ]
    assert containerfile.count(f"FROM {base}") == 2
    assert containerfile.startswith(syntax)
    assert "aa9fca823c03289fb6e3460b3dc864f3ea895cafaf9b99247701a67b17d1b018" in (containerfile)
    assert "uv sync --locked --no-dev --no-editable" in containerfile
    assert "USER 65532:65532" in containerfile
    assert not re.search(r"(?i)\b(ARG|ENV)\s+\w*(token|secret|password|credential)", containerfile)
    assert not re.search(r"(?i)\b(cuda|torch|tensorflow|nvidia)\b", containerfile)


def test_workflow_state_is_bounded_and_task_local() -> None:
    # Given: both workflow definitions.
    workflows = _workflow_text("ci.yml") + _workflow_text("runtime-image.yml")

    # When: cache, cancellation, and timeout controls are inspected.
    # Then: every persistent path is repo-local and interrupted builds are bounded.
    assert "tmp" not in workflows.lower()
    assert ".cache/ci" in workflows
    assert ".cache/docker" in workflows
    assert ".cache/buildx" in workflows
    assert workflows.count("timeout-minutes: 30") == 2
    assert workflows.count("cancel-in-progress: true") == 2
