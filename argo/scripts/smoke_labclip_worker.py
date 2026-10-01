"""Code embedded in each Argo pod for the LabCLIP runtime smoke workflow."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any


EXPECTED_NODES = {"vis-lab", "ubuntu"}
STORE_BUCKETS = {
    "code": ("lab-code",),
    "ml-assets": ("lab-data", "lab-runs", "argo-artifacts"),
}
STORE_ENV_PREFIX = {"code": "CODE", "ml-assets": "ML_ASSETS"}


class WorkloadFailure(RuntimeError):
    def __init__(self, stage: str, error_type: str) -> None:
        self.stage = stage
        self.error_type = error_type


def exercise_cache_marker(cache_root: Path, run_id: str, node: str) -> dict[str, Any]:
    """Create, verify, and remove only this run's unique cache marker."""
    if not re.fullmatch(r"[0-9a-f]{32}", run_id) or node not in EXPECTED_NODES:
        raise ValueError("invalid smoke marker identity")
    marker = cache_root / f".labclip-runtime-smoke-{run_id}-{node}.marker"
    payload = f"labclip-runtime-smoke\n{run_id}\n{node}\n".encode("utf-8")
    created = False
    try:
        with marker.open("xb") as marker_file:
            created = True
            marker_file.write(payload)
        observed = marker.read_bytes()
        if observed != payload:
            raise RuntimeError("PVC marker readback did not match")
        return {
            "sha256": hashlib.sha256(observed).hexdigest(),
            "removed": True,
        }
    finally:
        if created:
            marker.unlink(missing_ok=True)


def exercise_cuda() -> str:
    import torch

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable")
    device = torch.device("cuda:0")
    left = torch.randn((1024, 1024), device=device)
    right = torch.randn((1024, 1024), device=device)
    product = torch.mm(left, right)
    torch.cuda.synchronize(device)
    if not torch.isfinite(product).all().item():
        raise RuntimeError("CUDA matrix multiplication returned non-finite values")
    return str(torch.cuda.get_device_name(device))


def exercise_s3_store(
    *, store: str, bucket: str, env_prefix: str, run_id: str, node: str, keep: bool
) -> dict[str, Any]:
    import boto3
    from botocore.config import Config
    from botocore.exceptions import ClientError

    access_key = os.environ[f"{env_prefix}_ACCESS_KEY"]
    secret_key = os.environ[f"{env_prefix}_SECRET_KEY"]
    endpoint = os.environ[f"{env_prefix}_ENDPOINT"]
    region = os.environ[f"{env_prefix}_REGION"]
    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=region,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(
            signature_version="s3v4",
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
            s3={"addressing_style": "path"},
        ),
    )
    key = f"infrastructure-smoke/{run_id}/{node}/{bucket}.txt"
    payload = f"labclip-runtime-smoke\n{run_id}\n{node}\n{store}\n{bucket}\n".encode(
        "utf-8"
    )
    expected_digest = hashlib.sha256(payload).hexdigest()
    head_metadata_verified = False
    head_content_length = None
    body_checksum_verified = False
    put_attempted = False
    marker_removed = False
    try:
        put_attempted = True
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=payload,
            Metadata={"sha256": expected_digest},
        )
        head = s3.head_object(Bucket=bucket, Key=key)
        metadata = {
            str(name).lower(): value
            for name, value in (head.get("Metadata") or {}).items()
        }
        head_content_length = head.get("ContentLength")
        if head_content_length != len(payload):
            raise RuntimeError("S3 marker HEAD content length did not match")
        if metadata.get("sha256") != expected_digest:
            raise RuntimeError("S3 marker HEAD SHA256 metadata did not match")
        head_metadata_verified = True

        response = s3.get_object(Bucket=bucket, Key=key)
        try:
            observed = response["Body"].read()
        finally:
            response["Body"].close()
        digest = hashlib.sha256(observed).hexdigest()
        if observed != payload:
            raise RuntimeError("S3 marker readback did not match")
        if digest != expected_digest:
            raise RuntimeError("S3 marker body SHA256 did not match")
        body_checksum_verified = True
    finally:
        if put_attempted and not keep:
            s3.delete_object(Bucket=bucket, Key=key)
            try:
                s3.head_object(Bucket=bucket, Key=key)
            except ClientError as error:
                status = (error.response.get("ResponseMetadata") or {}).get(
                    "HTTPStatusCode"
                )
                if status != 404:
                    raise
            else:
                raise RuntimeError("S3 marker still exists after delete")
            marker_removed = True

    retained_data = None
    if bucket != "argo-artifacts":
        try:
            retained_data = read_existing_object(s3, bucket)
        except ClientError as error:
            code = (error.response.get("Error") or {}).get("Code")
            status = (error.response.get("ResponseMetadata") or {}).get(
                "HTTPStatusCode"
            )
            if code not in {"AccessDenied", "Forbidden", "403"} and status != 403:
                raise
            retained_data = {"bucket": bucket, "status": "skipped_list_denied"}

    evidence: dict[str, Any] = {
        "store": store,
        "bucket": bucket,
        "sha256": digest,
        "metadata_sha256": expected_digest,
        "head_content_length": head_content_length,
        "head_metadata_verified": head_metadata_verified,
        "body_checksum_verified": body_checksum_verified,
        "marker_removed": marker_removed,
    }
    if keep:
        evidence["key"] = key
    if retained_data is not None:
        evidence["retained_data"] = retained_data
    return evidence


