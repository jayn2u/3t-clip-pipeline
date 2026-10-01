import base64
import hashlib
import json
import subprocess
import tempfile
import unittest
import unittest.mock as mock
from contextlib import nullcontext
from pathlib import Path

from scripts.minio_bootstrap import (
    MC_RELEASE,
    MC_SHA256,
    _validate_researcher_secret,
    bootstrap_store,
    build_bootstrap_plan,
    build_policy,
    ensure_mc,
    read_kubernetes_secret,
    redact,
)


class MinioBootstrapTest(unittest.TestCase):
    def test_mc_is_pinned(self) -> None:
        self.assertEqual(MC_RELEASE, "RELEASE.2025-08-13T08-35-41Z")
        self.assertEqual(
            MC_SHA256,
            "01f866e9c5f9b87c2b09116fa5d7c06695b106242d829a8bb32990c00312e891",
        )

    def test_store_bucket_plan(self) -> None:
        code = build_bootstrap_plan("code")
        assets = build_bootstrap_plan("ml-assets")
        self.assertEqual(code.buckets, ("lab-code",))
        self.assertEqual(code.local_port, 19010)
        self.assertEqual(code.researcher_secret_name, "minio-code-researcher-secret")
        self.assertEqual(
            assets.buckets,
            ("lab-data", "lab-runs", "argo-artifacts"),
        )
        self.assertEqual(assets.local_port, 19000)
        self.assertEqual(
            assets.researcher_secret_name,
            "minio-ml-assets-researcher-secret",
        )

    def test_policy_limits_resources_to_selected_buckets(self) -> None:
        policy = build_policy(("lab-code",))
        statement = policy["Statement"][0]
        self.assertEqual(
            set(statement["Resource"]),
            {"arn:aws:s3:::lab-code", "arn:aws:s3:::lab-code/*"},
        )
        self.assertEqual(
            set(statement["Action"]),
            {
                "s3:GetBucketLocation",
                "s3:ListBucket",
                "s3:GetObject",
                "s3:PutObject",
                "s3:DeleteObject",
            },
        )

    def test_redact_removes_all_secret_values(self) -> None:
        text = "root-password and pipeline-secret"
        self.assertEqual(
            redact(text, ("root-password", "pipeline-secret")),
            "[REDACTED] and [REDACTED]",
        )

    def test_researcher_credentials_follow_minio_native_length_range(self) -> None:
        plan = build_bootstrap_plan("code")
        root_secret = {
            "root-user": "root-user-value-1234",
            "root-password": "root-password-value-1234",
            "access-key": "pipeline-user-value-1234",
            "secret-key": "pipeline-secret-value-1234",
        }
        for value in ("1234567", "x" * 41):
            with self.subTest(length=len(value)), self.assertRaisesRegex(
                RuntimeError,
                "between 8 and 40",
            ):
                _validate_researcher_secret(
                    {"access-key": value, "secret-key": value},
                    root_secret,
                    plan,
                )

    def test_bootstrap_adds_pipeline_and_researcher_users(self) -> None:
        root_secret = {
            "root-user": "root-user-value-1234",
            "root-password": "root-password-value-1234",
            "access-key": "pipeline-user-value-1234",
            "secret-key": "pipeline-secret-value-1234",
        }
        researcher_secret = {
            "access-key": "runtime-user",
            "secret-key": "runtime-user",
        }
        with mock.patch(
            "scripts.minio_bootstrap.read_kubernetes_secret",
            side_effect=(root_secret, researcher_secret),
        ), mock.patch(
            "scripts.minio_bootstrap.ensure_mc",
            return_value=Path("/opt/mc"),
        ), mock.patch(
            "scripts.minio_bootstrap.bootstrap_endpoint",
            return_value=nullcontext("http://127.0.0.1:19010"),
        ), mock.patch(
            "scripts.minio_bootstrap._run_mc",
        ) as run_mc:
            bootstrap_store("code", mc_path=Path("/opt/mc"))
        commands = [call.args[0] for call in run_mc.call_args_list]
        self.assertIn(
            [
                "/opt/mc",
                "admin",
                "user",
                "add",
                "labclip-code",
                researcher_secret["access-key"],
                researcher_secret["secret-key"],
            ],
            commands,
        )
        self.assertIn(
            [
                "/opt/mc",
                "admin",
                "policy",
                "attach",
                "labclip-code",
                "labclip-code-rw",
                "--user",
                researcher_secret["access-key"],
            ],
            commands,
        )

    def test_ensure_mc_accepts_only_expected_checksum_and_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mc"
            data = b"pinned-mc"
            path.write_bytes(data)
            expected = hashlib.sha256(data).hexdigest()
            version = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=f"mc version {MC_RELEASE}\n",
                stderr="",
            )
            with mock.patch(
                "scripts.minio_bootstrap.MC_SHA256",
                expected,
            ), mock.patch("subprocess.run", return_value=version):
                self.assertEqual(ensure_mc(path), path)
            with mock.patch(
                "scripts.minio_bootstrap.MC_SHA256",
                "0" * 64,
            ), self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
                ensure_mc(path)

    def test_read_kubernetes_secret_decodes_values_without_a_labclip_checkout(self) -> None:
        payload = {
            "data": {
                "access-key": base64.b64encode(b"pipeline-user").decode("ascii"),
                "secret-key": base64.b64encode(b"pipeline-secret").decode("ascii"),
            }
        }
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(payload), stderr=""
        )
        with mock.patch("scripts.minio_bootstrap.subprocess.run", return_value=completed) as run:
            secret = read_kubernetes_secret("argo", "minio-code-secret")
        self.assertEqual(
            {"access-key": "pipeline-user", "secret-key": "pipeline-secret"}, secret
        )
        self.assertEqual(
            ["kubectl", "-n", "argo", "get", "secret", "minio-code-secret", "-o", "json"],
            run.call_args.args[0],
        )

    def test_read_kubernetes_secret_rejects_invalid_encoding(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps({"data": {"k": "%%%"}}), stderr=""
        )
        with mock.patch("scripts.minio_bootstrap.subprocess.run", return_value=completed):
            self.assertEqual({}, read_kubernetes_secret("argo", "minio-code-secret"))


if __name__ == "__main__":
    unittest.main()
