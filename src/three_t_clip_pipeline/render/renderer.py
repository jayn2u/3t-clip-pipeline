"""Deterministic mapping from the frozen workload contract to Argo Workflow YAML."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Final, override

import yaml

from three_t_clip_pipeline.contract.models import (
    ConfigMapValueFrom,
    EnvironmentVariable,
    FieldValueFrom,
    SecretValueFrom,
)
from three_t_clip_pipeline.contract.serialization import canonical_json_bytes
from three_t_clip_pipeline.render.constants import (
    ARTIFACT_PUBLISHER_NAME,
    BUNDLE_INIT_NAME,
    CACHE_MOUNT,
    COMMIT_RESULTS_NAME,
    CONTROL_MOUNT,
    INPUT_MOUNT,
    MAIN_NAME,
    NAMESPACE,
    OUTPUT_MOUNT,
    PLATFORM_IMAGE,
    SERVICE_ACCOUNT,
)

if TYPE_CHECKING:
    from pydantic import JsonValue

    from three_t_clip_pipeline.contract.models import Workload

_WORKFLOW_API_VERSION: Final = "argoproj.io/v1alpha1"
_WORKFLOW_KIND: Final = "Workflow"


class _NoAliasSafeDumper(yaml.SafeDumper):
    @override
    def ignore_aliases(self, data: JsonValue) -> bool:
        _ = data
        return True


def _environment(variable: EnvironmentVariable) -> dict[str, JsonValue]:
    match variable.value_from:
        case SecretValueFrom(secret_key_ref=reference):
            value_from: dict[str, JsonValue] = {
                "secretKeyRef": {"key": reference.key, "name": reference.name}
            }
        case ConfigMapValueFrom(config_map_key_ref=reference):
            value_from = {"configMapKeyRef": {"key": reference.key, "name": reference.name}}
        case FieldValueFrom(field_ref=reference):
            value_from = {"fieldRef": {"fieldPath": reference.field_path}}
    return {"name": variable.name, "valueFrom": value_from}


def _platform_template(name: str, phase: str) -> dict[str, JsonValue]:
    return {
        "container": {
            "args": ["runtime", "init", "--phase", phase],
            "command": ["3t-pipeline"],
            "image": PLATFORM_IMAGE,
            "name": name,
        },
        "name": name,
        "retryStrategy": {
            "backoff": {"duration": "5s", "factor": 2, "maxDuration": "60s"},
            "limit": "2",
            "retryPolicy": "OnError",
        },
    }


def _workload_template(workload: Workload, run_uid: str) -> dict[str, JsonValue]:
    spec = workload.spec
    output_environment: list[JsonValue] = [
        {"name": "RUN_UID", "value": run_uid},
        {"name": "OUTPUT_BUCKET", "value": spec.outputs.bucket},
        {"name": "OUTPUT_PREFIX", "value": spec.outputs.prefix},
    ]
    init_environment: list[JsonValue] = [
        *output_environment,
        {"name": "BUNDLE_S3_URI", "value": spec.bundle.s3_uri},
        {"name": "BUNDLE_SHA256", "value": spec.bundle.sha256},
        {"name": "CACHE_PROFILE", "value": spec.resources.cache_profile},
        {
            "name": "CACHE_MAPPINGS_JSON",
            "value": json.dumps(
                [item.model_dump(by_alias=True, mode="json") for item in spec.cache_mappings],
                separators=(",", ":"),
                sort_keys=True,
            ),
        },
    ]
    publisher_environment: list[JsonValue] = [
        *output_environment,
        {
            "name": "REQUIRED_PATHS_JSON",
            "value": json.dumps(sorted(spec.outputs.required_paths), separators=(",", ":")),
        },
        {
            "name": "PUBLICATION_FINAL_RETRY_SECONDS",
            "value": str(spec.timeouts.publication_final_retry_seconds),
        },
        {
            "name": "TERMINATION_GRACE_SECONDS",
            "value": str(spec.timeouts.termination_grace_seconds),
        },
    ]
    return {
        "container": {
            "command": list(spec.execution.command),
            "env": [
                _environment(item) for item in sorted(spec.environment, key=lambda item: item.name)
            ],
            "image": spec.execution.image,
            "name": MAIN_NAME,
            "resources": {
                "requests": {
                    "cpu": spec.resources.cpu,
                    "memory": spec.resources.memory,
                    spec.resources.gpu_resource: str(spec.resources.gpu_count),
                }
            },
            "volumeMounts": [
                {"mountPath": CACHE_MOUNT, "name": "cache", "readOnly": True},
                {"mountPath": INPUT_MOUNT, "name": "input", "readOnly": True},
                {"mountPath": OUTPUT_MOUNT, "name": "output"},
            ],
            "workingDir": f"{INPUT_MOUNT}/{spec.execution.working_directory}",
        },
        "initContainers": [
            {
                "args": ["runtime", "init"],
                "command": ["3t-pipeline"],
                "env": init_environment,
                "image": PLATFORM_IMAGE,
                "name": BUNDLE_INIT_NAME,
                "volumeMounts": [
                    {"mountPath": CACHE_MOUNT, "name": "cache"},
                    {"mountPath": CONTROL_MOUNT, "name": "control"},
                    {"mountPath": INPUT_MOUNT, "name": "input"},
                ],
            },
            {
                "args": ["runtime", "publisher"],
                "command": ["3t-pipeline"],
                "env": publisher_environment,
                "image": PLATFORM_IMAGE,
                "name": ARTIFACT_PUBLISHER_NAME,
                "restartPolicy": "Always",
                "volumeMounts": [
                    {"mountPath": CONTROL_MOUNT, "name": "control"},
                    {"mountPath": OUTPUT_MOUNT, "name": "output", "readOnly": True},
                ],
            },
        ],
        "name": "workload",
        "podSpecPatch": yaml.safe_dump(
            {"terminationGracePeriodSeconds": spec.timeouts.termination_grace_seconds},
            sort_keys=True,
        ),
        "retryStrategy": {
            "backoff": {"duration": "10s", "factor": 2, "maxDuration": "120s"},
            "limit": "1",
            "retryPolicy": "OnError",
        },
    }


def _finalizer_template(workload: Workload, run_uid: str) -> dict[str, JsonValue]:
    return {
        "container": {
            "args": ["runtime", "finalize"],
            "command": ["3t-pipeline"],
            "env": [
                {"name": "WORKFLOW_NAME", "value": "{{workflow.name}}"},
                {"name": "WORKFLOW_UID", "value": "{{workflow.uid}}"},
                {"name": "WORKFLOW_STATUS", "value": "{{workflow.status}}"},
                {"name": "RUN_UID", "value": run_uid},
                {"name": "OUTPUT_BUCKET", "value": workload.spec.outputs.bucket},
                {"name": "OUTPUT_PREFIX", "value": workload.spec.outputs.prefix},
                {
                    "name": "REQUIRED_PATHS_JSON",
                    "value": json.dumps(
                        sorted(workload.spec.outputs.required_paths), separators=(",", ":")
                    ),
                },
            ],
            "image": PLATFORM_IMAGE,
            "name": COMMIT_RESULTS_NAME,
        },
        "name": COMMIT_RESULTS_NAME,
        "retryStrategy": {
            "backoff": {"duration": "10s", "factor": 2, "maxDuration": "120s"},
            "limit": "2",
            "retryPolicy": "OnError",
        },
    }


def render_workflow_bytes(workload: Workload) -> bytes:
    """Render one validated workload into deterministic Argo Workflow YAML."""
    digest = hashlib.sha256(canonical_json_bytes(workload)).hexdigest()
    task_names = ("preflight", "acquire-profile", "prepare-cache")
    tasks: list[JsonValue] = []
    for index, name in enumerate(task_names):
        task: dict[str, JsonValue] = {"name": name, "template": name}
        if index > 0:
            task["dependencies"] = [task_names[index - 1]]
        tasks.append(task)
    tasks.append({"dependencies": [task_names[-1]], "name": "run", "template": "workload"})
    manifest: dict[str, JsonValue] = {
        "apiVersion": _WORKFLOW_API_VERSION,
        "kind": _WORKFLOW_KIND,
        "metadata": {
            "labels": {
                "app.kubernetes.io/part-of": "three-t-pipeline",
                "three-t.dev/run-uid": digest,
            },
            "name": f"{workload.metadata.name}-{digest[:12]}",
            "namespace": NAMESPACE,
        },
        "spec": {
            "activeDeadlineSeconds": workload.spec.timeouts.active_deadline_seconds,
            "entrypoint": "pipeline",
            "onExit": COMMIT_RESULTS_NAME,
            "podGC": {"strategy": "OnWorkflowCompletion"},
            "serviceAccountName": SERVICE_ACCOUNT,
            "templates": [
                {"dag": {"tasks": tasks}, "name": "pipeline"},
                *(_platform_template(name, name) for name in task_names),
                _workload_template(workload, digest),
                _finalizer_template(workload, digest),
            ],
            "ttlStrategy": {"secondsAfterCompletion": 86400},
            "volumeClaimGC": {"strategy": "OnWorkflowCompletion"},
            "volumes": [
                {"emptyDir": {}, "name": "control"},
                {"name": "cache", "persistentVolumeClaim": {"claimName": "three-t-cache"}},
                {"emptyDir": {}, "name": "input"},
                {"emptyDir": {}, "name": "output"},
            ],
        },
    }
    rendered = yaml.dump(
        manifest,
        Dumper=_NoAliasSafeDumper,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=True,
    )
    return rendered.encode()
