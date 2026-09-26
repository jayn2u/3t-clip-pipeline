from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.smoke_labclip_runtime import (
    IMAGE,
    NODES,
    RETAINED_DATA_BUCKETS,
    SERVICE_ACCOUNT,
    STORE_BUCKETS,
    SmokeError,
    _validate_evidence,
    build_workflow,
)
from scripts.smoke_labclip_worker import (
    exercise_cache_marker,
    exercise_s3_store,
    read_existing_object,
)


class SmokeWorkflowContractTests(unittest.TestCase):
    def test_workflow_targets_each_node_with_its_cache_and_gpu_runtime(self) -> None:
        workflow = build_workflow("1234567890abcdef1234567890abcdef", worker_source="pass")
        spec = workflow["spec"]
        templates = {template["name"]: template for template in spec["templates"]}
        dag_tasks = templates["both-nodes"]["dag"]["tasks"]
        smoke_template = templates["gpu-smoke"]

        self.assertEqual("argo", workflow["metadata"]["namespace"])
        self.assertEqual(SERVICE_ACCOUNT, spec["serviceAccountName"])
        self.assertEqual([node for node, _ in NODES], [task["name"] for task in dag_tasks])
        self.assertEqual(
            {"runtimeClassName": "nvidia"}, json.loads(spec["podSpecPatch"])
        )
        self.assertEqual([{"name": "ghcr-secret"}], spec["imagePullSecrets"])
        self.assertEqual(IMAGE, smoke_template["script"]["image"])

        for task, (node, claim) in zip(dag_tasks, NODES, strict=True):
            arguments = {
                parameter["name"]: parameter["value"]
                for parameter in task["arguments"]["parameters"]
            }
            self.assertEqual(node, arguments["expected-node"])
            self.assertEqual(claim, arguments["cache-claim"])

        self.assertEqual(
            {"kubernetes.io/hostname": "{{inputs.parameters.expected-node}}"},
            smoke_template["nodeSelector"],
        )
        self.assertNotIn("nodeName", smoke_template)
        self.assertEqual(
            "{{inputs.parameters.cache-claim}}",
            smoke_template["volumes"][0]["persistentVolumeClaim"]["claimName"],
        )
        resources = smoke_template["script"]["resources"]
        self.assertEqual("1", resources["requests"]["nvidia.com/gpu"])
        self.assertEqual("1", resources["limits"]["nvidia.com/gpu"])

    def test_workflow_uses_secret_key_references_and_emits_evidence(self) -> None:
        workflow = build_workflow(
            "1234567890abcdef1234567890abcdef",
            keep_s3_markers=True,
            worker_source="pass",
        )
        template = next(
            item for item in workflow["spec"]["templates"] if item["name"] == "gpu-smoke"
        )
        env = {item["name"]: item for item in template["script"]["env"]}

        for prefix, secret in (
            ("CODE", "minio-code-secret"),
            ("ML_ASSETS", "minio-ml-assets-secret"),
        ):
            for suffix, key in (
                ("ACCESS_KEY", "access-key"),
                ("SECRET_KEY", "secret-key"),
                ("ENDPOINT", "endpoint"),
                ("REGION", "region"),
            ):
                reference = env[f"{prefix}_{suffix}"]["valueFrom"]["secretKeyRef"]
                self.assertEqual({"name": secret, "key": key}, reference)

        self.assertEqual(
            "spec.nodeName",
            env["SMOKE_ACTUAL_NODE"]["valueFrom"]["fieldRef"]["fieldPath"],
        )
        self.assertEqual(
            "/tmp/smoke-evidence.json",
            template["outputs"]["parameters"][0]["valueFrom"]["path"],
        )
        arguments = workflow["spec"]["arguments"]["parameters"]
        self.assertEqual("true", arguments[1]["value"])


