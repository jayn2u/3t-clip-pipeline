from __future__ import annotations

import hashlib
import json
import os
from importlib import import_module
from types import ModuleType
from typing import Protocol, TypeGuard, runtime_checkable


class _S3Client(Protocol):
    def create_bucket(self, **kwargs: str) -> None: ...

    def put_object(self, **kwargs: str | bytes | dict[str, str]) -> None: ...


@runtime_checkable
class _Boto3Module(Protocol):
    def client(self, service_name: str, *, endpoint_url: str) -> _S3Client: ...


def _is_boto3_module(module: ModuleType) -> TypeGuard[_Boto3Module]:
    return isinstance(module, _Boto3Module)


bucket = os.environ["OUTPUT_BUCKET"]
prefix = os.environ["OUTPUT_PREFIX"]
workflow_uid = os.environ["WORKFLOW_UID"]
run_uid = os.environ["RUN_UID"]
result = b'{"message":"hello from isolated live"}\n'
digest = hashlib.sha256(result).hexdigest()
module = import_module("boto3")
if not _is_boto3_module(module):
    raise RuntimeError
client = module.client("s3", endpoint_url=os.environ["AWS_ENDPOINT_URL"])
client.create_bucket(Bucket=bucket)
client.put_object(
    Bucket=bucket, Key=f"{prefix}/result.json", Body=result, Metadata={"sha256": digest}
)
marker = {
    "objects": [{"key": f"{prefix}/result.json", "sha256": digest, "size": len(result)}],
    "runUid": run_uid,
    "schema": "committed/v1",
    "workflowUid": workflow_uid,
}
client.put_object(
    Bucket=bucket,
    Key=f"{prefix}/_COMMITTED.json",
    Body=(json.dumps(marker, separators=(",", ":"), sort_keys=True) + "\n").encode(),
    ContentType="application/json",
)
