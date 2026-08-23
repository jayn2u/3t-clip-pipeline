"""Platform-image runtime command entrypoints."""

from __future__ import annotations

import hashlib
import os
from enum import StrEnum, unique
from importlib import import_module
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Annotated,
    Protocol,
    TypedDict,
    TypeGuard,
    final,
    runtime_checkable,
)
from urllib.parse import urlsplit

import typer
from pydantic import StrictStr, TypeAdapter

from three_t_clip_pipeline.runtime.lifecycle import (
    CACHE_MOUNT_PATH,
    INPUT_MOUNT_PATH,
    OUTPUT_MOUNT_PATH,
    FinalizeRequest,
    InitRequest,
    PublisherRequest,
    finalize_results,
    initialize_bundle,
    run_publisher_sidecar,
)
from three_t_clip_pipeline.runtime.status import (
    CacheObjectPayload,
    FinalizePayload,
    InitPayload,
    MainStatus,
    MarkerResult,
    ObjectRecord,
    PublisherPayload,
    RuntimeModel,
)

if TYPE_CHECKING:
    from types import ModuleType

runtime_app = typer.Typer(
    help="Run platform init, sidecar, and finalizer roles.", no_args_is_help=True
)


@unique
class InitPhase(StrEnum):
    """Rendered platform initialization phases."""

    PREFLIGHT = "preflight"
    ACQUIRE_PROFILE = "acquire-profile"
    PREPARE_CACHE = "prepare-cache"


class _Readable(Protocol):
    def read(self) -> bytes: ...


class _HeadResponse(TypedDict):
    ContentLength: int
    Metadata: dict[str, str]


class _GetResponse(TypedDict):
    Body: _Readable


class _S3Client(Protocol):
    def download_file(self, bucket: str, key: str, destination: str) -> None: ...
    def put_object(self, **kwargs: str | bytes | dict[str, str]) -> None: ...
    def head_object(self, **kwargs: str) -> _HeadResponse: ...
    def get_object(self, **kwargs: str) -> _GetResponse: ...


@runtime_checkable
class _Boto3Module(Protocol):
    def client(self, service_name: str) -> _S3Client: ...


def _is_boto3_module(module: ModuleType) -> TypeGuard[_Boto3Module]:
    return isinstance(module, _Boto3Module)


@final
class S3RuntimeStore:
    """Bounded S3 adapter used only by platform-owned runtime images."""

    def __init__(self, bucket: str, client: _S3Client) -> None:
        """Bind all runtime writes to one configured bucket."""
        self._bucket = bucket
        self._client = client

    def download_verified(self, source_uri: str, destination: Path, sha256: str) -> ObjectRecord:
        """Download one S3 object and promote it only after checksum verification."""
        parsed = urlsplit(source_uri)
        if parsed.scheme != "s3" or not parsed.netloc or not parsed.path.lstrip("/"):
            message = "runtime source must be an S3 object URI"
            raise ValueError(message)
        temporary = destination.with_name(f".{destination.name}.partial")
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._client.download_file(parsed.netloc, parsed.path.lstrip("/"), str(temporary))
        content = temporary.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if digest != sha256:
            temporary.unlink()
            message = "downloaded object checksum mismatch"
            raise ValueError(message)
        _ = temporary.replace(destination)
        return ObjectRecord(key=destination.as_posix(), size=len(content), sha256=digest)

    def upload_immutable(self, source: Path, key: str) -> ObjectRecord:
        """Conditionally upload one immutable payload object."""
        content = source.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=content,
            Metadata={"sha256": digest},
            IfNoneMatch="*",
        )
        return ObjectRecord(key=key, size=len(content), sha256=digest)

    def put_manifest(self, key: str, payload: bytes) -> None:
        """Conditionally upload one immutable runtime manifest."""
        self._client.put_object(Bucket=self._bucket, Key=key, Body=payload, IfNoneMatch="*")

    def get_manifest(self, key: str) -> bytes:
        """Read one staged runtime manifest."""
        return self._client.get_object(Bucket=self._bucket, Key=key)["Body"].read()

    def head(self, key: str) -> ObjectRecord:
        """Read immutable object size and checksum metadata."""
        response = self._client.head_object(Bucket=self._bucket, Key=key)
        digest = response["Metadata"].get("sha256")
        if digest is None:
            message = "immutable object is missing sha256 metadata"
            raise ValueError(message)
        return ObjectRecord(key=key, size=response["ContentLength"], sha256=digest)

    def create_marker(self, key: str, payload: bytes) -> MarkerResult:
        """Create the marker with an S3 conditional write and never overwrite."""
        self._client.put_object(Bucket=self._bucket, Key=key, Body=payload, IfNoneMatch="*")
        return MarkerResult.CREATED


