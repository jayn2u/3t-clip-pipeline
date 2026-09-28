import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = REPOSITORY_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))

from prepare_kubeflow_overlay import approve_render


def load_apply_tools():
    script = SCRIPTS_ROOT / "apply_kubeflow.py"
    if not script.is_file():
        return None
    spec = importlib.util.spec_from_file_location("apply_kubeflow", script)
    if spec is None or spec.loader is None:
        raise AssertionError("Kubeflow apply module cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_check_tools():
    script = SCRIPTS_ROOT / "check_kubeflow.py"
    if not script.is_file():
        return None
    spec = importlib.util.spec_from_file_location("check_kubeflow", script)
    if spec is None or spec.loader is None:
        raise AssertionError("Kubeflow drift module cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def make_render(root: Path, extra_documents=()) -> tuple[Path, Path, Path, Path, str]:
    manifest_path = root / "rendered.yaml"
    receipt_path = root / "receipt.json"
    inventory_path = root / "inventory.json"
    approval_path = root / "approval.json"
    email = "test@lab.example"
    documents = [
        {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": "istio-ingressgateway", "namespace": "istio-system"},
            "spec": {"type": "ClusterIP"},
        },
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": "dex", "namespace": "auth"},
            "data": {
                "config.yaml": yaml.safe_dump(
                    {
                        "staticPasswords": [
                            {"email": email, "username": "test", "hashFromEnv": "DEX_USER_PASSWORD"}
                        ]
                    }
                )
            },
        },
        {
            "apiVersion": "kubeflow.org/v1beta1",
            "kind": "Profile",
            "metadata": {"name": "kubeflow-user-test-lab-example"},
            "spec": {"owner": {"name": email}},
        },
        {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": "dex-passwords", "namespace": "auth"},
            "stringData": {"DEX_USER_PASSWORD": "$2b$12$" + "a" * 53},
        },
        {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "Ingress",
            "metadata": {"name": "kubeflow-tailnet", "namespace": "istio-system"},
            "spec": {
                "ingressClassName": "tailscale",
                "tls": [{"hosts": ["labclip-kubeflow"]}],
                "rules": [
                    {
                        "host": "labclip-kubeflow",
                        "http": {
                            "paths": [
                                {
                                    "path": "/",
                                    "pathType": "Prefix",
                                    "backend": {
                                        "service": {
                                            "name": "istio-ingressgateway",
                                            "port": {"number": 80},
                                        }
                                    },
                                }
                            ]
                        },
                    }
                ],
            },
        },
    ]
    documents.extend(extra_documents)
    manifest_text = "---\n".join(yaml.safe_dump(item, sort_keys=False) for item in documents)
    manifest_path.write_text(manifest_text, encoding="utf-8")
    manifest_path.chmod(0o600)
    inventory = sorted(
        (
            item["apiVersion"],
            item["kind"],
            item.get("metadata", {}).get("namespace", ""),
            item["metadata"]["name"],
        )
        for item in documents
    )
    inventory_text = json.dumps([list(row) for row in inventory], indent=2) + "\n"
    inventory_path.write_text(inventory_text, encoding="utf-8")
    inventory_path.chmod(0o600)
    digest = hashlib.sha256(manifest_text.encode()).hexdigest()
    receipt = {
        "schema_version": 1,
        "source_ref": "github.com/kubeflow/community-distribution/example?ref=f09f3eeaa25cc852665f460497a42b7fc68639ac",
        "source_tag": "26.03.1",
        "source_commit": "f09f3eeaa25cc852665f460497a42b7fc68639ac",
        "sha256": digest,
        "inventory_sha256": hashlib.sha256(inventory_text.encode()).hexdigest(),
        "object_count": len(inventory),
        "manifest": manifest_path.name,
    }
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    receipt_path.chmod(0o600)
    return manifest_path, receipt_path, inventory_path, approval_path, digest


class KubeflowApplyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tools = load_apply_tools()
        self.temp = tempfile.TemporaryDirectory(prefix="kubeflow-apply-tests-")
        self.root = Path(self.temp.name)
        self.manifest, self.receipt, self.inventory, self.approval, self.digest = make_render(
            self.root
        )
        approve_render(
            self.digest,
            self.manifest,
            self.receipt,
            self.inventory,
            self.approval,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def require_implementation(self):
        self.assertIsNotNone(self.tools, "Kubeflow apply tooling is not implemented")

    def test_apply_rejects_digest_mismatch(self) -> None:
        self.require_implementation()
        self.manifest.write_text(self.manifest.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with mock.patch.object(self.tools.subprocess, "run") as run:
            with self.assertRaises(ValueError):
                self.tools.apply_distribution(self.manifest, self.receipt)
        run.assert_not_called()

    def test_apply_rejects_a_self_generated_receipt_without_approval(self) -> None:
        self.require_implementation()
        self.approval.unlink()
        with mock.patch.object(self.tools.subprocess, "run") as run:
            with self.assertRaises(ValueError):
                self.tools.apply_distribution(self.manifest, self.receipt)
        run.assert_not_called()

    def test_apply_retries_missing_crd_only(self) -> None:
        self.require_implementation()
        results = [
            mock.Mock(returncode=1, stdout="", stderr='no matches for kind "Profile" in version "kubeflow.org/v1beta1"'),
            mock.Mock(returncode=0, stdout="customresourcedefinition.apiextensions.k8s.io/profiles.kubeflow.org condition met", stderr=""),
            mock.Mock(returncode=0, stdout="applied", stderr=""),
        ]
        with mock.patch.object(self.tools.subprocess, "run", side_effect=results) as run:
            self.tools.apply_distribution(self.manifest, self.receipt)
        commands = [call.args[0] for call in run.call_args_list]
        apply_commands = [command for command in commands if command[1] == "apply"]
        wait_commands = [command for command in commands if command[1] == "wait"]
        self.assertEqual(2, len(apply_commands))
        self.assertEqual(1, len(wait_commands))
        self.assertNotIn("--force-conflicts", [argument for command in commands for argument in command])

    def test_apply_stops_on_field_conflict(self) -> None:
        self.require_implementation()
        result = mock.Mock(
            returncode=1,
            stdout="",
            stderr="Apply failed with conflict: field manager owns spec.selector",
        )
        with mock.patch.object(self.tools.subprocess, "run", return_value=result) as run:
            with self.assertRaises(self.tools.KubeflowApplyError):
                self.tools.apply_distribution(self.manifest, self.receipt)
        self.assertEqual(1, run.call_count)
        command = run.call_args.args[0]
        self.assertEqual(["kubectl", "apply", "-f"], command[:3])
        self.assertNotIn("--force-conflicts", command)

    def test_apply_never_exceeds_six_attempts(self) -> None:
        self.require_implementation()
        missing_crd = mock.Mock(
            returncode=1,
            stdout="",
            stderr='no matches for kind "Profile" in version "kubeflow.org/v1beta1"',
        )
        established = mock.Mock(returncode=0, stdout="Established", stderr="")
        responses = []
        for attempt in range(6):
            responses.append(missing_crd)
            if attempt < 5:
                responses.append(established)
        with mock.patch.object(self.tools.subprocess, "run", side_effect=responses) as run:
            with self.assertRaises(self.tools.KubeflowApplyError):
                self.tools.apply_distribution(self.manifest, self.receipt, max_attempts=6)
        apply_count = sum(call.args[0][1] == "apply" for call in run.call_args_list)
        self.assertEqual(6, apply_count)
        with mock.patch.object(self.tools.subprocess, "run") as run:
            with self.assertRaises(ValueError):
                self.tools.apply_distribution(self.manifest, self.receipt, max_attempts=7)
        run.assert_not_called()

    def test_apply_rejects_terraform_owned_objects(self) -> None:
        self.require_implementation()
        self.manifest.write_text(
            self.manifest.read_text(encoding="utf-8")
            + "---\napiVersion: v1\nkind: Namespace\nmetadata:\n  name: argo\n",
            encoding="utf-8",
        )
        with mock.patch.object(self.tools.subprocess, "run") as run:
            with self.assertRaises(self.tools.KubeflowApplyError):
                self.tools.apply_distribution(self.manifest, self.receipt)
        run.assert_not_called()

    def test_apply_establishes_crds_before_the_distribution(self) -> None:
        self.require_implementation()
        crd = {
            "apiVersion": "apiextensions.k8s.io/v1",
            "kind": "CustomResourceDefinition",
            "metadata": {"name": "profiles.kubeflow.org"},
            "spec": {},
        }
        manifest, receipt, inventory, approval, digest = make_render(
            self.root,
            extra_documents=(crd,),
        )
        approve_render(digest, manifest, receipt, inventory, approval)
        responses = [
            mock.Mock(returncode=0, stdout="crd applied", stderr=""),
            mock.Mock(returncode=0, stdout="crd established", stderr=""),
            mock.Mock(returncode=0, stdout="distribution applied", stderr=""),
        ]
        with mock.patch.object(self.tools.subprocess, "run", side_effect=responses) as run:
            self.tools.apply_distribution(manifest, receipt)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual("apply", commands[0][1])
        self.assertNotEqual(str(manifest), commands[0][-1])
        self.assertEqual("wait", commands[1][1])
        self.assertEqual(str(manifest), commands[2][-1])


class KubeflowDriftTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tools = load_check_tools()
        self.temp = tempfile.TemporaryDirectory(prefix="kubeflow-drift-tests-")
        self.root = Path(self.temp.name)
        self.manifest, self.receipt, self.inventory, self.approval, self.digest = make_render(
            self.root
        )
        approve_render(
            self.digest,
            self.manifest,
            self.receipt,
            self.inventory,
            self.approval,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_drift_check_is_read_only(self) -> None:
        self.assertIsNotNone(self.tools, "Kubeflow drift tooling is not implemented")
        output = "diff -u -N live/deployment.yaml desired/deployment.yaml\n- secret-data-sentinel"
        result = mock.Mock(returncode=1, stdout=output, stderr="")
        with mock.patch.object(self.tools.subprocess, "run", return_value=result) as run:
            report = self.tools.check_distribution(self.manifest, self.receipt)
        command = run.call_args.args[0]
        self.assertEqual(["kubectl", "diff", "-f"], command[:3])
        self.assertTrue(report.has_drift)
        self.assertEqual(1, report.changed_objects)
        self.assertNotIn("secret-data-sentinel", report.summary)
        self.assertNotIn("apply", command)

    def test_drift_check_rejects_an_unapproved_render(self) -> None:
        self.assertIsNotNone(self.tools, "Kubeflow drift tooling is not implemented")
        self.approval.unlink()
        with mock.patch.object(self.tools.subprocess, "run") as run:
            with self.assertRaises(ValueError):
                self.tools.check_distribution(self.manifest, self.receipt)
        run.assert_not_called()

    def test_readiness_reports_unbound_claim_and_unavailable_deployment(self) -> None:
        deployment = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "dashboard-test", "namespace": "kubeflow"},
            "spec": {"replicas": 2},
        }
        claim = {
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": {"name": "cache-test", "namespace": "kubeflow"},
            "spec": {},
        }
        manifest, receipt, inventory, approval, digest = make_render(
            self.root,
            extra_documents=(deployment, claim),
        )
        approve_render(digest, manifest, receipt, inventory, approval)
        live = {
            "items": [
                {
                    "kind": "Deployment",
                    "metadata": {"name": "dashboard-test", "namespace": "kubeflow"},
                    "spec": {"replicas": 2},
                    "status": {"availableReplicas": 1},
                },
                {
                    "kind": "PersistentVolumeClaim",
                    "metadata": {"name": "cache-test", "namespace": "kubeflow"},
                    "status": {"phase": "Pending"},
                },
            ]
        }
        result = mock.Mock(returncode=0, stdout=json.dumps(live), stderr="")
        with mock.patch.object(self.tools.subprocess, "run", return_value=result) as run:
            report = self.tools.check_readiness(manifest, receipt)
        self.assertFalse(report.ready)
        self.assertEqual(2, report.checked_objects)
        self.assertEqual(
            ("Deployment/kubeflow/dashboard-test", "PersistentVolumeClaim/kubeflow/cache-test"),
            report.pending_objects,
        )
        self.assertEqual("get", run.call_args.args[0][1])
