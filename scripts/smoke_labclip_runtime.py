#!/usr/bin/env python3
"""Run a bounded Argo smoke workflow against both LabCLIP GPU nodes."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import yaml


NAMESPACE = "argo"
SERVICE_ACCOUNT = "labclip-runner"
IMAGE = (
    "ghcr.io/jayn2u/labclip:0.0.2"
    "@sha256:a12c5555f33c9da84ea5d5049872b1ba95f0acf3aaf4719ba6725a9639af4d4d"
)
NODES = (
    ("vis-lab", "labclip-cache-vis-lab"),
    ("ubuntu", "labclip-cache-ubuntu"),
)
STORE_BUCKETS = {
    "code": ("lab-code",),
    "ml-assets": ("lab-data", "lab-runs", "argo-artifacts"),
}
RETAINED_DATA_BUCKETS = ("lab-code", "lab-data", "lab-runs")
WORKER_PATH = Path(__file__).with_name("smoke_labclip_worker.py")


class SmokeError(RuntimeError):
    """An actionable smoke-run failure without exposing command output."""


def build_workflow(
    run_id: str, *, keep_s3_markers: bool = False, worker_source: str | None = None
) -> dict[str, Any]:
    """Build the one-shot Workflow manifest used by both nodes."""
    if not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise ValueError("run_id must be a lowercase UUID hex string")

    source = (
        worker_source
        if worker_source is not None
        else WORKER_PATH.read_text(encoding="utf-8")
    )
    workflow_name = f"labclip-runtime-smoke-{run_id[:16]}"
    dag_tasks = []
    for node, claim in NODES:
        dag_tasks.append(
            {
                "name": node,
                "template": "gpu-smoke",
                "arguments": {
                    "parameters": [
                        {"name": "expected-node", "value": node},
                        {"name": "cache-claim", "value": claim},
                    ]
                },
            }
        )

    secret_env = []
    for store, secret in (
        ("CODE", "minio-code-secret"),
        ("ML_ASSETS", "minio-ml-assets-secret"),
    ):
        for field, secret_key in (
            ("ACCESS_KEY", "access-key"),
            ("SECRET_KEY", "secret-key"),
            ("ENDPOINT", "endpoint"),
            ("REGION", "region"),
        ):
            secret_env.append(
                {
                    "name": f"{store}_{field}",
                    "valueFrom": {
                        "secretKeyRef": {"name": secret, "key": secret_key}
                    },
                }
            )

    return {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Workflow",
        "metadata": {
            "name": workflow_name,
            "namespace": NAMESPACE,
            "labels": {"app.kubernetes.io/name": "labclip-runtime-smoke"},
        },
        "spec": {
            "serviceAccountName": SERVICE_ACCOUNT,
            "entrypoint": "both-nodes",
            "arguments": {
                "parameters": [
                    {"name": "run-id", "value": run_id},
                    {
                        "name": "keep-s3-markers",
                        "value": str(keep_s3_markers).lower(),
                    },
                ]
            },
            "imagePullSecrets": [{"name": "ghcr-secret"}],
            "podSpecPatch": json.dumps({"runtimeClassName": "nvidia"}),
            "templates": [
                {
                    "name": "both-nodes",
                    "dag": {"tasks": dag_tasks},
                },
                {
                    "name": "gpu-smoke",
                    "inputs": {
                        "parameters": [
                            {"name": "expected-node"},
                            {"name": "cache-claim"},
                        ]
                    },
                    "nodeSelector": {
                        "kubernetes.io/hostname": "{{inputs.parameters.expected-node}}"
                    },
                    "volumes": [
                        {
                            "name": "labclip-cache",
                            "persistentVolumeClaim": {
                                "claimName": "{{inputs.parameters.cache-claim}}"
                            },
                        }
                    ],
                    "script": {
                        "image": IMAGE,
                        "command": ["python"],
                        "source": source,
                        "resources": {
                            "requests": {"nvidia.com/gpu": "1"},
                            "limits": {"nvidia.com/gpu": "1"},
                        },
                        "volumeMounts": [
                            {"name": "labclip-cache", "mountPath": "/cache"}
                        ],
                        "env": [
                            {
                                "name": "SMOKE_RUN_ID",
                                "value": "{{workflow.parameters.run-id}}",
                            },
                            {
                                "name": "SMOKE_EXPECTED_NODE",
                                "value": "{{inputs.parameters.expected-node}}",
                            },
                            {
                                "name": "SMOKE_ACTUAL_NODE",
                                "valueFrom": {
                                    "fieldRef": {"fieldPath": "spec.nodeName"}
                                },
                            },
                            {
                                "name": "SMOKE_KEEP_S3_MARKERS",
                                "value": "{{workflow.parameters.keep-s3-markers}}",
                            },
                            *secret_env,
                        ],
                    },
                    "outputs": {
                        "parameters": [
                            {
                                "name": "smoke-evidence",
                                "valueFrom": {"path": "/tmp/smoke-evidence.json"},
                            }
                        ]
                    },
                },
            ],
        },
    }


def _run_argo(
    arguments: list[str], *, stdin: str | None = None
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            ["argo", *arguments],
            input=stdin,
            check=False,
            capture_output=True,
            text=True,
            timeout=35,
        )
    except FileNotFoundError as error:
        raise SmokeError("argo CLI is not installed or is not on PATH") from error
    except subprocess.TimeoutExpired as error:
        raise SmokeError("argo CLI request exceeded 35 seconds") from error
    if result.returncode != 0:
        raise SmokeError(f"argo {arguments[0]} failed with exit code {result.returncode}")
    return result


def _workflow_json(name: str) -> dict[str, Any]:
    result = _run_argo(
        [
            "get",
            "-n",
            NAMESPACE,
            "-o",
            "json",
            "--request-timeout",
            "30s",
            name,
        ]
    )
    try:
        workflow = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise SmokeError("argo get returned invalid JSON") from error
    if not isinstance(workflow, dict):
        raise SmokeError("argo get returned an unexpected response")
    return workflow


def _wait_for_terminal_workflow(
    name: str, *, timeout_seconds: int, poll_interval_seconds: int
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_phase = "unknown"
    last_error: SmokeError | None = None
    while time.monotonic() < deadline:
        try:
            workflow = _workflow_json(name)
            last_error = None
        except SmokeError as error:
            last_error = error
            workflow = None

        if workflow is not None:
            last_phase = str((workflow.get("status") or {}).get("phase") or "Pending")
            if last_phase in {"Succeeded", "Failed", "Error"}:
                return workflow
        time.sleep(min(poll_interval_seconds, max(0, deadline - time.monotonic())))

    detail = f"; last get error: {last_error}" if last_error else ""
    raise SmokeError(
        f"workflow {name} did not finish within {timeout_seconds}s "
        f"(last phase {last_phase}){detail}; inspect it with argo get -n {NAMESPACE} {name}"
    )


def _extract_evidence(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    records = []
    nodes = (workflow.get("status") or {}).get("nodes") or {}
    if not isinstance(nodes, dict):
        raise SmokeError("workflow status has no node evidence")
    for node_status in nodes.values():
        outputs = (node_status or {}).get("outputs") or {}
        for parameter in outputs.get("parameters") or []:
            if parameter.get("name") != "smoke-evidence":
                continue
            try:
                record = json.loads(parameter.get("value", ""))
            except json.JSONDecodeError as error:
                raise SmokeError("workflow returned malformed smoke evidence") from error
            if isinstance(record, dict):
                records.append(record)
    return records


def _validate_evidence(
    records: list[dict[str, Any]], *, keep_s3_markers: bool, run_id: str
) -> list[dict[str, Any]]:
    if not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise SmokeError("workflow evidence run-id is invalid")
    if len(records) != len(NODES):
        raise SmokeError(
            f"workflow returned {len(records)} node evidence records; expected {len(NODES)}"
        )
    by_node = {record.get("node"): record for record in records}
    expected_nodes = {node for node, _ in NODES}
    if set(by_node) != expected_nodes:
        raise SmokeError(
            "workflow succeeded without evidence from both target nodes "
            f"(received {', '.join(sorted(str(node) for node in by_node)) or 'none'})"
        )

    expected_buckets = {
        (store, bucket)
        for store, buckets in STORE_BUCKETS.items()
        for bucket in buckets
    }
    ordered_records = []
    for node, _ in NODES:
        record = by_node[node]
        if record.get("expected_node") != node or not record.get("gpu"):
            raise SmokeError(f"workflow evidence is incomplete for node {node}")
        marker = record.get("pvc_marker") or {}
        if not marker.get("removed") or not re.fullmatch(
            r"[0-9a-f]{64}", str(marker.get("sha256", ""))
        ):
            raise SmokeError(f"PVC marker validation failed on node {node}")

        node_checks = record.get("s3")
        if not isinstance(node_checks, list):
            raise SmokeError(f"S3 checks are missing for node {node}")
        s3_checks = {}
        for check in node_checks:
            if not isinstance(check, dict):
                raise SmokeError(f"S3 checks are malformed for node {node}")
            pair = (check.get("store"), check.get("bucket"))
            if pair in s3_checks:
                raise SmokeError(f"duplicate S3 check for {pair[0]}/{pair[1]} on {node}")
            s3_checks[pair] = check
        if set(s3_checks) != expected_buckets:
            raise SmokeError(f"S3 bucket checks are incomplete on node {node}")
        for (store, bucket), check in s3_checks.items():
            expected_removed = not keep_s3_markers
            if check.get("marker_removed") is not expected_removed:
                raise SmokeError(f"S3 marker cleanup failed for {store}/{bucket} on {node}")
            expected_payload = (
                f"labclip-runtime-smoke\n{run_id}\n{node}\n{store}\n{bucket}\n"
            ).encode("utf-8")
            expected_digest = hashlib.sha256(expected_payload).hexdigest()
            if (
                check.get("head_metadata_verified") is not True
                or check.get("body_checksum_verified") is not True
                or check.get("metadata_sha256") != check.get("sha256")
                or not re.fullmatch(r"[0-9a-f]{64}", str(check.get("sha256", "")))
                or check.get("sha256") != expected_digest
            ):
                raise SmokeError(f"S3 checksum evidence is invalid for {store}/{bucket} on {node}")
            content_length = check.get("head_content_length")
            if type(content_length) is not int or content_length != len(expected_payload):
                raise SmokeError(f"S3 HEAD content length is invalid for {store}/{bucket} on {node}")
            expected_key = f"infrastructure-smoke/{run_id}/{node}/{bucket}.txt"
            if keep_s3_markers and check.get("key") != expected_key:
                raise SmokeError(f"kept S3 marker key is invalid for {store}/{bucket} on {node}")
            if not keep_s3_markers and check.get("key") is not None:
                raise SmokeError(f"removed S3 marker key should not be retained for {store}/{bucket} on {node}")

        retained_data = {}
        for (_, bucket), check in s3_checks.items():
            item = check.get("retained_data")
            if bucket in RETAINED_DATA_BUCKETS:
                if not isinstance(item, dict) or item.get("bucket") != bucket:
                    raise SmokeError(f"retained-data evidence is missing for {bucket} on {node}")
                if bucket in retained_data:
                    raise SmokeError(f"duplicate retained-data evidence for {bucket} on {node}")
                retained_data[bucket] = item
            elif item is not None:
                raise SmokeError(f"unexpected retained-data evidence for {bucket} on {node}")
        if set(retained_data) != set(RETAINED_DATA_BUCKETS):
            raise SmokeError(f"retained-data checks are incomplete on node {node}")
        for bucket, item in retained_data.items():
            status = item.get("status")
            if status == "read":
                if (
                    not item.get("key")
                    or str(item["key"]).startswith("infrastructure-smoke/")
                    or not isinstance(item.get("bytes_read"), int)
                    or not 0 <= item["bytes_read"] <= 4096
                    or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256", "")))
                ):
                    raise SmokeError(f"retained-data evidence is invalid for {bucket} on {node}")
            elif status not in {"skipped_empty", "skipped_list_denied"}:
                raise SmokeError(f"retained-data status is invalid for {bucket} on {node}")
        ordered_records.append(record)
    return ordered_records


def _workflow_parameter_value(workflow: dict[str, Any], name: str) -> str:
    parameters = (
        (workflow.get("spec") or {}).get("arguments") or {}
    ).get("parameters") or []
    matches = [
        parameter.get("value")
        for parameter in parameters
        if isinstance(parameter, dict) and parameter.get("name") == name
    ]
    if len(matches) != 1 or not isinstance(matches[0], str):
        raise SmokeError(f"workflow receipt is missing parameter {name}")
    return matches[0]


def _retained_data_checks(record: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        check["retained_data"]
        for check in record["s3"]
        if isinstance(check.get("retained_data"), dict)
    ]


def _print_success(
    name: str,
    records: list[dict[str, Any]],
    *,
    keep_s3_markers: bool,
    show_details: bool = True,
) -> None:
    s3_total = sum(len(record["s3"]) for record in records)
    retained_reads = sum(
        item.get("status") == "read"
        for record in records
        for item in _retained_data_checks(record)
    )
    marker_state = "retained" if keep_s3_markers else "removed"
    print(
        f"PASS workflow={name} phase=Succeeded nodes=vis-lab,ubuntu "
        f"gpu=2/2 pvc-markers=2/2-removed s3-markers={s3_total}/{s3_total}-{marker_state} "
        f"retained-data-reads={retained_reads}/6"
    )
    if not show_details:
        return
    if keep_s3_markers:
        for record in records:
            for check in record["s3"]:
                print(
                    f"S3_MARKER node={record['node']} store={check['store']} "
                    f"bucket={check['bucket']} key={check['key']} sha256={check['sha256']}"
                )
    for record in records:
        for item in _retained_data_checks(record):
            if item["status"] == "read":
                print(
                    f"S3_DATA node={record['node']} bucket={item['bucket']} "
                    f"key={item['key']} bytes={item['bytes_read']} sha256={item['sha256']}"
                )
            else:
                print(
                    f"S3_DATA node={record['node']} bucket={item['bucket']} "
                    f"status={item['status']}"
                )


def validate_workflow_receipt(path: Path) -> str:
    try:
        workflow = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SmokeError("cannot read workflow receipt") from error
    if not isinstance(workflow, dict):
        raise SmokeError("workflow receipt must be a JSON object")
    metadata = workflow.get("metadata") or {}
    status = workflow.get("status") or {}
    if (
        workflow.get("kind") != "Workflow"
        or not isinstance(metadata, dict)
        or metadata.get("namespace") != NAMESPACE
        or not isinstance(status, dict)
        or status.get("phase") != "Succeeded"
    ):
        raise SmokeError("receipt is not a succeeded LabCLIP smoke Workflow in argo")
    run_id = _workflow_parameter_value(workflow, "run-id")
    if not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise SmokeError("workflow receipt run-id is invalid")
    name = metadata.get("name")
    expected_name = f"labclip-runtime-smoke-{run_id[:16]}"
    if name != expected_name:
        raise SmokeError("workflow receipt name does not match its run-id")
    keep_s3_markers = _workflow_parameter_value(workflow, "keep-s3-markers")
    if keep_s3_markers not in {"true", "false"}:
        raise SmokeError("workflow receipt keep-s3-markers value is invalid")
    records = _validate_evidence(
        _extract_evidence(workflow),
        keep_s3_markers=keep_s3_markers == "true",
        run_id=run_id,
    )
    _print_success(
        name,
        records,
        keep_s3_markers=keep_s3_markers == "true",
        show_details=False,
    )
    return name


def _print_failure_logs(name: str) -> None:
    try:
        result = _run_argo(["logs", "-n", NAMESPACE, name])
    except SmokeError:
        return
    for line in result.stdout.splitlines():
        marker = "SMOKE_FAILURE "
        offset = line.find(marker)
        if offset < 0:
            continue
        try:
            failure = json.loads(line[offset + len(marker) :])
        except json.JSONDecodeError:
            continue
        print(
            "FAILURE_DETAIL "
            f"node={failure.get('node', 'unknown')} "
            f"stage={failure.get('stage', 'unknown')} "
            f"type={failure.get('error_type', 'unknown')}",
            file=sys.stderr,
        )


def run_smoke(
    *, keep_s3_markers: bool = False, timeout_seconds: int = 1800, poll_interval_seconds: int = 10
) -> str:
    if timeout_seconds < 1 or poll_interval_seconds < 1:
        raise ValueError("timeout and poll interval must be positive")
    run_id = uuid.uuid4().hex
    name = f"labclip-runtime-smoke-{run_id[:16]}"
    manifest = yaml.safe_dump(
        build_workflow(run_id, keep_s3_markers=keep_s3_markers), sort_keys=False
    )
    _run_argo(["submit", "-n", NAMESPACE, "-o", "name", "-"], stdin=manifest)
    workflow = _wait_for_terminal_workflow(
        name, timeout_seconds=timeout_seconds, poll_interval_seconds=poll_interval_seconds
    )
    phase = (workflow.get("status") or {}).get("phase")
    if phase != "Succeeded":
        _print_failure_logs(name)
        raise SmokeError(f"workflow {name} finished with phase {phase or 'unknown'}")

    records = _validate_evidence(
        _extract_evidence(workflow),
        keep_s3_markers=keep_s3_markers,
        run_id=run_id,
    )
    _print_success(name, records, keep_s3_markers=keep_s3_markers)
    return name


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep-s3-markers",
        action="store_true",
        help="retain only this run's S3 marker objects and print their keys",
    )
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument("--poll-interval-seconds", type=int, default=10)
    parser.add_argument(
        "--receipt",
        type=Path,
        help="validate a saved successful Argo Workflow JSON receipt without submitting",
    )
    args = parser.parse_args()
    try:
        if args.receipt is not None:
            validate_workflow_receipt(args.receipt)
            return 0
        run_smoke(
            keep_s3_markers=args.keep_s3_markers,
            timeout_seconds=args.timeout_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
        )
    except (SmokeError, ValueError) as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