class WorkflowEvidenceValidationTests(unittest.TestCase):
    run_id = "1234567890abcdef1234567890abcdef"

    def valid_records(self) -> list[dict]:
        records = []
        for node, _ in NODES:
            s3_checks = []
            for store, buckets in STORE_BUCKETS.items():
                for bucket in buckets:
                    payload = (
                        f"labclip-runtime-smoke\n{self.run_id}\n{node}\n{store}\n{bucket}\n"
                    ).encode("utf-8")
                    digest = hashlib.sha256(payload).hexdigest()
                    retained_data = None
                    if bucket in RETAINED_DATA_BUCKETS:
                        retained_data = {
                            "bucket": bucket,
                            "status": "read",
                            "key": f"synthetic/{bucket}/example.bin",
                            "bytes_read": 23,
                            "sha256": "b" * 64,
                        }
                    s3_checks.append(
                        {
                            "store": store,
                            "bucket": bucket,
                            "key": f"infrastructure-smoke/{self.run_id}/{node}/{bucket}.txt",
                            "sha256": digest,
                            "metadata_sha256": digest,
                            "head_content_length": len(payload),
                            "head_metadata_verified": True,
                            "body_checksum_verified": True,
                            "marker_removed": False,
                            "retained_data": retained_data,
                        }
                    )
            records.append(
                {
                    "expected_node": node,
                    "node": node,
                    "gpu": "Synthetic GPU",
                    "pvc_marker": {"removed": True, "sha256": "c" * 64},
                    "s3": s3_checks,
                }
            )
        return records

    def test_validates_actual_nested_retained_data_shape(self) -> None:
        records = self.valid_records()

        validated = _validate_evidence(
            records, keep_s3_markers=True, run_id=self.run_id
        )

        self.assertEqual([node for node, _ in NODES], [record["node"] for record in validated])
        for record in validated:
            self.assertNotIn("retained_data", record)
            nested = [check["retained_data"] for check in record["s3"] if check["retained_data"]]
            self.assertEqual(set(RETAINED_DATA_BUCKETS), {item["bucket"] for item in nested})

    def test_rejects_missing_metadata_body_and_length_evidence(self) -> None:
        mutations = (
            {"head_metadata_verified": False},
            {"body_checksum_verified": False},
            {"metadata_sha256": "d" * 64},
            {"head_content_length": 1},
        )
        for mutation in mutations:
            records = self.valid_records()
            records[0]["s3"][0].update(mutation)
            with self.subTest(mutation=mutation):
                with self.assertRaises(SmokeError):
                    _validate_evidence(
                        records, keep_s3_markers=True, run_id=self.run_id
                    )


class CacheMarkerIsolationTests(unittest.TestCase):
    def test_marker_round_trip_removes_only_its_unique_file(self) -> None:
        run_id = "1234567890abcdef1234567890abcdef"
        with tempfile.TemporaryDirectory(prefix="labclip-cache-marker-") as temporary:
            root = Path(temporary)
            unrelated = root / "keep.bin"
            unrelated.write_bytes(b"pre-existing cache content")
            other_run_marker = root / ".labclip-runtime-smoke-other.marker"
            other_run_marker.write_bytes(b"leave this marker alone")

            evidence = exercise_cache_marker(root, run_id, "vis-lab")

            self.assertEqual(hashlib.sha256(
                f"labclip-runtime-smoke\n{run_id}\nvis-lab\n".encode()
            ).hexdigest(), evidence["sha256"])
            self.assertTrue(evidence["removed"])
            self.assertEqual(b"pre-existing cache content", unrelated.read_bytes())
            self.assertEqual(b"leave this marker alone", other_run_marker.read_bytes())
            self.assertFalse(
                (root / f".labclip-runtime-smoke-{run_id}-vis-lab.marker").exists()
            )

    def test_marker_collision_preserves_the_existing_file(self) -> None:
        run_id = "1234567890abcdef1234567890abcdef"
        with tempfile.TemporaryDirectory(prefix="labclip-cache-marker-") as temporary:
            root = Path(temporary)
            marker = root / f".labclip-runtime-smoke-{run_id}-ubuntu.marker"
            marker.write_bytes(b"existing content")

            with self.assertRaises(FileExistsError):
                exercise_cache_marker(root, run_id, "ubuntu")

            self.assertEqual(b"existing content", marker.read_bytes())