def read_existing_object(s3: Any, bucket: str) -> dict[str, Any]:
    """Read a bounded prefix from one existing, non-smoke object without changing it."""
    continuation_token = None
    while True:
        request: dict[str, Any] = {"Bucket": bucket, "MaxKeys": 1000}
        if continuation_token:
            request["ContinuationToken"] = continuation_token
        page = s3.list_objects_v2(**request)
        for item in page.get("Contents", []):
            key = str(item.get("Key", ""))
            if not key or key.startswith("infrastructure-smoke/"):
                continue
            if "Size" not in item:
                raise RuntimeError("S3 listing omitted object size")
            size = int(item["Size"])
            get_request: dict[str, Any] = {"Bucket": bucket, "Key": key}
            if size > 0:
                get_request["Range"] = "bytes=0-4095"
            response = s3.get_object(**get_request)
            try:
                prefix = response["Body"].read(4097)
            finally:
                response["Body"].close()
            if len(prefix) > 4096:
                raise RuntimeError("S3 endpoint returned more than 4096 bytes")
            return {
                "bucket": bucket,
                "status": "read",
                "key": key,
                "bytes_read": len(prefix),
                "sha256": hashlib.sha256(prefix).hexdigest(),
            }
        if not page.get("IsTruncated"):
            return {"bucket": bucket, "status": "skipped_empty"}
        continuation_token = page.get("NextContinuationToken")
        if not continuation_token:
            raise RuntimeError("truncated S3 listing omitted its continuation token")


def run() -> dict[str, Any]:
    stage = "identity"
    try:
        expected_node = os.environ["SMOKE_EXPECTED_NODE"]
        actual_node = os.environ["SMOKE_ACTUAL_NODE"]
        run_id = os.environ["SMOKE_RUN_ID"]
        keep = os.environ["SMOKE_KEEP_S3_MARKERS"].lower() == "true"
        if expected_node not in EXPECTED_NODES or actual_node != expected_node:
            raise ValueError("pod did not run on its expected node")

        stage = "cuda"
        gpu_name = exercise_cuda()

        stage = "pvc-marker"
        pvc_marker = exercise_cache_marker(Path("/cache"), run_id, expected_node)

        stage = "s3"
        s3_checks = []
        for store, buckets in STORE_BUCKETS.items():
            for bucket in buckets:
                s3_checks.append(
                    exercise_s3_store(
                        store=store,
                        bucket=bucket,
                        env_prefix=STORE_ENV_PREFIX[store],
                        run_id=run_id,
                        node=expected_node,
                        keep=keep,
                    )
                )

        return {
            "expected_node": expected_node,
            "node": actual_node,
            "gpu": gpu_name,
            "pvc_marker": pvc_marker,
            "s3": s3_checks,
        }
    except Exception as error:
        raise WorkloadFailure(stage, type(error).__name__) from None


def main() -> int:
    node = os.environ.get("SMOKE_EXPECTED_NODE", "unknown")
    try:
        evidence = run()
    except WorkloadFailure as error:
        print(
            "SMOKE_FAILURE "
            + json.dumps(
                {"node": node, "stage": error.stage, "error_type": error.error_type},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1

    evidence_text = json.dumps(evidence, separators=(",", ":"), sort_keys=True)
    Path("/tmp/smoke-evidence.json").write_text(evidence_text, encoding="utf-8")
    print(f"SMOKE_EVIDENCE {evidence_text}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
