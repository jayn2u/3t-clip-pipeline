#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["pydantic>=2.11,<3", "pyyaml>=6.0,<7", "typer>=0.16,<1"]
# ///
# ─── How to run ───
# .venv/bin/python scripts/image-metadata.py --help

"""Parse BuildKit image metadata and render reviewed image policy artifacts."""

import json
from pathlib import Path
from typing import Annotated, ClassVar, Final, Literal, final

import typer
import yaml
from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, ValidationError

RUNTIME_REPOSITORY: Final = "ghcr.io/jayn2u/3t-clip-pipeline-runtime"
DIGEST_PATTERN: Final = r"^sha256:[0-9a-f]{64}$"


class SelectedRuntimeMetadata(BaseModel):
    """Canonical runtime identity selected from a BuildKit envelope."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    envelope_digest: Annotated[str, Field(pattern=DIGEST_PATTERN)] = Field(alias="envelopeDigest")
    runtime_digest: Annotated[str, Field(pattern=DIGEST_PATTERN)] = Field(alias="runtimeDigest")


class _ImageLockBase(BaseModel):
    """Reviewed deployment image fields required by rendering."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="allow")

    schema_version: Literal[1] = Field(alias="schemaVersion")
    release_repository: Annotated[str, Field(pattern=rf"^{RUNTIME_REPOSITORY}$")] = Field(
        alias="releaseRepository"
    )


class ReviewedImageLock(_ImageLockBase):
    """Human-reviewed deployment image lock."""

    review_status: Literal["reviewed"] = Field(alias="reviewStatus")
    runtime_image: Annotated[str, Field(pattern=rf"^{RUNTIME_REPOSITORY}@{DIGEST_PATTERN[1:]}")] = (
        Field(alias="runtimeImage")
    )


class UnpromotedImageLock(_ImageLockBase):
    """Lock state that cannot select a deployable runtime image."""

    review_status: Literal["unpromoted"] = Field(alias="reviewStatus")
    runtime_image: None = Field(alias="runtimeImage")


ImageLock = ReviewedImageLock | UnpromotedImageLock
_IMAGE_LOCK_ADAPTER: Final[TypeAdapter[ImageLock]] = TypeAdapter(ImageLock)


class OCIPlatform(BaseModel):
    """OCI platform selector fields."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="allow")

    architecture: str
    os: str


class OCIManifestDescriptor(BaseModel):
    """OCI child descriptor used to distinguish runtime and attestation manifests."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="allow")

    media_type: Literal["application/vnd.oci.image.manifest.v1+json"] = Field(alias="mediaType")
    digest: Annotated[str, Field(pattern=DIGEST_PATTERN)]
    size: Annotated[int, Field(gt=0)]
    platform: OCIPlatform
    annotations: dict[str, str] = Field(default_factory=dict)

    @property
    def is_linux_amd64_runtime(self) -> bool:
        """Return whether this descriptor is the deployable runtime platform."""
        return (
            self.platform.os == "linux"
            and self.platform.architecture == "amd64"
            and self.annotations.get("vnd.docker.reference.type") != "attestation-manifest"
        )


class OCIIndex(BaseModel):
    """BuildKit OCI image index inspected from the pushed envelope."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="allow")

    schema_version: Literal[2] = Field(alias="schemaVersion")
    media_type: Literal["application/vnd.oci.image.index.v1+json"] = Field(alias="mediaType")
    manifests: tuple[OCIManifestDescriptor, ...]


@final
class RuntimeSelectionError(ValueError):
    """Raised when an OCI envelope has no unique deployable runtime manifest."""

    candidate_count: int

    def __init__(self, candidate_count: int) -> None:
        """Record the number of deployable manifests found."""
        self.candidate_count = candidate_count
        super().__init__(f"linux_amd64_runtime_count={candidate_count}")


app = typer.Typer(add_completion=False, no_args_is_help=True)


class ReleaseIdentity(BaseModel):
    """Canonical source identity for an immutable release tag."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    commit_sha: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]


def _load_json(path: Path) -> SelectedRuntimeMetadata:
    return SelectedRuntimeMetadata.model_validate_json(path.read_text(encoding="utf-8"))


def _load_lock(path: Path) -> ImageLock:
    return _IMAGE_LOCK_ADAPTER.validate_python(yaml.safe_load(path.read_text(encoding="utf-8")))


def _write_yaml(path: Path, document: dict[str, JsonValue]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(yaml.safe_dump(document, sort_keys=True), encoding="utf-8")


def _write_selected_metadata(path: Path, metadata: SelectedRuntimeMetadata) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(
        json.dumps(metadata.model_dump(by_alias=True), separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


@app.command("select-runtime")
def select_runtime(
    index: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    envelope_digest: Annotated[str, typer.Option()],
    output: Annotated[Path, typer.Option(dir_okay=False)],
    github_output: Annotated[Path | None, typer.Option(dir_okay=False)] = None,
) -> None:
    """Select the unique linux/amd64 runtime manifest from an OCI envelope."""
    envelope = OCIIndex.model_validate_json(index.read_text(encoding="utf-8"))
    candidates = tuple(
        manifest for manifest in envelope.manifests if manifest.is_linux_amd64_runtime
    )
    if len(candidates) != 1:
        raise RuntimeSelectionError(len(candidates))
    selected = SelectedRuntimeMetadata(
        envelopeDigest=envelope_digest,
        runtimeDigest=candidates[0].digest,
    )
    _write_selected_metadata(output, selected)
    if github_output is not None:
        github_output.parent.mkdir(parents=True, exist_ok=True)
        with github_output.open("a", encoding="utf-8") as output_stream:
            _ = output_stream.write(f"runtime-digest={selected.runtime_digest}\n")


@app.command()
def propose(
    metadata: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    commit_sha: Annotated[str, typer.Option()],
    output: Annotated[Path, typer.Option(dir_okay=False)],
) -> None:
    """Create an unapproved lock proposal from BuildKit's digest output."""
    identity = ReleaseIdentity(commit_sha=commit_sha)
    build = _load_json(metadata)
    _write_yaml(
        output,
        {
            "autoUpdate": False,
            "immutableTag": f"sha-{identity.commit_sha}",
            "reviewStatus": "proposed",
            "envelopeDigest": build.envelope_digest,
            "runtimeImage": f"{RUNTIME_REPOSITORY}@{build.runtime_digest}",
            "schemaVersion": 1,
            "sourceCommit": identity.commit_sha,
        },
    )


@app.command("render-configmap")
def render_configmap(
    lock: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    output: Annotated[Path, typer.Option(dir_okay=False)],
) -> None:
    """Render the Kustomize-owned runtime image ConfigMap from its lock."""
    image_lock = _load_lock(lock)
    match image_lock:
        case UnpromotedImageLock():
            output.parent.mkdir(parents=True, exist_ok=True)
            _ = output.write_text("---\n", encoding="utf-8")
        case ReviewedImageLock():
            _write_yaml(
                output,
                {
                    "apiVersion": "v1",
                    "data": {"runtimeImage": image_lock.runtime_image},
                    "kind": "ConfigMap",
                    "metadata": {
                        "name": "three-t-runtime-image",
                        "namespace": "three-t-pipeline",
                    },
                },
            )


def main() -> None:
    """Run the image metadata policy boundary with stable failure output."""
    try:
        app(standalone_mode=False)
    except (OSError, ValueError, ValidationError, yaml.YAMLError) as error:
        typer.echo(f"IMAGE_METADATA_INVALID {error}", err=True)
        raise SystemExit(2) from error


if __name__ == "__main__":
    main()
