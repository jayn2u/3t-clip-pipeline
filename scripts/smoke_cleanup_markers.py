#!/usr/bin/env python3
"""Delete only retained marker objects named by a successful smoke receipt."""

from __future__ import annotations

import argparse
import base64
import binascii
import importlib.util
import json
import os
import re
import select
import subprocess
import sys
import time
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator


NAMESPACE = "argo"
EXPECTED_NODES = ("vis-lab", "ubuntu")
STORE_SECRETS = {
    "code": (
        "minio-code-secret",
        "minio-code",
        "http://minio-code.argo.svc.cluster.local:9000",
        ("lab-code",),
    ),
    "ml-assets": (
        "minio-ml-assets-secret",
        "minio-ml-assets",
        "http://minio-ml-assets.argo.svc.cluster.local:9000",
        ("lab-data", "lab-runs", "argo-artifacts"),
    ),
}
RUN_ID_PATTERN = re.compile(r"[0-9a-f]{32}\Z")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


class CleanupError(RuntimeError):
    """A safe-to-print validation or cleanup error."""


@dataclass(frozen=True)
class Marker:
    run_id: str
    node: str
    store: str
    bucket: str
    key: str


def _workflow_parameter(workflow: dict[str, Any], name: str) -> str:
    parameters = (
        (workflow.get("spec") or {}).get("arguments") or {}
    ).get("parameters") or []
    if not isinstance(parameters, list):
        raise CleanupError("receipt workflow arguments are malformed")
    matches = [
        item.get("value")
        for item in parameters
        if isinstance(item, dict) and item.get("name") == name
    ]
    if len(matches) != 1 or not isinstance(matches[0], str):
        raise CleanupError(f"receipt must contain exactly one {name} workflow parameter")
    return matches[0]


