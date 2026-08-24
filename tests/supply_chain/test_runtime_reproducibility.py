import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from pydantic import JsonValue, TypeAdapter

ROOT = Path(__file__).parents[2]
IMAGES_LOCK = ROOT / "deploy/images.lock.yaml"
METADATA_POLICY = ROOT / "scripts/image-metadata.py"
RUNTIME_REPOSITORY = "ghcr.io/jayn2u/3t-clip-pipeline-runtime"
MAPPING_ADAPTER: TypeAdapter[dict[str, JsonValue]] = TypeAdapter(dict[str, JsonValue])


def _mapping(path: Path) -> dict[str, JsonValue]:
    return MAPPING_ADAPTER.validate_python(yaml.safe_load(path.read_text(encoding="utf-8")))


def _run_metadata(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(METADATA_POLICY), *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def _manifest(digest: str, *, attestation: bool = False) -> dict[str, JsonValue]:
    document: dict[str, JsonValue] = {
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "digest": digest,
        "size": 1,
        "platform": {"architecture": "amd64", "os": "linux"},
    }
    if attestation:
        document["annotations"] = {"vnd.docker.reference.type": "attestation-manifest"}
    return document


def _write_index(path: Path, manifests: list[dict[str, JsonValue]]) -> None:
    _ = path.write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "mediaType": "application/vnd.oci.image.index.v1+json",
                "manifests": manifests,
            }
        ),
        encoding="utf-8",
    )


def test_runtime_build_omits_generated_bytecode_and_account_creation() -> None:
    # Given: the runtime context allowlist and image construction instructions.
    containerfile = (ROOT / "Containerfile.runtime").read_text(encoding="utf-8")
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

    # When: nondeterministic generated files and account databases are considered.
    # Then: neither can contribute timestamp-varying bytes to a runtime layer.
    assert dockerignore[-2:] == ["src/**/__pycache__/", "src/**/*.py[co]"]
    assert "UV_COMPILE_BYTECODE" not in containerfile
    assert "groupadd" not in containerfile
    assert "useradd" not in containerfile
    assert containerfile.count("ARG SOURCE_DATE_EPOCH") == 2
    assert "-name uv_cache.json -delete" in containerfile
    assert "find /runtime-root -exec touch -h" in containerfile
    for workflow in ("ci.yml", "runtime-image.yml"):
        text = (ROOT / ".github/workflows" / workflow).read_text(encoding="utf-8")
        assert text.count('SOURCE_DATE_EPOCH: "0"') == 1
    assert "USER 65532:65532" in containerfile


def test_runtime_selector_chooses_one_linux_amd64_non_attestation_manifest(
    tmp_path: Path,
) -> None:
    # Given: a realistic OCI index with one runtime manifest and two attestations.
    runtime_digest = "sha256:" + "1" * 64
    envelope_digest = "sha256:" + "e" * 64
    index = tmp_path / "index.json"
    output = tmp_path / "metadata.json"
    _write_index(
        index,
        [
            _manifest(runtime_digest),
            _manifest("sha256:" + "a" * 64, attestation=True),
            _manifest("sha256:" + "b" * 64, attestation=True),
        ],
    )

    # When: the typed metadata boundary selects the deployable platform manifest.
    result = _run_metadata(
        "select-runtime",
        "--index",
        str(index),
        "--envelope-digest",
        envelope_digest,
        "--output",
        str(output),
    )

    # Then: only the linux/amd64 child is canonical, never its outer envelope.
    assert result.returncode == 0, result.stderr
    assert _mapping(output) == {
        "envelopeDigest": envelope_digest,
        "runtimeDigest": runtime_digest,
    }


def test_committed_reviewed_lock_renders_approved_runtime_digest(tmp_path: Path) -> None:
    # Given: the committed image lock carries the independently approved digest.
    lock = _mapping(IMAGES_LOCK)
    output = tmp_path / "runtime-image.yaml"
    runtime_image = (
        "ghcr.io/jayn2u/3t-clip-pipeline-runtime@sha256:"
        "9ba8cd5a2a1d8c6882edfe80ab10c0df4d0c7ca320626e8aed74344367eafe42"
    )

    # When: its optional platform fragment is rendered.
    result = _run_metadata("render-configmap", "--lock", str(IMAGES_LOCK), "--output", str(output))

    # Then: the reviewed lock renders exactly its approved immutable image.
    assert lock["reviewStatus"] == "reviewed"
    assert lock["runtimeImage"] == runtime_image
    assert result.returncode == 0, result.stderr
    document = _mapping(output)
    assert document["data"] == {"runtimeImage": runtime_image}


@pytest.mark.parametrize(
    "manifests",
    [
        [],
        [_manifest("sha256:" + "a" * 64), _manifest("sha256:" + "b" * 64)],
        [_manifest("sha256:" + "a" * 64, attestation=True)],
        [_manifest("sha256:invalid")],
    ],
    ids=["missing", "ambiguous", "attestation_only", "malformed_digest"],
)
def test_runtime_selector_rejects_noncanonical_indexes(
    tmp_path: Path,
    manifests: list[dict[str, JsonValue]],
) -> None:
    # Given: an OCI index without exactly one valid deployable platform manifest.
    index = tmp_path / "index.json"
    output = tmp_path / "metadata.json"
    _write_index(index, manifests)

    # When: the index crosses the typed selection boundary.
    result = _run_metadata(
        "select-runtime",
        "--index",
        str(index),
        "--envelope-digest",
        "sha256:" + "e" * 64,
        "--output",
        str(output),
    )

    # Then: selection fails closed without leaving partial metadata.
    assert result.returncode == 2
    assert "IMAGE_METADATA_INVALID" in result.stderr
    assert not output.exists()


def test_reviewed_lock_renders_exact_runtime_digest(tmp_path: Path) -> None:
    # Given: a separate reviewed fixture carrying one immutable runtime digest.
    digest = "sha256:9ba8cd5a2a1d8c6882edfe80ab10c0df4d0c7ca320626e8aed74344367eafe42"
    lock = tmp_path / "reviewed-lock.yaml"
    output = tmp_path / "runtime-image.yaml"
    _ = lock.write_text(
        yaml.safe_dump(
            {
                "schemaVersion": 1,
                "releaseRepository": RUNTIME_REPOSITORY,
                "reviewStatus": "reviewed",
                "runtimeImage": f"{RUNTIME_REPOSITORY}@{digest}",
            }
        ),
        encoding="utf-8",
    )

    # When: the reviewed fixture is rendered.
    result = _run_metadata("render-configmap", "--lock", str(lock), "--output", str(output))

    # Then: the ConfigMap contains exactly the reviewed image by digest.
    assert result.returncode == 0, result.stderr
    document = _mapping(output)
    assert document["data"] == {"runtimeImage": f"{RUNTIME_REPOSITORY}@{digest}"}