class ExistingS3DataReadTests(unittest.TestCase):
    def test_reads_a_bounded_prefix_from_first_non_smoke_object(self) -> None:
        payload = bytes(range(256)) * 32

        class ListingClient:
            def __init__(self) -> None:
                self.request = None

            def list_objects_v2(self, **_kwargs):
                return {
                    "Contents": [
                        {"Key": "infrastructure-smoke/prior/run.txt", "Size": 12},
                        {"Key": "models/checkpoint.bin", "Size": len(payload)},
                    ],
                    "IsTruncated": False,
                }

            def get_object(self, **kwargs):
                self.request = kwargs
                return {"Body": io.BytesIO(payload[:4096])}

        client = ListingClient()
        evidence = read_existing_object(client, "lab-data")

        self.assertEqual("read", evidence["status"])
        self.assertEqual("models/checkpoint.bin", evidence["key"])
        self.assertEqual(4096, evidence["bytes_read"])
        self.assertEqual(hashlib.sha256(payload[:4096]).hexdigest(), evidence["sha256"])
        self.assertEqual("bytes=0-4095", client.request["Range"])

    def test_empty_bucket_is_reported_without_attempting_a_read(self) -> None:
        class EmptyClient:
            def list_objects_v2(self, **_kwargs):
                return {"IsTruncated": False}

            def get_object(self, **_kwargs):
                raise AssertionError("empty bucket must not trigger a read")

        self.assertEqual(
            {"bucket": "lab-runs", "status": "skipped_empty"},
            read_existing_object(EmptyClient(), "lab-runs"),
        )


class S3MarkerContractTests(unittest.TestCase):
    def test_put_head_get_delete_uses_consumer_sha256_metadata_contract(self) -> None:
        class FakeClientError(Exception):
            def __init__(self, response):
                self.response = response

        class FakeConfig:
            def __init__(self, **kwargs):
                self.values = kwargs

        class FakeS3:
            def __init__(self):
                self.events = []
                self.body = b""
                self.metadata = {}
                self.deleted = False

            def put_object(self, **kwargs):
                self.events.append("put")
                self.body = kwargs["Body"]
                self.metadata = kwargs["Metadata"]

            def head_object(self, **_kwargs):
                self.events.append("head")
                if self.deleted:
                    raise FakeClientError(
                        {"ResponseMetadata": {"HTTPStatusCode": 404}}
                    )
                return {
                    "Metadata": self.metadata,
                    "ContentLength": len(self.body),
                }

            def get_object(self, **_kwargs):
                self.events.append("get")
                return {"Body": io.BytesIO(self.body)}

            def delete_object(self, **_kwargs):
                self.events.append("delete")
                self.deleted = True

            def list_objects_v2(self, **_kwargs):
                self.events.append("list")
                return {"IsTruncated": False}

        fake_s3 = FakeS3()
        fake_boto3 = types.ModuleType("boto3")
        fake_boto3.client = lambda *_args, **_kwargs: fake_s3
        fake_botocore = types.ModuleType("botocore")
        fake_botocore.__path__ = []
        fake_config_module = types.ModuleType("botocore.config")
        fake_config_module.Config = FakeConfig
        fake_exceptions_module = types.ModuleType("botocore.exceptions")
        fake_exceptions_module.ClientError = FakeClientError
        modules = {
            "boto3": fake_boto3,
            "botocore": fake_botocore,
            "botocore.config": fake_config_module,
            "botocore.exceptions": fake_exceptions_module,
        }
        credentials = {
            "CODE_ACCESS_KEY": "not-printed-access-key",
            "CODE_SECRET_KEY": "not-printed-secret-key",
            "CODE_ENDPOINT": "http://minio-code.argo.svc.cluster.local:9000",
            "CODE_REGION": "garage",
        }

        with patch.dict(sys.modules, modules), patch.dict(os.environ, credentials):
            evidence = exercise_s3_store(
                store="code",
                bucket="lab-code",
                env_prefix="CODE",
                run_id="1234567890abcdef1234567890abcdef",
                node="vis-lab",
                keep=False,
            )

        expected_digest = hashlib.sha256(fake_s3.body).hexdigest()
        self.assertEqual({"sha256": expected_digest}, fake_s3.metadata)
        self.assertEqual(
            ["put", "head", "get", "delete", "head", "list"], fake_s3.events
        )
        self.assertTrue(evidence["head_metadata_verified"])
        self.assertTrue(evidence["body_checksum_verified"])
        self.assertEqual(expected_digest, evidence["metadata_sha256"])
        self.assertEqual(len(fake_s3.body), evidence["head_content_length"])
        self.assertTrue(evidence["marker_removed"])


if __name__ == "__main__":
    unittest.main()
