from __future__ import annotations

import json
import io
import os
import unittest
from unittest.mock import patch

from scripts.smoke_cleanup_markers import (
    CleanupError,
    STORE_SECRETS,
    _port_forward,
    _s3_client,
    validate_receipt,
)


RUN_ID = "1234567890abcdef1234567890abcdef"
NODES = ("vis-lab", "ubuntu")


def successful_receipt() -> dict:
    arguments = [
        {"name": "run-id", "value": RUN_ID},
        {"name": "keep-s3-markers", "value": "true"},
    ]
    node_outputs = {}
    for node in NODES:
        checks = []
        for store, (_, _, _, buckets) in STORE_SECRETS.items():
            for bucket in buckets:
                checks.append(
                    {
                        "store": store,
                        "bucket": bucket,
                        "key": f"infrastructure-smoke/{RUN_ID}/{node}/{bucket}.txt",
                        "sha256": "a" * 64,
                        "marker_removed": False,
                    }
                )
        record = json.dumps(
            {
                "expected_node": node,
                "node": node,
                "s3": checks,
            }
        )
        node_outputs[node] = {
            "outputs": {
                "parameters": [{"name": "smoke-evidence", "value": record}]
            }
        }
    return {
        "kind": "Workflow",
        "metadata": {"namespace": "argo"},
        "spec": {"arguments": {"parameters": arguments}},
        "status": {"phase": "Succeeded", "nodes": node_outputs},
    }


class CleanupReceiptTests(unittest.TestCase):
    def test_extracts_only_the_eight_generated_marker_keys(self) -> None:
        markers = validate_receipt(successful_receipt())

        self.assertEqual(8, len(markers))
        self.assertEqual(8, len({marker.key for marker in markers}))
        self.assertTrue(
            all(
                marker.key
                == f"infrastructure-smoke/{RUN_ID}/{marker.node}/{marker.bucket}.txt"
                for marker in markers
            )
        )

    def test_rejects_paths_outside_the_exact_run_node_bucket_key(self) -> None:
        receipt = successful_receipt()
        parameter = receipt["status"]["nodes"]["vis-lab"]["outputs"]["parameters"][0]
        evidence = json.loads(parameter["value"])
        evidence["s3"][0]["key"] = f"infrastructure-smoke/{RUN_ID}/../secrets.txt"
        parameter["value"] = json.dumps(evidence)

        with self.assertRaisesRegex(CleanupError, "outside the generated marker path"):
            validate_receipt(receipt)

    def test_rejects_unknown_bucket_wrong_node_and_nonretained_markers(self) -> None:
        cases = []
        for mutate in (
            lambda check: check.update(bucket="other-bucket"),
            lambda check: check.update(key=f"infrastructure-smoke/{RUN_ID}/ubuntu/lab-code.txt"),
            lambda check: check.update(marker_removed=True),
        ):
            receipt = successful_receipt()
            parameter = receipt["status"]["nodes"]["vis-lab"]["outputs"]["parameters"][0]
            evidence = json.loads(parameter["value"])
            mutate(evidence["s3"][0])
            parameter["value"] = json.dumps(evidence)
            cases.append(receipt)

        for receipt in cases:
            with self.subTest(receipt=receipt):
                with self.assertRaises(CleanupError):
                    validate_receipt(receipt)

    def test_rejects_failed_workflow_or_receipt_that_did_not_keep_markers(self) -> None:
        receipt = successful_receipt()
        receipt["status"]["phase"] = "Failed"
        with self.assertRaisesRegex(CleanupError, "did not reach Succeeded"):
            validate_receipt(receipt)

        receipt = successful_receipt()
        receipt["spec"]["arguments"]["parameters"][1]["value"] = "false"
        with self.assertRaisesRegex(CleanupError, "does not confirm"):
            validate_receipt(receipt)


class PortForwardLifecycleTests(unittest.TestCase):
    class FakeProcess:
        def __init__(self, stdout):
            self.stdout = stdout
            self.terminated = False
            self.waited = False

        def poll(self):
            return 0 if self.terminated else None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            self.waited = True
            return 0

        def kill(self):
            self.terminated = True

    def test_forward_is_loopback_only_and_stops_after_success(self) -> None:
        read_fd, write_fd = os.pipe()
        os.write(write_fd, b"Forwarding from 127.0.0.1:49123 -> 9000\n")
        os.close(write_fd)
        process = self.FakeProcess(os.fdopen(read_fd, "r"))

        with (
            patch(
                "scripts.smoke_cleanup_markers.subprocess.Popen", return_value=process
            ) as popen,
        ):
            with _port_forward("minio-code") as endpoint:
                self.assertEqual("http://127.0.0.1:49123", endpoint)

        command = popen.call_args.args[0]
        self.assertIn("--address=127.0.0.1", command)
        self.assertIn("service/minio-code", command)
        self.assertIn(":9000", command)
        self.assertTrue(process.terminated)
        self.assertTrue(process.waited)
        self.assertTrue(process.stdout.closed)

    def test_forward_process_is_stopped_if_readiness_fails(self) -> None:
        process = self.FakeProcess(io.StringIO())
        with (
            patch("scripts.smoke_cleanup_markers.subprocess.Popen", return_value=process),
            patch(
                "scripts.smoke_cleanup_markers._wait_for_port_forward",
                side_effect=CleanupError("not ready"),
            ),
        ):
            with self.assertRaisesRegex(CleanupError, "not ready"):
                with _port_forward("minio-ml-assets"):
                    self.fail("port-forward should not be yielded when readiness fails")

        self.assertTrue(process.terminated)
        self.assertTrue(process.waited)
        self.assertTrue(process.stdout.closed)

    def test_rejects_services_outside_the_known_minio_pair(self) -> None:
        with self.assertRaisesRegex(CleanupError, "unsupported MinIO Service"):
            with _port_forward("other-service"):
                self.fail("unexpected Service accepted")

    def test_s3_client_rejects_cluster_dns_endpoints(self) -> None:
        with self.assertRaisesRegex(CleanupError, "loopback port-forward"):
            _s3_client({}, "http://minio-code.argo.svc.cluster.local:9000")


if __name__ == "__main__":
    unittest.main()