def _load[T: RuntimeModel](path: Path, model: type[T]) -> T:
    return model.model_validate_json(path.read_bytes())


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if value is None:
        message = f"required runtime environment is missing: {name}"
        raise RuntimeError(message)
    return value


def _init_from_environment() -> InitPayload:
    cache_adapter: TypeAdapter[tuple[CacheObjectPayload, ...]] = TypeAdapter(
        tuple[CacheObjectPayload, ...]
    )
    return InitPayload(
        bucket=_required_environment("OUTPUT_BUCKET"),
        runPrefix=_required_environment("OUTPUT_PREFIX"),
        runUid=_required_environment("RUN_UID"),
        bundleUri=_required_environment("BUNDLE_S3_URI"),
        bundleSha256=_required_environment("BUNDLE_SHA256"),
        cacheObjects=cache_adapter.validate_json(_required_environment("CACHE_MAPPINGS_JSON")),
    )


def _publisher_from_environment() -> PublisherPayload:
    paths_adapter: TypeAdapter[tuple[StrictStr, ...]] = TypeAdapter(tuple[StrictStr, ...])
    return PublisherPayload(
        bucket=_required_environment("OUTPUT_BUCKET"),
        runPrefix=_required_environment("OUTPUT_PREFIX"),
        runUid=_required_environment("RUN_UID"),
        requiredPaths=paths_adapter.validate_json(_required_environment("REQUIRED_PATHS_JSON")),
        publicationFinalRetrySeconds=int(_required_environment("PUBLICATION_FINAL_RETRY_SECONDS")),
        terminationGraceSeconds=int(_required_environment("TERMINATION_GRACE_SECONDS")),
    )


def _finalize_from_environment() -> FinalizePayload:
    paths_adapter: TypeAdapter[tuple[StrictStr, ...]] = TypeAdapter(tuple[StrictStr, ...])
    return FinalizePayload(
        bucket=_required_environment("OUTPUT_BUCKET"),
        runPrefix=_required_environment("OUTPUT_PREFIX"),
        runUid=_required_environment("RUN_UID"),
        mainStatus=MainStatus(_required_environment("WORKFLOW_STATUS")),
        requiredPaths=paths_adapter.validate_json(_required_environment("REQUIRED_PATHS_JSON")),
    )


def _store(bucket: str) -> S3RuntimeStore:
    module = import_module("boto3")
    if not _is_boto3_module(module):
        message = "boto3 module does not expose the required S3 client boundary"
        raise RuntimeError(message)
    return S3RuntimeStore(bucket, module.client("s3"))


@runtime_app.command("init")
def runtime_init(
    request_json: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
    phase: Annotated[InitPhase | None, typer.Option()] = None,
) -> None:
    """Verify and hydrate immutable bundle/cache inputs, then upload init.json."""
    if phase is not None:
        return
    payload = _init_from_environment() if request_json is None else _load(request_json, InitPayload)
    request = InitRequest(
        run_prefix=payload.run_prefix,
        run_uid=payload.run_uid,
        bundle_uri=payload.bundle_uri,
        bundle_sha256=payload.bundle_sha256,
        cache_objects=tuple(
            (item.s3_uri, item.sha256, item.destination) for item in payload.cache_objects
        ),
        input_root=Path(INPUT_MOUNT_PATH),
        cache_root=Path(CACHE_MOUNT_PATH),
    )
    _ = initialize_bundle(request, _store(payload.bucket))


@runtime_app.command("publisher")
def runtime_publisher(
    request_json: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
) -> None:
    """Run the native sidecar publisher until completion or bounded TERM."""
    payload = (
        _publisher_from_environment()
        if request_json is None
        else _load(request_json, PublisherPayload)
    )
    request = PublisherRequest(
        run_prefix=payload.run_prefix,
        run_uid=payload.run_uid,
        output_root=Path(OUTPUT_MOUNT_PATH),
        required_paths=payload.required_paths,
        publication_final_retry_seconds=payload.publication_final_retry_seconds,
        termination_grace_seconds=payload.termination_grace_seconds,
    )
    _ = run_publisher_sidecar(request, _store(payload.bucket))


@runtime_app.command("finalize")
def runtime_finalize(
    request_json: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
) -> None:
    """Verify stable workflow publication state and conditionally create the marker."""
    payload = (
        _finalize_from_environment()
        if request_json is None
        else _load(request_json, FinalizePayload)
    )
    outcome = finalize_results(
        FinalizeRequest(
            run_prefix=payload.run_prefix,
            run_uid=payload.run_uid,
            main_status=payload.main_status,
            required_paths=payload.required_paths,
        ),
        _store(payload.bucket),
    )
    if outcome in {MarkerResult.REFUSED, MarkerResult.COLLISION}:
        raise typer.Exit(code=1)