def _evidence_records(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = ((workflow.get("status") or {}).get("nodes") or {})
    if not isinstance(nodes, dict):
        raise CleanupError("receipt has no workflow node outputs")
    records = []
    for node_status in nodes.values():
        if not isinstance(node_status, dict):
            continue
        outputs = (node_status or {}).get("outputs") or {}
        if not isinstance(outputs, dict):
            continue
        parameters = outputs.get("parameters") or []
        if not isinstance(parameters, list):
            continue
        for parameter in parameters:
            if not isinstance(parameter, dict):
                continue
            if parameter.get("name") != "smoke-evidence":
                continue
            try:
                record = json.loads(parameter.get("value", ""))
            except (TypeError, json.JSONDecodeError) as error:
                raise CleanupError("receipt contains malformed smoke evidence") from error
            if not isinstance(record, dict):
                raise CleanupError("receipt smoke evidence must be a JSON object")
            records.append(record)
    return records


def validate_receipt(workflow: dict[str, Any]) -> list[Marker]:
    """Validate the workflow and return its exact, generated S3 marker keys."""
    metadata = workflow.get("metadata") or {}
    status = workflow.get("status") or {}
    if not isinstance(metadata, dict) or not isinstance(status, dict):
        raise CleanupError("receipt workflow metadata or status is malformed")
    if workflow.get("kind") != "Workflow" or metadata.get("namespace") != NAMESPACE:
        raise CleanupError("receipt must be an Argo Workflow from namespace argo")
    if status.get("phase") != "Succeeded":
        raise CleanupError("receipt workflow did not reach Succeeded")
    run_id = _workflow_parameter(workflow, "run-id")
    keep_markers = _workflow_parameter(workflow, "keep-s3-markers")
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise CleanupError("receipt run-id is not a lowercase 32-character UUID")
    if keep_markers != "true":
        raise CleanupError("receipt does not confirm that S3 markers were retained")

    records = _evidence_records(workflow)
    if len(records) != len(EXPECTED_NODES):
        raise CleanupError("receipt must contain evidence from exactly two GPU nodes")
    if any(not isinstance(record.get("node"), str) for record in records):
        raise CleanupError("receipt node identities are malformed")
    by_node = {record.get("node"): record for record in records}
    if len(by_node) != len(records) or set(by_node) != set(EXPECTED_NODES):
        raise CleanupError("receipt nodes must be exactly vis-lab and ubuntu")

    expected_pairs = {
        (store, bucket)
        for store, (_, _, _, buckets) in STORE_SECRETS.items()
        for bucket in buckets
    }
    markers = []
    for node in EXPECTED_NODES:
        record = by_node[node]
        if record.get("expected_node") != node or record.get("node") != node:
            raise CleanupError(f"receipt node identity does not match {node}")
        checks = record.get("s3")
        if not isinstance(checks, list):
            raise CleanupError(f"receipt has no S3 checks for {node}")
        if any(not isinstance(check, dict) for check in checks):
            raise CleanupError(f"receipt has malformed S3 checks for {node}")
        observed_pairs = [(check.get("store"), check.get("bucket")) for check in checks]
        if any(
            not isinstance(store, str) or not isinstance(bucket, str)
            for store, bucket in observed_pairs
        ):
            raise CleanupError(f"receipt S3 store or bucket is malformed for {node}")
        if len(checks) != len(expected_pairs) or set(observed_pairs) != expected_pairs:
            raise CleanupError(f"receipt S3 stores and buckets are incomplete for {node}")
        if len(set(observed_pairs)) != len(observed_pairs):
            raise CleanupError(f"receipt has duplicate S3 checks for {node}")

        for check in checks:
            store = check["store"]
            bucket = check["bucket"]
            key = check.get("key")
            if check.get("marker_removed") is not False:
                raise CleanupError(f"receipt does not show a retained marker for {store}/{bucket}")
            if not isinstance(key, str) or not SHA256_PATTERN.fullmatch(
                str(check.get("sha256", ""))
            ):
                raise CleanupError(f"receipt key or checksum is malformed for {store}/{bucket}")
            expected_key = f"infrastructure-smoke/{run_id}/{node}/{bucket}.txt"
            if key != expected_key:
                raise CleanupError(f"receipt contains a key outside the generated marker path")
            markers.append(Marker(run_id, node, store, bucket, key))

    if len({marker.key for marker in markers}) != len(markers):
        raise CleanupError("receipt contains duplicate marker keys")
    return markers


def load_receipt(path: Path) -> tuple[dict[str, Any], list[Marker]]:
    try:
        workflow = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CleanupError("cannot read the workflow JSON receipt") from error
    if not isinstance(workflow, dict):
        raise CleanupError("workflow JSON receipt must be an object")
    return workflow, validate_receipt(workflow)


def _decode_secret_value(secret: dict[str, Any], key: str, secret_name: str) -> str:
    encoded = (secret.get("data") or {}).get(key)
    if not isinstance(encoded, str):
        raise CleanupError(f"Kubernetes Secret {secret_name} is missing key {key}")
    try:
        value = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (ValueError, binascii.Error, UnicodeDecodeError) as error:
        raise CleanupError(f"Kubernetes Secret {secret_name} key {key} is invalid") from error
    if not value:
        raise CleanupError(f"Kubernetes Secret {secret_name} key {key} is empty")
    return value


def _load_store_credentials(store: str) -> dict[str, str]:
    secret_name, _, expected_endpoint, _ = STORE_SECRETS[store]
    try:
        result = subprocess.run(
            ["kubectl", "get", "secret", secret_name, "-n", NAMESPACE, "-o", "json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        raise CleanupError(f"cannot retrieve Kubernetes Secret {secret_name}") from error
    if result.returncode != 0:
        raise CleanupError(f"cannot retrieve Kubernetes Secret {secret_name}")
    try:
        secret = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise CleanupError(f"Kubernetes Secret {secret_name} returned invalid JSON") from error
    metadata = secret.get("metadata") if isinstance(secret, dict) else None
    if not isinstance(metadata, dict) or metadata.get("name") != secret_name:
        raise CleanupError(f"Kubernetes Secret response did not match {secret_name}")
    if metadata.get("namespace") != NAMESPACE:
        raise CleanupError(f"Kubernetes Secret {secret_name} is outside namespace {NAMESPACE}")
    credentials = {
        key: _decode_secret_value(secret, key, secret_name)
        for key in ("access-key", "secret-key", "endpoint", "region")
    }
    if (
        credentials["endpoint"] != expected_endpoint
        or credentials["region"] != "garage"
    ):
        raise CleanupError(
            f"Kubernetes Secret {secret_name} does not match the LabCLIP endpoint contract"
        )
    return credentials


def _s3_client(credentials: dict[str, str], endpoint: str) -> Any:
    match = re.fullmatch(r"http://127\.0\.0\.1:(\d+)", endpoint)
    if match is None or not 1 <= int(match.group(1)) <= 65535:
        raise CleanupError("S3 cleanup endpoint must be a loopback port-forward")
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=credentials["region"],
        aws_access_key_id=credentials["access-key"],
        aws_secret_access_key=credentials["secret-key"],
        config=Config(
            signature_version="s3v4",
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
            s3={"addressing_style": "path"},
        ),
    )


def _wait_for_port_forward(process: subprocess.Popen[str], service: str) -> str:
    if process.stdout is None:
        raise CleanupError(f"port-forward for Service {service} has no status stream")
    ready_line = re.compile(r"Forwarding from 127\.0\.0\.1:(\d+) -> 9000\s*")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise CleanupError(f"port-forward for Service {service} exited before ready")
        readable, _, _ = select.select([process.stdout], [], [], 0.25)
        if not readable:
            continue
        line = process.stdout.readline()
        match = ready_line.fullmatch(line.rstrip("\n"))
        if match:
            return f"http://127.0.0.1:{match.group(1)}"
    raise CleanupError(f"port-forward for Service {service} did not become ready in 30s")


def _stop_port_forward(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    if process.stdout is not None:
        process.stdout.close()


@contextmanager
def _port_forward(service: str) -> Iterator[str]:
    allowed_services = {configuration[1] for configuration in STORE_SECRETS.values()}
    if service not in allowed_services:
        raise CleanupError("unsupported MinIO Service for port-forward")
    try:
        process = subprocess.Popen(
            [
                "kubectl",
                "port-forward",
                "--address=127.0.0.1",
                "--pod-running-timeout=20s",
                "-n",
                NAMESPACE,
                f"service/{service}",
                ":9000",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except OSError as error:
        raise CleanupError(f"cannot start port-forward for Service {service}") from error
    try:
        yield _wait_for_port_forward(process, service)
    finally:
        _stop_port_forward(process)


def delete_markers(markers: list[Marker]) -> None:
    from botocore.exceptions import ClientError

    credentials = {
        store: _load_store_credentials(store) for store in STORE_SECRETS
    }
    with ExitStack() as forwards:
        endpoints = {
            store: forwards.enter_context(_port_forward(configuration[1]))
            for store, configuration in STORE_SECRETS.items()
        }
        clients = {
            store: _s3_client(secret, endpoints[store])
            for store, secret in credentials.items()
        }
        verified = 0
        for marker in markers:
            client = clients[marker.store]
            try:
                client.delete_object(Bucket=marker.bucket, Key=marker.key)
                try:
                    client.head_object(Bucket=marker.bucket, Key=marker.key)
                except ClientError as error:
                    status = (error.response.get("ResponseMetadata") or {}).get(
                        "HTTPStatusCode"
                    )
                    if status != 404:
                        raise CleanupError(
                            f"delete verification failed for {marker.store}/{marker.bucket}"
                        ) from error
                else:
                    raise CleanupError(
                        f"marker still exists after deletion for {marker.store}/{marker.bucket}"
                    )
            except CleanupError:
                raise
            except Exception as error:
                raise CleanupError(
                    f"S3 cleanup failed at marker {verified + 1}/{len(markers)} "
                    f"({type(error).__name__}); rerun is safe with the same receipt"
                ) from None
            verified += 1
            print(
                f"DELETE_VERIFIED store={marker.store} bucket={marker.bucket} "
                f"key={marker.key} missing_after_delete=true"
            )


def _use_labclip_venv_if_needed() -> None:
    if importlib.util.find_spec("boto3") is not None:
        return
    python = (
        Path(__file__).resolve().parents[2]
        / "lab_clip"
        / ".venv"
        / "bin"
        / "python"
    )
    if not python.is_file() or not os.access(python, os.X_OK):
        raise CleanupError(
            "boto3 is unavailable; invoke with /mnt/data/lab_clip/.venv/bin/python"
        )
    probe = subprocess.run(
        [str(python), "-c", "import boto3"],
        check=False,
        capture_output=True,
        timeout=15,
    )
    if probe.returncode != 0:
        raise CleanupError("boto3 is unavailable in the LabCLIP virtual environment")
    os.execv(str(python), [str(python), str(Path(__file__).resolve()), *sys.argv[1:]])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow_json", type=Path, help="saved argo get -o json receipt")
    args = parser.parse_args()
    try:
        _, markers = load_receipt(args.workflow_json)
        _use_labclip_venv_if_needed()
        delete_markers(markers)
    except CleanupError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1
    except Exception as error:
        print(f"FAIL cleanup failed ({type(error).__name__})", file=sys.stderr)
        return 1
    print(f"PASS verified_deleted={len(markers)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
